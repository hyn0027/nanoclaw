"""Questions for the operator: multiple-choice cards, each also answerable in their own words.

A question is about one *topic* (topics.py): fields the ontology needs but cannot observe,
such as what a GitHub token can do. It is asked one card at a time. Every card has up to
MAX_CHOICES choices plus "Other (please specify)". Cards come from:
- the topic's fixed steps, which are choice questions that fill fields;
- Claude, which writes the next choice question from the topic's field schema when no
  fixed step is left and fields are still missing.
An "Other" answer is read by Claude against the schema and fills whatever fields it covers;
steps whose fields are then filled are skipped. When a saved answer exists, the first card
asks whether it still holds. When every field is filled, a last card shows what will be
saved and asks to confirm.
Every card after the first also has "Back to the last question": it undoes the last answer
(choice or Other) and shows that card again.

Files under data/ontology-runtime/:
- questions/<id>.json is written only by Python. It holds the topic, the fields so far, and
  `card`, the one card waiting for an answer: {n, title, prompt, options: [{label, value}]}.
- answers/<id>.json is written only by the host:
  {"answers": {"<card n>": {"choice": value} | {"other": text}}}.
- answers/<id>.terminal.json holds the same, from `ontology_runtime.py answer <id>`.
NanoClaw's host (src/modules/ontology-runtime/questions.ts) shows each card as a DM with
buttons, adds the Other button, and records answers; all logic is here. Whoever waits on
a question (a blocking sync, materialize, the terminal) calls advance() to move it on.

Until a question is answered, the sessions it concerns wait (the operator's choice:
wait rather than assume).
"""

import copy
import fcntl
import json
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import llm
from .llm import LLMError, call
from .paths import RUNTIME_DIR
from .store import now, write_atomic

QUESTIONS_DIR = RUNTIME_DIR / "questions"
ANSWERS_DIR = RUNTIME_DIR / "answers"
MAX_CHOICES = 4  # plus the host's "Other" button: five, one row of chat buttons
KEEP = "keep"
BACK = {"label": "← Back to the last question", "value": "@back"}
UNDOABLE = ("fields", "choices", "generated", "history", "card")
CONFIRM = "confirm"


@dataclass
class Step:
    """A choice question. Each option is {label, value, set}, where ``set`` is the field
    updates it makes: a dict, or a function of the current fields returning one."""
    id: str
    prompt: str
    options: list[dict]
    asks: tuple[str, ...] = ()  # skipped once these fields are filled (e.g. by an Other answer)
    when: Callable[[dict], bool] | None = None  # (fields so far) -> whether it applies
    filled: Callable[[dict], bool] | None = None  # overrides the ``asks`` check

    def is_filled(self, fields: dict) -> bool:
        if self.filled:
            return self.filled(fields)
        return bool(self.asks) and all(fields.get(a) is not None for a in self.asks)


@dataclass
class Topic:
    name: str
    what: str  # "GitHub token"
    fields: dict  # JSON-schema properties; every one must be filled
    guidance: str  # for Claude: what the fields mean and how to fill them
    describe: Callable[[dict], str]  # fields -> one line the operator can confirm
    save: Callable[[dict, dict], str]  # (question, fields) -> result line
    steps: Callable[[dict], list[Step]] = lambda meta: []  # fixed steps for the question's meta
    context: Callable[[dict], str] = lambda meta: ""  # what is known, for Claude
    hosts: tuple[str, ...] = ()  # credentials used at these hosts are asked about
    confirm: bool = True  # False: save as soon as every field is filled, without the confirm card


def _topic(q: dict) -> Topic:
    from .topics import TOPICS
    return TOPICS[q["topic"]]


# ── storage ──────────────────────────────────────────────────────────────────

def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2) + "\n")


def all_questions() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(QUESTIONS_DIR.glob("q*.json"))] if QUESTIONS_DIR.is_dir() else []


def get(qid: str) -> dict | None:
    path = QUESTIONS_DIR / f"{qid}.json"
    return json.loads(path.read_text()) if path.exists() else None


def save(q: dict) -> None:
    _write(QUESTIONS_DIR / f"{q['id']}.json", q)


