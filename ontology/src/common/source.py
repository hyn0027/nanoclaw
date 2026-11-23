"""What a model pinned to a source checkout was reviewed against.

A module that models a checkout (``ar:sourceRepositoryPath``) pins ``ar:sourceContentHash``:
a hash of every file in the checkout except ``ontology/`` itself, as it is on disk (tracked and
untracked, ignored files excluded). A content hash, not a commit: the ontology lives inside the
checkout it models, so the pin is committed together with the code it describes, and only a
change to that code makes it stale.
"""

import hashlib
import subprocess
from pathlib import Path

EXCLUDED = ":(exclude)ontology"


def source_files(repo: Path) -> list[str]:
    out = subprocess.check_output(["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                                   "--", ".", EXCLUDED])
    return sorted(p for p in set(out.decode().split("\0")) if p and (repo / p).is_file())


def source_hash(repo: Path) -> str:
    """sha256 over the source files' paths and git blob ids (their content on disk)."""
    files = source_files(repo)
    blobs = subprocess.run(["git", "-C", str(repo), "hash-object", "--stdin-paths"], input="\n".join(files),
                           capture_output=True, text=True, check=True).stdout.split()
    return hashlib.sha256("".join(f"{p}\0{b}\n" for p, b in zip(files, blobs)).encode()).hexdigest()

