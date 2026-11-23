"""Annotate pairs of ontology resource kinds with possible joint risks."""

import json
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from src.runtime import llm
from table_output import print_table

ROOT = Path(__file__).resolve().parent

# The instructions are in llm.toml (pair_risks_kinds_system; materialize: pair_risks_session_system).
SCHEMA = {
    "type": "object",
    "properties": {
        "joint_risk": {"type": "boolean"},
        "consequence": {"type": "string"},
        "why_both": {"type": "string"},
        "assumptions": {"type": "string"},
    },
    "required": ["joint_risk", "consequence", "why_both", "assumptions"],
    "additionalProperties": False,
}


def render_assessment(assessment: dict) -> list[tuple[str, str]]:
    return [
        ("Joint risk", "yes" if assessment["joint_risk"] else "no"),
        ("Consequence", assessment["consequence"] or "(none)"),
        ("Why both", assessment["why_both"] or "(none)"),
        ("Assumptions", assessment["assumptions"] or "(none)"),
    ]


def describe_resource(resource: dict) -> list[str]:
    return [", ".join(resource["effects"])]


def analyze_pairs(
    resources: list[dict],
    model: str | None = None,
    max_pairs: int | None = None,
    *,
    task: str = "pair_risks_kinds",
    schema: dict = SCHEMA,
    results_prefix: str = "assessments",
    render: Callable[[dict], list[tuple[str, str]]] = render_assessment,
    describe: Callable[[dict], list[str]] = describe_resource,
    key: str = "kind",
    results_dir: Path | None = None,
    results_name: str | None = None,
    on_result: Callable[[dict, dict, dict, int, int], None] | None = None,
    extra_catalog: dict | None = None,
    pair_filter: Callable[[dict, dict], bool] | None = None,
    seed: int | None = None,
    workers: int | None = None,
) -> None:
    """Shuffle resources and save up to max_pairs assessments as JSONL; zero skips.

    Every call goes to Claude (llm.call); ``task`` names the llm.toml task whose settings
    (model, effort, workers) and system prompt (``<task>_system``) apply; ``model`` (a
    command's --model) overrides the task's. The defaults are the loader's kind-level
    question; callers with other resource descriptions (e.g. materialize's session-level
    ones) pass their own task, schema, renderer, and identifying key. results_dir/results_name
    place the JSONL file; on_result(first, second, assessment, done, total) runs
    after each saved assessment. extra_catalog adds top-level entries (e.g.
    policies) to the shared catalog. pair_filter(first, second) keeps only the pairs it
    accepts (e.g. one item from each side). Pairs are a uniform random sample of all
    candidate pairs, in random order; ``seed`` makes the selection reproducible. Calls run
    on ``workers`` threads (the first alone, to fill the prompt cache); results are saved,
    printed, and passed to on_result as they finish, from the calling thread."""
    seed = seed if seed is not None else random.randrange(2**32)
    workers = workers or int(llm.settings(task).get("workers", 4))
    instructions = llm.prompt(f"{task}_system")
    rng = random.Random(seed)
    candidates = [p for p in combinations(resources, 2) if pair_filter is None or pair_filter(*p)]
    total_pairs = len(candidates)
    pairs = rng.sample(candidates, total_pairs if max_pairs is None else min(max_pairs, total_pairs))
    pairs = [(a, b) if rng.random() < 0.5 else (b, a) for a, b in pairs]  # no fixed first/second order

    print_table(
        "Pair selection",
        ("Metric", "Value"),
        [
            ("Items (" + key + ")", str(len(resources))),
            ("Selected pairs", f"{len(pairs)} of {total_pairs}"),
            ("Assessment calls", str(len(pairs))),
            ("Random seed", str(seed)),
        ],
        (30, 88),
    )
    if not pairs:
        return

    selected = {resource[key] for pair in pairs for resource in pair}
    catalog = json.dumps(
        {
            "resources": [
                resource
                for resource in sorted(resources, key=lambda resource: resource[key])
                if resource[key] in selected
            ],
            **(extra_catalog or {}),
        },
        sort_keys=True,
    )
    results_dir = results_dir or ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    results_path = results_dir / (results_name or f"{results_prefix}-{timestamp}.jsonl")
    results_path.touch(exist_ok=False)
    print_table(
        "Assessment output",
        ("Field", "Value"),
        [("JSONL results file", str(results_path))],
        (30, 88),
    )

    def assess(first: dict, second: dict) -> tuple[dict, dict]:
        """One Claude call; runs on a worker thread."""
        pair = json.dumps({"first": first[key], "second": second[key]})
        return llm.call(instructions, llm.prompt("pair_risks_user", catalog=catalog, pair=pair), schema, model=model, task=task)

    joint_risks, failed, done = 0, 0, 0

    def record(first: dict, second: dict, outcome: tuple | BaseException) -> None:
        """Save, print, and report one finished pair; called on the main thread only."""
        nonlocal joint_risks, failed, done
        done += 1
        if isinstance(outcome, BaseException):
            failed += 1
            with results_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps({"model": model, "seed": seed, "first": first, "second": second,
                                         "error": f"{type(outcome).__name__}: {outcome}"}, ensure_ascii=False) + "\n")
            print_table(f"Assessment {done}/{len(pairs)} failed", ("Field", "Value"),
                        [("Pair", f"{first['label']} + {second['label']}"), ("Error", f"{type(outcome).__name__}: {outcome}"[:500])], (30, 88))
            return
        assessment, usage = outcome
        with results_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"model": ", ".join(usage.get("models") or []) or model, "seed": seed, "first": first, "second": second,
                                     "assessment": assessment, "cost_usd_list_price": usage.get("cost_usd_list_price")}, ensure_ascii=False) + "\n")
        rows = [
            ("Pair", f"{first['label']} + {second['label']}"),
            ("First resource effects", "\n".join(describe(first))),
            ("Second resource effects", "\n".join(describe(second))),
            *render(assessment),
        ]
        if usage:
            rows.append(("Model", ", ".join(usage.get("models") or []) or "?"))
        print_table(f"Assessment {done}/{len(pairs)}", ("Field", "Value"), rows, (30, 88))
        if assessment["joint_risk"]:
            joint_risks += 1
        if on_result:
            on_result(first, second, assessment, done, len(pairs))

    def run(pair: tuple[dict, dict]) -> tuple | BaseException:
        try:
            return assess(*pair)
        except Exception as error:  # recorded per pair; the others go on
            return error

    # The first call runs alone (an early failure, e.g. a bad setting, shows before the rest start).
    record(*pairs[0], run(pairs[0]))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(run, pair): pair for pair in pairs[1:]}
        for future in as_completed(futures):
            record(*futures[future], future.result())

    print_table(
        "Annotation summary",
        ("Metric", "Value"),
        [("Model-flagged joint risks", f"{joint_risks}/{len(pairs) - failed} assessed pairs"),
         ("Failed calls", str(failed)), ("Parallel workers", str(workers))],
        (30, 88),
    )