def answers(q: dict) -> dict:
    """card n (as a string) -> {"choice": v} | {"other": text}, from chat and the terminal; the terminal wins."""
    out = {}
    for name in (f"{q['id']}.json", f"{q['id']}.terminal.json"):
        path = ANSWERS_DIR / name
        if path.exists():
            try:
                out.update(json.loads(path.read_text()).get("answers", {}))
            except ValueError:
                pass
    return out


def open_question(topic: Topic, key: str, title: str, intro: str, meta: dict, sessions: list[str],
                  existing: dict | None = None) -> dict:
    """The open question for ``key``, created (with its first card) if there is none.
    ``existing``: the saved answer's fields, offered to keep."""
    for q in all_questions():
        if q.get("key") == key and q["status"] == "open":
            if set(sessions) - set(q["sessions"]):
                q["sessions"] = sorted(set(q["sessions"]) | set(sessions))
                save(q)
            return q
    q = {"id": "q" + secrets.token_hex(4), "key": key, "topic": topic.name, "title": title, "intro": intro,
         "meta": meta, "sessions": sorted(sessions), "status": "open", "created_at": now(),
         "existing": existing, "fields": dict.fromkeys(topic.fields), "choices": {}, "generated": [],
         "history": [], "n": 0, "card": None}
    _next_card(topic, q)
    save(q)
    return q


# ── the engine ───────────────────────────────────────────────────────────────

def _steps(topic: Topic, q: dict) -> list[Step]:
    steps = []
    if q.get("existing"):
        steps.append(Step(KEEP, f"Saved answer: {topic.describe(q['existing'])}. Still right?",
                          [{"label": "Yes, keep it", "value": "keep"}, {"label": "No, change it", "value": "change"}]))
    steps += topic.steps(q["meta"])
    steps += [Step(g["id"], g["prompt"], g["options"], tuple(g["asks"])) for g in q["generated"]]
    return steps


def _pending(topic: Topic, q: dict) -> Step | None:
    return next((s for s in _steps(topic, q) if s.id not in q["choices"] and (s.when is None or s.when(q["fields"]))
                 and not s.is_filled(q["fields"])), None)


def _missing(q: dict) -> list[str]:
    return [k for k, v in q["fields"].items() if v is None]


def _update_schema(topic: Topic) -> dict:
    """Every field, each null when not covered."""
    return {"type": "object", "additionalProperties": False, "required": list(topic.fields),
            "properties": {k: {"anyOf": [s, {"type": "null"}], **({"description": s["description"]} if "description" in s else {})}
                           for k, s in topic.fields.items()}}


def _state(topic: Topic, q: dict) -> str:
    return "\n".join(filter(None, [
        topic.context(q["meta"]),
        f"Saved answer from before: {topic.describe(q['existing'])}" if q.get("existing") else "",
        f"Fields so far (null = not yet known): {json.dumps(q['fields'])}",
    ]))


def _generate(topic: Topic, q: dict) -> Step:
    """Claude writes the next choice question for the missing fields."""
    missing = _missing(q)
    schema = {"type": "object", "additionalProperties": False, "required": ["prompt", "options"], "properties": {
        "prompt": {"type": "string", "description": "one short question to the operator"},
        "options": {"type": "array", "minItems": 2, "maxItems": MAX_CHOICES, "items": {
            "type": "object", "additionalProperties": False, "required": ["label", "updates"],
            "properties": {"label": {"type": "string", "description": "button text, at most 60 characters"},
                           "updates": _update_schema(topic)}}}}}
    system = llm.prompt("question_generate_system", what=topic.what, guidance=topic.guidance, max_choices=MAX_CHOICES)
    result, _ = call(system, llm.prompt("question_generate_user", state=_state(topic, q), missing=", ".join(missing)),
                     schema, task="question_generate")
    options = [{"label": o["label"][:80], "value": str(i), "set": {k: v for k, v in o["updates"].items() if v is not None and k in missing}}
               for i, o in enumerate(result["options"])]
    asks = sorted({k for o in options for k in o["set"]}) or missing
    g = {"id": f"gen{len(q['generated']) + 1}", "prompt": result["prompt"], "options": options, "asks": asks}
    q["generated"].append(g)
    return Step(g["id"], g["prompt"], options, tuple(asks))


