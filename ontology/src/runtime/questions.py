"""Questions for the operator when materialize or the runtime needs a fact it cannot observe.

Today one kind exists: the scope of a granted credential (which repositories a GitHub
token reaches, with which permissions). No API reveals a fine-grained token's scope,
and probing it with writes is not acceptable, so the operator is asked once; the answer
becomes a declaration in local/credential-scopes.json (declarations.py) that later runs
trust. A declaration that goes stale (the token was replaced, updated, or expired) asks
again.

A question is a list of steps, each a prompt with at most five options (it must fit a
row of chat buttons). A step may apply only after an earlier answer (``when``), and may
accept typed text in the terminal (``free_text``).

Where it is asked:
- materialize run in a terminal asks right there (ask_tty);
- otherwise the question is written to data/ontology-runtime/questions/<id>.json and
  NanoClaw's host (src/modules/ontology-runtime) DMs the owner one button card per
  step, recording each click in data/ontology-runtime/answers/<id>.json. Python is the
  only writer of questions/, the host the only writer of answers/ (the terminal answer
  command writes answers/<id>.terminal.json);
- ``ontology_runtime.py answer <id>`` answers an open question in the terminal.

Until a question is answered, materialize waits and the runtime holds expansions for
the sessions it concerns (the operator's choice: wait rather than assume).
"""

import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .paths import RUNTIME_DIR

QUESTIONS_DIR = RUNTIME_DIR / "questions"
ANSWERS_DIR = RUNTIME_DIR / "answers"
MAX_OPTIONS = 5


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def all_questions() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(QUESTIONS_DIR.glob("q*.json"))] if QUESTIONS_DIR.is_dir() else []


def get(qid: str) -> dict | None:
    path = QUESTIONS_DIR / f"{qid}.json"
    return json.loads(path.read_text()) if path.exists() else None


def save(q: dict) -> None:
    _write(QUESTIONS_DIR / f"{q['id']}.json", q)


def open_question(key: str, title: str, intro: str, steps: list[dict], meta: dict, sessions: list[str]) -> dict:
    """The open question for ``key``, created if there is none. Steps are checked to fit chat buttons."""
    for s in steps:
        assert 1 <= len(s["options"]) <= MAX_OPTIONS, s["id"]
    for q in all_questions():
        if q["key"] == key and q["status"] == "open":
            if set(sessions) - set(q["sessions"]):
                q["sessions"] = sorted(set(q["sessions"]) | set(sessions))
                save(q)
            return q
    q = {"id": "q" + secrets.token_hex(4), "key": key, "title": title, "intro": intro, "steps": steps, "meta": meta,
         "sessions": sorted(sessions), "status": "open", "created_at": now()}
    save(q)
    return q


def answers(q: dict) -> dict:
    """step id -> answer value, from the host (chat) and the terminal; the terminal wins."""
    out = {}
    for name in (f"{q['id']}.json", f"{q['id']}.terminal.json"):
        path = ANSWERS_DIR / name
        if path.exists():
            try:
                out.update(json.loads(path.read_text()).get("answers", {}))
            except ValueError:
                pass
    return out


# ── answering in plain language ──────────────────────────────────────────────
# Besides the choices, the operator can describe the answer in their own words (the
# first step's "@nl" option, or words typed at any terminal prompt). The text is the
# answer to the pseudo-step DESCRIBE; Python interprets it with Claude and appends a
# confirmation step "nl.confirm.<n>" (yes / again / choices). While a description is
# in play the choice steps are skipped; "choices" goes back to them.
NL = "@nl"
DESCRIBE = "describe"
CONFIRM = "nl.confirm."
NL_OPTION = {"label": "Describe it in my own words", "value": NL}


def latest_confirm(q: dict) -> dict | None:
    confirms = [s for s in q["steps"] if s["id"].startswith(CONFIRM)]
    return confirms[-1] if confirms else None


def nl_mode(q: dict, given: dict) -> bool:
    latest = latest_confirm(q)
    return DESCRIBE in given and not (latest and given.get(latest["id"]) == "choices")


def applies(step: dict, given: dict, q: dict | None = None) -> bool:
    """A step's ``when`` is one condition or a list of them (all must hold). In plain-
    language mode only the latest confirmation applies."""
    if q is not None and step["id"].startswith(CONFIRM):
        return nl_mode(q, given) and step is latest_confirm(q)
    if q is not None and nl_mode(q, given):
        return False
    cond = step.get("when")
    conds = [] if cond is None else cond if isinstance(cond, list) else [cond]
    return all(given.get(c["step"]) in c["in"] for c in conds)


def next_step(q: dict, given: dict) -> dict | None:
    return next((s for s in q["steps"] if applies(s, given, q) and s["id"] not in given), None)


