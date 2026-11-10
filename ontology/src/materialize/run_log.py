"""Per-run log directory: everything a materialize run printed, in full.

    observations/runs/<UTC timestamp>-<session>/
      console.log            the terminal output, as printed (stdout and stderr)
      report.md              warnings, every report table with full cell contents
                             (no wrapping or truncation), model/runtime differences
      instances.json         discovered instances, their sources, and gaps
      requests.jsonl         every discovery request made as the agent (no bodies)
      joint-risks.md         the LLM pair annotation, for reading (when it ran)
      joint-risks.jsonl      the same assessments, one JSON object per pair

The directory is under observations/ (git-ignored) because the tables hold real
identifiers, handles, and paths.
"""

import html
import json
import sys
from pathlib import Path
from typing import TextIO

from table_output import record_tables


class _Tee:
    """Write to the terminal stream and the console log."""

    def __init__(self, stream: TextIO, log: TextIO) -> None:
        self.stream, self.log = stream, log

    def write(self, text: str) -> int:
        self.log.write(text)
        return self.stream.write(text)

    def flush(self) -> None:
        self.log.flush()
        self.stream.flush()

    def __getattr__(self, name: str):
        return getattr(self.stream, name)


def cell(text: str) -> str:
    """A Markdown table cell keeping line breaks and leading indentation."""
    lines = []
    for line in str(text).splitlines() or [""]:
        stripped = line.lstrip(" ")
        lines.append("&nbsp;" * (len(line) - len(stripped)) + html.escape(stripped, quote=False).replace("|", "\\|"))
    return "<br>".join(lines)


def markdown_table(title: str, headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    out = [f"## {title}", "", "| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(value) for value in row) + " |" for row in rows]
    return "\n".join(out) + "\n"


class RunLog:
    def __init__(self, root: Path, session_id: str, taken_at: str) -> None:
        self.dir = root / "runs" / f"{taken_at.replace(':', '')}-{session_id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.warnings: list[str] = []
        self.tables: list = []
        self._console = (self.dir / "console.log").open("w", encoding="utf-8")
        sys.stdout = _Tee(sys.__stdout__, self._console)
        sys.stderr = _Tee(sys.__stderr__, self._console)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"warning: {message}", file=sys.stderr)

    def write_json(self, name: str, records: list[dict]) -> None:
        with (self.dir / name).open("w", encoding="utf-8") as out:
            for r in records:
                out.write(json.dumps(r, ensure_ascii=False) + "\n")

    def start_tables(self) -> None:
        record_tables(self.tables)

    def write_report(self, title: str, findings: list[str], snapshot: Path | None) -> Path:
        record_tables(None)
        parts = [f"# {title}", ""]
        if snapshot:
            parts += [f"Snapshot: `{snapshot}`", ""]
        if self.warnings:
            parts += ["## Warnings", "", *[f"- {w}" for w in self.warnings], ""]
        parts += [markdown_table(*table) for table in self.tables]
        if findings:
            parts += ["## Model/runtime differences", "", *[f"- {f}" for f in findings], ""]
        path = self.dir / "report.md"
        path.write_text("\n".join(parts), encoding="utf-8")
        return path

    def close(self) -> None:
        sys.stdout.flush()
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
        self._console.close()