def _next_card(topic: Topic, q: dict) -> None:
    step = _pending(topic, q)
    if step is None and _missing(q):
        step = _generate(topic, q)
    if step is None:
        step = Step(CONFIRM, f"I'll save: {topic.describe(q['fields'])}. Correct?",
                    [{"label": "Yes, save it", "value": "yes"}, {"label": "Start over", "value": "restart"}])
    prompt = step.prompt
    if note := q.pop("note", None):
        prompt = f"{note}\n\n{prompt}"
    if q["n"] == 0:
        prompt = f"{q['intro']}\n\n{prompt}"
    _show(q, {"step": step.id, "title": q["title"], "prompt": prompt,
              "options": [{"label": o["label"], "value": o["value"]} for o in step.options[:MAX_CHOICES]]})


def _show(q: dict, card: dict) -> None:
    """Make ``card`` the current one, under a new number (answers are keyed by it), with
    Back when there is an answer to undo."""
    q["n"] += 1
    options = [o for o in card["options"] if o["value"] != BACK["value"]] + ([BACK] if q.get("undo") else [])
    q["card"] = {**card, "n": q["n"], "options": options}


def _named(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, dict) and "name" in x for x in value)


def _apply(q: dict, updates: dict) -> None:
    """Fill fields from updates. A list of named items (e.g. permissions) is merged by name, so
    an answer about some items keeps the others; an item with level "none" is removed."""
    for k, v in updates.items():
        if k not in q["fields"] or v is None:
            continue
        old = q["fields"][k]
        if _named(v) and _named(old):
            merged = {x["name"]: x for x in old}
            merged.update({x["name"]: x for x in v})
            v = list(merged.values())
        if _named(v):
            v = [x for x in v if x.get("level") != "none"]
        q["fields"][k] = v


def _interpret(topic: Topic, q: dict, step_id: str, text: str) -> None:
    """Claude reads an Other answer: fills the fields it covers, or notes what is unclear."""
    schema = {"type": "object", "additionalProperties": False, "required": ["understood", "unclear", "updates"], "properties": {
        "understood": {"type": "boolean", "description": "false only when the answer settles nothing"},
        "unclear": {"type": ["string", "null"], "description": "what is missing or ambiguous, when understood is false"},
        "updates": _update_schema(topic)}}
    system = llm.prompt("question_interpret_system", what=topic.what, guidance=topic.guidance)
    card = q["card"]
    prompt = llm.prompt("question_interpret_user", state=_state(topic, q), question=card["prompt"],
                        choices=", ".join(o["label"] for o in card["options"]), answer=text)
    result, _ = call(system, prompt, schema, task="question_interpret")
    if step_id == KEEP:
        _apply(q, q["existing"])  # changes relative to the saved answer
    _apply(q, result["updates"])
    if result["understood"]:
        if step_id != CONFIRM:
            q["choices"][step_id] = "@other"
    else:
        q["note"] = f"I couldn't work that out: {result.get('unclear') or 'please try again'}"


def advance(q: dict) -> dict | None:
    """Apply the answer to the current card, if there is one, and make the next card.
    Returns the question when this closed it. Locked, so two waiters never apply the same
    answer twice. Raises LLMError when Claude is needed and fails (retried next time)."""
    QUESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUESTIONS_DIR / f"{q['id']}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        q = get(q["id"]) or q
        if q["status"] != "open":
            return None
        topic = _topic(q)
        card = q["card"]
        given = answers(q).get(str(card["n"]))
        if not given:
            return None
        step_id = card["step"]
        if given.get("choice") == BACK["value"]:
            if q.get("undo"):  # back to the card before, as it was before it was answered
                previous = q["undo"].pop()
                q.update(previous)
                _show(q, previous["card"])
                save(q)
            return None
        before = copy.deepcopy({k: q[k] for k in UNDOABLE})
        if "other" in given:
            _interpret(topic, q, step_id, given["other"])
        elif step_id == CONFIRM and given.get("choice") == "yes":
            return _finish(topic, q, given)
        elif step_id == KEEP and given.get("choice") == "keep":
            q["fields"] = dict(q["existing"])
            return _finish(topic, q, given)
        elif step_id == CONFIRM:  # start over
            q.update(fields=dict.fromkeys(topic.fields), choices={}, generated=[])
        else:
            step = next((s for s in _steps(topic, q) if s.id == step_id), None)
            if step is None:  # the card's step no longer exists (the topic changed): show the next card instead
                _next_card(topic, q)
                save(q)
                return None
            option = next((o for o in step.options if o["value"] == given.get("choice")), None)
            if option is None:
                return None
            q["choices"][step_id] = option["value"]
            _apply(q, option["set"](q["fields"]) if callable(option.get("set")) else option.get("set") or {})
        if not topic.confirm and not _missing(q):
            return _finish(topic, q, given)
        q["history"].append({"n": card["n"], "step": step_id, "answer": given, "at": now()})
        if (q["fields"], q["choices"]) != (before["fields"], before["choices"]):
            q.setdefault("undo", []).append(before)  # an answer that settled nothing is not a step back
        _next_card(topic, q)
        save(q)
    return None