def complete(q: dict, given: dict | None = None) -> bool:
    given = answers(q) if given is None else given
    if nl_mode(q, given):
        latest = latest_confirm(q)
        return bool(latest) and given.get(latest["id"]) == "yes" and q.get("interpretation", {}).get("text") == given[DESCRIBE]
    return next_step(q, given) is None


def needs_interpretation(q: dict, given: dict | None = None) -> str | None:
    """The description to interpret, when it has no current interpretation yet."""
    given = answers(q) if given is None else given
    if not nl_mode(q, given):
        return None
    text = given[DESCRIBE]
    return text if q.get("interpretation", {}).get("text") != text else None


def add_interpretation(q: dict, text: str, result: dict) -> dict:
    """Record Claude's reading of ``text`` and append the step that confirms it."""
    n = sum(1 for s in q["steps"] if s["id"].startswith(CONFIRM)) + 1
    q["interpretation"] = {"text": text, "result": result, "at": now()}
    if result.get("understood"):
        prompt = f"I understood: {result['summary'].rstrip('. ')}. Correct?"
        options = [{"label": "Yes, save it", "value": "yes"}, {"label": "No, let me describe it again", "value": "again"},
                   {"label": "Use the choices instead", "value": "choices"}]
    else:
        prompt = f"I couldn't work it out: {result.get('unclear') or result['summary']}"
        options = [{"label": "Describe it again", "value": "again"}, {"label": "Use the choices instead", "value": "choices"}]
    q["steps"].append({"id": f"{CONFIRM}{n}", "prompt": prompt, "options": options})
    save(q)
    return q


def finish(q: dict, apply) -> dict:
    """Apply the answers (``apply(q, answers)`` returns a description) and close the question."""
    given = answers(q)
    q["result"] = apply(q, given)
    q["answers"] = given
    q["status"] = "answered"
    q["answered_at"] = now()
    save(q)
    return q


def ask_tty(q: dict) -> dict:
    """Ask every applicable step in the terminal; returns the answers (also saved, so a
    chat card for the same question stops at the next step)."""
    given = answers(q)
    print(f"\n── {q['title']} ──\n{q['intro']}\n(At any prompt you can answer in your own words: type d, or just write it.)\n", file=sys.stderr)

    def record(key: str, value: str) -> None:
        given[key] = value
        _write(ANSWERS_DIR / f"{q['id']}.terminal.json", {"answers": given, "updated_at": now()})

    while True:
        if (text := needs_interpretation(q, given)) is not None:
            print("  … working out what that means", file=sys.stderr, flush=True)
            interpret(q, text)
            q.update(get(q["id"]) or q)
        step = next_step(q, given)
        if step is None:
            break
        print(step["prompt"], file=sys.stderr)
        for i, o in enumerate(step["options"], 1):
            print(f"  {i}. {o['label']}", file=sys.stderr)
        hint = f", or type {step['free_text']}" if step.get("free_text") else ""
        raw = input(f"choose 1-{len(step['options'])}{hint}, or d to describe: ").strip()
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(step["options"]):
            value = step["options"][int(raw) - 1]["value"]
            if value == NL or value == "again":
                text = input("describe the access in your own words: ").strip()
                if text:
                    if value == "again":
                        record(step["id"], value)
                    record(DESCRIBE, "text:" + text)
                continue
            if value == "@text":
                raw = input(f"{step['free_text']}: ").strip()
                if raw:
                    record(step["id"], "text:" + raw)
                continue
            record(step["id"], value)
        elif raw.lower() in ("d", "describe"):
            text = input("describe the access in your own words: ").strip()
            if text:
                record(DESCRIBE, "text:" + text)
        elif step.get("free_text") and " " not in raw:
            record(step["id"], "text:" + raw)  # a typed value (pattern, date)
        else:
            record(DESCRIBE, "text:" + raw)  # words: a description
    return given


def interpret(q: dict, text: str) -> dict:
    """Ask Claude what the description means for this question; appends the confirm step."""
    from .scope_questions import interpret_description
    result = interpret_description(q, text.removeprefix("text:"))
    return add_interpretation(q, text, result)


def wait(q: dict, advance=None, poll_s: int = 5) -> None:
    """Block until the question is answered in chat or in another terminal. ``advance``
    (scope_questions.advance) interprets plain-language answers as they arrive."""
    print(f"waiting for your answer to '{q['title']}' (question {q['id']}): sent to you as a direct message by NanoClaw; "
          f"or answer in a terminal: cd ontology && .venv/bin/python ontology_runtime.py answer {q['id']}", file=sys.stderr, flush=True)
    last = -1
    while True:
        if advance is not None:
            advance(q)
        q = get(q["id"]) or q
        if q["status"] != "open" or complete(q):
            return
        given = answers(q)
        if len(given) != last:
            step = next_step(q, given)
            print(f"  … {len(given)} answered; next: {step['prompt'] if step else '-'}", file=sys.stderr, flush=True)
            last = len(given)
        time.sleep(poll_s)
