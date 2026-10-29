"""Helpers for the test suite: find the repo, import a tool by path, make a temp proof/."""

import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT, "skills")
CORE_DIR = os.path.join(SKILLS_DIR, "proof-core")

if CORE_DIR not in sys.path:
    sys.path.insert(0, CORE_DIR)


def load_tool(skill, filename):
    """Import skills/<skill>/<filename> as a module, without needing a package."""
    path = os.path.join(SKILLS_DIR, skill, filename)
    name = "proof_%s_%s" % (skill.replace("-", "_"), os.path.splitext(filename)[0])
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TempProof:
    """A throwaway proof/ folder. Use as a context manager; ``.dir`` is the path."""

    def __enter__(self):
        self.base = tempfile.mkdtemp(prefix="proof-test-")
        self.dir = os.path.join(self.base, "proof")
        os.makedirs(self.dir)
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.base, ignore_errors=True)

    def write(self, rel, content=b"x"):
        """Create a file under proof/ (default non-empty) and return its ledger-relative path.

        Text is written with newline translation off, so a captured HTTP response
        keeps its exact line endings.
        """
        full = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if isinstance(content, bytes):
            with open(full, "wb") as fh:
                fh.write(content)
        else:
            with open(full, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        return rel.replace(os.sep, "/")
