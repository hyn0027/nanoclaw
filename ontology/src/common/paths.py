"""Locations of the project's ontology files.

Code lives in src/; every ontology graph lives under ontology/.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_DIR = ROOT / "ontology"
UNIVERSAL_DIR = ONTOLOGY_DIR / "universal"  # generic modules, vocabulary, shapes
NANOCLAW_DIR = ONTOLOGY_DIR / "nanoclaw"  # NanoClaw harness modules
CLAUDE_DIR = ONTOLOGY_DIR / "claude"  # Claude provider (built-in tool) modules
SKILLS_DIR = ONTOLOGY_DIR / "skills"
MCP_DIR = ONTOLOGY_DIR / "mcp"  # third-party MCP servers, generated from pinned snapshots
# Always-available model modules, in discovery order (skills are separate).
MODEL_DIRS = (UNIVERSAL_DIR, NANOCLAW_DIR, CLAUDE_DIR)
VOCABULARY = UNIVERSAL_DIR / "vocabulary.ttl"
SHAPES = UNIVERSAL_DIR / "shapes.ttl"
MANIFEST = ROOT / "manifest.json"
OBSERVATIONS_DIR = ROOT / "observations"
