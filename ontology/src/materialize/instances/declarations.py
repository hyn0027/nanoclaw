"""User-declared credential scopes: what a token may do, as its owner says.

Some token limits cannot be observed: GitHub has no API that lists a fine-grained
token's selected repositories or permissions, and finding out by trying writes is
not acceptable. The owner declares them once, here, and materialize trusts the
declaration (marked "declared by user" wherever it is used), checks it against what
discovery can observe, and narrows instances by it.

The file is ``local/credential-scopes.json`` (git-ignored: it names private
resources), keyed by OneCLI credential name:

    {
      "credentials": {
        "GitHub": {
          "secretId": "...",              # the OneCLI secret declared about
          "secretUpdatedAt": "...",       # its updated_at then; a later value means the token changed
          "declaredAt": "2026-10-16T...Z",
          "scope": {                      # environment-specific; GitHub: see envs/github.py
            "repositories": ["hyn0027/agent-playground"],
            "permissions": "*",           # or {"issues": "write", "contents": "read", ...}
            "user_permissions": {}        # "*" (default) or account-level ones; {} = none
          },
          "expiresAt": "2027-01-14",      # optional; after it, the declaration is stale
          "note": "fine-grained PAT, one repository"
        }
      }
    }

Edit it with ``python3 credential_scope.py`` (set, show, rm, suggest).
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ...common.paths import ROOT
from ..gateway_policy import patterns_overlap

PATH = ROOT / "local" / "credential-scopes.json"


@dataclass
class Declaration:
    credential: str          # OneCLI secret name
    scope: dict
    declared_at: str
    stale: str | None        # why it may no longer hold (token replaced or expired), else None
    note: str = ""
    expires: str | None = None

    def describe(self) -> str:
        return f"declared by user for credential '{self.credential}' on {self.declared_at}" + (f" ({self.note})" if self.note else "")


def load(path: Path = PATH) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"credentials": {}}


def save(data: dict, path: Path = PATH) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def for_host(secrets: list[dict], host: str, path: Path = PATH) -> Declaration | None:
    """The declaration for the granted credential that covers ``host``, if any."""
    declared = load(path)["credentials"]
    for s in secrets:
        if not patterns_overlap(s.get("hostPattern") or "", host) or s["name"] not in declared:
            continue
        d = declared[s["name"]]
        stale = None
        if d.get("secretId") and d["secretId"] != s.get("id"):
            stale = "the credential was replaced by a different OneCLI secret since the declaration"
        elif d.get("secretUpdatedAt") and s.get("updatedAt") and str(s["updatedAt"]) > str(d["secretUpdatedAt"]):
            stale = f"the credential was updated ({s['updatedAt']}) after the declaration"
        expires = d.get("expiresAt")
        if not stale and expires and expires < datetime.now(timezone.utc).date().isoformat():
            stale = f"the token expired on {expires} (as declared)"
        return Declaration(s["name"], d.get("scope") or {}, d.get("declaredAt", "?"), stale, d.get("note", ""), expires)
    return None
