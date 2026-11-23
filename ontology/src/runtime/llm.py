"""Structured calls to Claude through headless Claude Code (`claude -p`), on the
user's Claude subscription.

Each call runs with no tools, no MCP servers, no settings files, and no saved
session, from an empty working directory, so the model sees only the prompt.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from .paths import RUNTIME_DIR


class LLMError(RuntimeError):
    pass


def claude_bin() -> str:
    found = os.environ.get("ONTOLOGY_CLAUDE_BIN") or shutil.which("claude") or shutil.which("claude", path=str(Path.home() / ".local" / "bin"))
    if not found:
        raise LLMError("claude CLI not found (set ONTOLOGY_CLAUDE_BIN)")
    return found


TIMEOUT_S = 1200


def call(system: str, prompt: str, schema: dict, model: str | None = None) -> tuple[dict, dict]:
    """(structured output, usage metadata)."""
    cwd = RUNTIME_DIR / "llm-cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    argv = [claude_bin(), "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--setting-sources", "", "--output-format", "json", "--json-schema", json.dumps(schema),
            "--system-prompt", system]
    model = model or os.environ.get("ONTOLOGY_MODEL")
    if model:
        argv += ["--model", model]
    try:
        done = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude -p timed out after {TIMEOUT_S}s") from e
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
    }
    return out["structured_output"], usage
