"""Validate selected skill ontologies and report their possible effects and calls."""

import argparse

from table_output import set_table_width

from llm_annotation import analyze_pairs

from .annotation_input import annotation_resources
from .assemble import assemble
from .catalog import check_selection, discover_catalog, read_manifest
from .checks import run_checks
from .inventory import build_inventory
from .report import print_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, help="fit printed tables into this many characters (default: the terminal width, or 160 when output is redirected)")
    parser.add_argument("--max-pairs", type=int, help="Maximum pairs to annotate (default: all; 0: skip)")
    parser.add_argument("--model", default="gpt-6-luna", help="OpenAI model (default: gpt-6-luna)")
    parser.add_argument("--workers", type=int, default=20, help="parallel LLM calls (default: 20)")
    parser.add_argument("--seed", type=int, help="random seed for the pair sample (default: a fresh one, printed)")
    args = parser.parse_args()
    if args.max_pairs is not None and args.max_pairs < 0:
        parser.error("--max-pairs must be non-negative")
    return args


def main() -> None:
    """Load and validate selected ontologies, report effects, and annotate pairs."""
    args = parse_args()
    set_table_width(args.width)
    manifest = read_manifest()
    catalog = discover_catalog()
    check_selection(manifest, catalog)
    combined = assemble(manifest, catalog)
    kinds = run_checks(combined)
    inventory = build_inventory(manifest, catalog, combined, kinds.invocations)
    print_report(inventory, catalog, combined)
    analyze_pairs(annotation_resources(inventory, combined), max_pairs=args.max_pairs, model=args.model, seed=args.seed, workers=args.workers)
