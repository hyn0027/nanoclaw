"""Instantiate the universal NanoClaw ontology for one live session.

Entry point; the implementation lives in ``src.materialize``.
Run ``python3 materialize.py --help`` for options.
"""

from src.materialize.cli import main

if __name__ == "__main__":
    main()
