"""Structured calls to Claude through headless Claude Code (`claude -p`), on the
user's Claude subscription. Every model call of the ontology side goes through ``call``.

Settings and every prompt live in one file, ontology/llm.toml (ONTOLOGY_LLM_CONFIG names
another): ``settings(task)`` reads a task's settings, ``prompt(name, **values)`` fills a
prompt template. Each call runs with no tools, no MCP servers, no settings files, and no
saved session, from an empty working directory, so the model sees only the prompt.
"""

import json
import os
import re
import shutil
import subprocess
import tomllib
from functools import lru_cache
from pathlib import Path
from string import Template

from ..common.paths import ROOT
from .paths import NANOCLAW

CONFIG = Path(os.environ.get("ONTOLOGY_LLM_CONFIG") or ROOT / "llm.toml")


class LLMError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _config(mtime: float) -> dict:  # noqa: ARG001 - keyed by the file's mtime, so an edit is read on the next call
    with open(CONFIG, "rb") as f:
        return tomllib.load(f)


def config() -> dict:
    return _config(CONFIG.stat().st_mtime)


def settings(task: str | None = None) -> dict:
    """[claude] with the task's overrides; empty strings mean "not set"."""
    c = config()
    merged = {**c.get("claude", {}), **(c.get("tasks", {}).get(task, {}) if task else {})}
    return {k: v for k, v in merged.items() if v != ""}


def prompt(name: str, **values) -> str:
    """The named prompt with its $placeholders filled (a missing value is an error)."""
    try:
        text = Template(config()["prompts"][name]).substitute({k: "" if v is None else str(v) for k, v in values.items()})
    except KeyError as e:
        raise LLMError(f"prompt {name}: no value for {e} (llm.toml)") from e
    return re.sub(r"\n{3,}", "\n\n", text).strip()  # an empty optional part leaves no gap


def claude_bin() -> str:
    found = settings().get("bin") or os.environ.get("ONTOLOGY_CLAUDE_BIN") or shutil.which("claude") \
        or shutil.which("claude", path=str(Path.home() / ".local" / "bin"))
    if not found:
        raise LLMError("claude CLI not found (set [claude].bin in llm.toml)")
    return found


def call(system: str, prompt_text: str, schema: dict, model: str | None = None, task: str | None = None) -> tuple[dict, dict]:
    """(structured output, usage metadata). ``task`` picks the [tasks.<task>] settings;
    ``model`` (a command's --model) overrides them."""
    s = settings(task)
    cwd = NANOCLAW / s.get("workdir", "data/ontology-runtime/llm-cwd")
    cwd.mkdir(parents=True, exist_ok=True)
    argv = [claude_bin(), "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--setting-sources", "", "--output-format", "json", "--json-schema", json.dumps(schema),
            "--system-prompt", system]
    model = model or os.environ.get("ONTOLOGY_MODEL") or s.get("model")
    for flag, value in (("--model", model), ("--effort", s.get("effort")), ("--fallback-model", s.get("fallback_model"))):
        if value:
            argv += [flag, str(value)]
    timeout = int(s.get("timeout_s", 1200))
    try:
        done = subprocess.run(argv, input=prompt_text, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude -p timed out after {timeout}s") from e
    try:
        out = json.loads(done.stdout)
    except ValueError as e:
        raise LLMError(f"claude -p exit {done.returncode}: {(done.stderr or done.stdout)[-800:]}") from e
    if out.get("is_error") or out.get("structured_output") is None:
        raise LLMError(f"claude -p failed ({out.get('subtype')}): {str(out.get('result'))[:800]}")
    usage = {
        "models": sorted((out.get("modelUsage") or {}).keys()),
        "cost_usd_list_price": out.get("total_cost_usd"),
        "duration_ms": out.get("duration_ms"),
        "task": task,
    }
    return out["structured_output"], usage
