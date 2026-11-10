"""Thin wrappers over the external CLIs and files the collector reads.

Every call is read-only.
"""

import json
import shlex
import shutil
import sqlite3
import subprocess
from pathlib import Path


def run(argv: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True, timeout=60).stdout


def tool(name: str) -> str:
    """Resolve a CLI from PATH, falling back to ~/.local/bin where NanoClaw setup installs it."""
    found = shutil.which(name) or shutil.which(name, path=str(Path.home() / ".local" / "bin"))
    if not found:
        raise SystemExit(f"'{name}' not found on PATH or in ~/.local/bin.")
    return found


def ncl(nanoclaw: Path, *args: str) -> object:
    """Host ncl over its Unix socket, JSON output."""
    out = json.loads(run([str(nanoclaw / "bin" / "ncl"), *args, "--json"], cwd=nanoclaw))
    if not out.get("ok"):
        raise SystemExit(f"ncl {' '.join(args)} failed: {out.get('error')}")
    return out["data"]


def container_command(container: str, name: str) -> str:
    """Path of ``name`` on the container's PATH (command -v), or "" when absent. Read-only."""
    try:
        return run([tool("docker"), "exec", container, "sh", "-c", f"command -v {shlex.quote(name)} || true"]).strip()
    except subprocess.CalledProcessError:
        return ""


def docker_json(*args: str) -> object:
    return json.loads(run([tool("docker"), *args]))


def sqlite_rows(path: Path, sql: str) -> list[dict]:
    """Rows from a SQLite file opened read-only; [] if missing or the table is absent."""
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(sql)]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