def _finish(topic: Topic, q: dict, given: dict) -> dict:
    q["history"].append({"n": q["card"]["n"], "step": q["card"]["step"], "answer": given, "at": now()})
    q["result"] = topic.save(q, q["fields"])
    q.update(status="answered", answered_at=now(), card=None)
    save(q)
    return q


def complete(q: dict, fields: dict, how: str) -> dict | None:
    """Answer an open question outside its cards (e.g. a terminal command that did what it
    asks for): set ``fields`` and save. None when it was no longer open."""
    QUESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUESTIONS_DIR / f"{q['id']}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        q = get(q["id"]) or q
        if q["status"] != "open":
            return None
        q["fields"] = {**q["fields"], **fields}
        return _finish(_topic(q), q, {"done": how})


def own_words(q: dict) -> list[str]:
    """The operator's Other answers, for a saved note."""
    return [h["answer"]["other"] for h in q.get("history", []) if "other" in h["answer"]]


# ── asking ───────────────────────────────────────────────────────────────────

def ask_tty(q: dict) -> dict:
    """Ask the question in this terminal until it is answered. Answers are saved where the
    chat card sees them, so a card for the same question stops."""
    print(f"\n── {q['title']} ──", file=sys.stderr)
    if q["n"] > 1:
        print(q["intro"], file=sys.stderr)
    while (q := get(q["id"]) or q)["status"] == "open":
        card = q["card"]
        print(f"\n{card['prompt']}", file=sys.stderr)
        for i, o in enumerate(card["options"], 1):
            print(f"  {i}. {o['label']}", file=sys.stderr)
        other = len(card["options"]) + 1
        print(f"  {other}. Other (please specify)", file=sys.stderr)
        raw = input(f"choose 1-{other}, or just type your answer: ").strip()
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) < other:
            answer = {"choice": card["options"][int(raw) - 1]["value"]}
        else:
            text = input("your answer: ").strip() if raw == str(other) else raw
            if not text:
                continue
            answer = {"other": text}
            print("  … working out what that means", file=sys.stderr, flush=True)
        path = ANSWERS_DIR / f"{q['id']}.terminal.json"
        given = json.loads(path.read_text()).get("answers", {}) if path.exists() else {}
        _write(path, {"answers": {**given, str(card["n"]): answer}, "updated_at": now()})
        try:
            advance(q)
        except LLMError as e:
            sys.exit(f"could not read that answer: {e}; run the answer command again to retry")
    return q


def wait(q: dict, poll_s: int = 5) -> dict:
    """Block until the question is answered in chat or in another terminal."""
    print(f"waiting for your answer to '{q['title']}' (question {q['id']}): sent to you as a direct message by NanoClaw; "
          f"or answer in a terminal: cd ontology && .venv/bin/python ontology_runtime.py answer {q['id']}", file=sys.stderr, flush=True)
    shown = None
    while True:
        try:
            advance(q)
        except LLMError as e:
            print(f"  … could not read the answer yet ({e}); retrying", file=sys.stderr, flush=True)
        q = get(q["id"]) or q
        if q["status"] != "open":
            return q
        if q["card"]["n"] != shown:
            shown = q["card"]["n"]
            print(f"  … card {shown}: {q['card']['prompt'].splitlines()[-1]}", file=sys.stderr, flush=True)
        time.sleep(poll_s)
