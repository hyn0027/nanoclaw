"""Where the runtime reads NanoClaw state and keeps session ontologies."""

import os
from pathlib import Path

from ..common.paths import ONTOLOGY_DIR, ROOT

NANOCLAW = Path(os.environ.get("NANOCLAW_ROOT", ROOT.parent)).resolve()
DATA = NANOCLAW / "data"
CENTRAL_DB = DATA / "v2.db"
SESSIONS = DATA / "v2-sessions"
# Where the OneCLI gateway writes captured calls (mounted into it at /capture).
CAPTURE_DIR = Path(os.environ.get("NANOCLAW_CAPTURE_DIR", DATA / "onecli-capture"))
# Runtime-wide state: the capture cursor and the run lock.
RUNTIME_DIR = DATA / "ontology-runtime"
RUNTIME_VOCABULARY = ONTOLOGY_DIR / "runtime" / "vocabulary.ttl"
SKILLS_SRC = NANOCLAW / "container" / "skills"
LOG_DIR = NANOCLAW / "logs" / "ontology-runtime"


def session_dir(group_id: str, session_id: str) -> Path:
    return SESSIONS / group_id / session_id / "ontology"


def group_settings(group_id: str) -> Path:
    return SESSIONS / group_id / ".claude-shared" / "settings.json"
