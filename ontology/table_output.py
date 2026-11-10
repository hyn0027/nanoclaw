"""Small aligned, wrapping tables for the ontology CLI reports."""

import shutil
from textwrap import wrap

# When set (record_tables), every printed table is also appended here unwrapped,
# as (title, headers, rows), for logs that keep the full cell contents.
_recorded: list[tuple[str, tuple[str, ...], list[tuple[str, ...]]]] | None = None


# Total width every table is fitted into (columns plus " | " separators). None: the
# terminal's width when known, else FALLBACK_WIDTH (e.g. when output is redirected).
_max_total: int | None = None
FALLBACK_WIDTH = 160
MIN_COLUMN = 10


def set_table_width(total: int | None) -> None:
    """Fit every table into ``total`` characters (None: the terminal's width)."""
    global _max_total
    _max_total = total


def table_width() -> int:
    return _max_total or shutil.get_terminal_size((FALLBACK_WIDTH, 24)).columns


def fit_widths(widths: list[int], headers: tuple[str, ...], total: int) -> list[int]:
    """Shrink the widest columns until the table fits ``total`` characters, keeping each
    column at least MIN_COLUMN wide (or its header's width, if smaller)."""
    widths = list(widths)
    budget = total - 3 * (len(widths) - 1)
    floors = [min(w, max(MIN_COLUMN, min(len(h), MIN_COLUMN))) for w, h in zip(widths, headers)]
    while sum(widths) > budget:
        i = max(range(len(widths)), key=lambda j: widths[j] - floors[j])
        if widths[i] <= floors[i]:
            break  # cannot shrink further; the table stays wider than the limit
        widths[i] -= 1
    return widths


def record_tables(sink: list | None) -> None:
    """Start (a list) or stop (None) recording printed tables."""
    global _recorded
    _recorded = sink


def _wrap_line(line: str, width: int) -> list[str]:
    """Wrap one line, keeping its leading indent (plus two spaces) on continuation lines."""
    indent = line[: len(line) - len(line.lstrip(" "))]
    return wrap(line, width, subsequent_indent=indent + "  ", drop_whitespace=True) or [""]


def print_table(
    title: str,
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
    max_widths: tuple[int, ...],
    record_rows: list[tuple[str, ...]] | None = None,
) -> None:
    """Print rows with aligned columns, wrapping long cell contents.

    Cells may hold several lines; indented lines stay indented when wrapped.
    The table is fitted into table_width() characters: the widest columns shrink
    first (cells wrap more), none below MIN_COLUMN.
    record_rows, when given, is what gets recorded instead of rows (the full
    contents behind a shortened terminal display).
    When any cell spans several lines, rows are separated by a rule.
    """
    if not rows:
        return
    if _recorded is not None:
        _recorded.append((title, tuple(headers), [tuple(row) for row in (record_rows if record_rows is not None else rows)]))
    widths = [
        min(limit, max(len(header), *(len(line) for row in rows for line in (row[index].splitlines() or [""]))))
        for index, (header, limit) in enumerate(zip(headers, max_widths))
    ]
    widths = fit_widths(widths, tuple(headers), table_width())
    rule = "-+-".join("-" * width for width in widths)
    multiline = any("\n" in cell for row in rows for cell in row)
    print(f"\n{title}")
    header_lines = [wrap(header, width) or [""] for header, width in zip(headers, widths)]
    for line_index in range(max(map(len, header_lines))):
        print(" | ".join((h[line_index] if line_index < len(h) else "").ljust(width) for h, width in zip(header_lines, widths)))
    print(rule)
    for row_index, row in enumerate(rows):
        if multiline and row_index:
            print(rule.replace("-", "·").replace("+", "·"))
        cells = [
            [part for line in (value.splitlines() or [""]) for part in _wrap_line(line, width)]
            for value, width in zip(row, widths)
        ]
        for line_index in range(max(map(len, cells))):
            print(
                " | ".join(
                    (cell[line_index] if line_index < len(cell) else "").ljust(width)
                    for cell, width in zip(cells, widths)
                )
            )
