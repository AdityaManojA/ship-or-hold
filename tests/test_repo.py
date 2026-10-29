"""Repo-wide checks: structure, SKILL.md shape, flag existence, and originality.

These enforce the rules in CONTRIBUTING.md across every skill, so a new skill that
breaks a convention fails here.
"""

import os
import re
import unittest

from _load import ROOT, SKILLS_DIR

# Phase 1 skills. Later phases add to this list as they land.
EXPECTED = {"proof-core", "proof-report", "proof-swarm", "proof-plan", "proof-doctor",
            "proof-web-e2e", "proof-sec-web"}

# Names and command prefixes of the projects Proof took inspiration from. None may
# appear anywhere under skills/ or tests/ (decision 5: nothing traces back).
FORBIDDEN = [
    "mirofish", "betta fish", "bettafish", "founder-board", "founder-cfo", "founder-skill",
    "founder-consumer", "ponytail", "cloudflare", "trail of bits", "trailofbits",
    "replica-skill", "arena-skill", "claude-seo", "geo-seo-claude", "seomachine",
    "lighthouse-ci", "schincariol", "opusjake",
]


def skill_dirs():
    return sorted(d for d in os.listdir(SKILLS_DIR)
                  if os.path.isdir(os.path.join(SKILLS_DIR, d)) and d.startswith("proof-"))


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


SHIPPED = (os.path.join(ROOT, "skills"), os.path.join(ROOT, "tests"))


def text_files(roots=None):
    for root in (roots or [ROOT]):
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
            for f in files:
                if f.endswith((".py", ".md", ".json", ".txt", ".yml", ".yaml")):
                    yield os.path.join(base, f)


def imported_top_modules(path):
    """Top-level module names actually imported by a Python file (via ast, so
    import-looking lines inside string templates do not count)."""
    import ast
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


class Repo(unittest.TestCase):
    def test_expected_skills_present(self):
        present = set(skill_dirs())
        missing = EXPECTED - present
        self.assertFalse(missing, "missing skills: %s" % ", ".join(sorted(missing)))

    def test_every_skill_has_skill_md(self):
        for d in skill_dirs():
            self.assertTrue(os.path.isfile(os.path.join(SKILLS_DIR, d, "SKILL.md")), "%s has no SKILL.md" % d)

    def test_frontmatter_name_matches_folder(self):
        for d in skill_dirs():
            fm, text = frontmatter(os.path.join(SKILLS_DIR, d, "SKILL.md"))
            self.assertIn("name: %s\n" % d, fm + "\n", "%s frontmatter name" % d)
            self.assertRegex(fm, r"description:", d)
            self.assertGreater(len(fm), 300, "%s description is too thin" % d)
            self.assertGreater(len(text.splitlines()), 60, "%s SKILL.md is too short" % d)

    def test_skill_md_has_honest_limits_and_output(self):
        for d in skill_dirs():
            if d == "proof-core":
                continue
            _, text = frontmatter(os.path.join(SKILLS_DIR, d, "SKILL.md"))
            low = text.lower()
            self.assertIn("honest limits", low, "%s has no Honest limits section" % d)
            self.assertIn("output", low, "%s names no Output / next" % d)

    def test_named_tools_exist(self):
        for d in skill_dirs():
            path = os.path.join(SKILLS_DIR, d, "SKILL.md")
            _, text = frontmatter(path)
            for tool in re.findall(r"\$\{CLAUDE_SKILL_DIR\}/([\w.\-/]+\.(?:py|md|json))", text):
                self.assertTrue(os.path.exists(os.path.join(SKILLS_DIR, d, tool)),
                                "%s names %s which does not exist" % (d, tool))

    def test_tools_are_stdlib_only(self):
        # No third-party imports in our tools. A short allowlist of stdlib modules
        # plus our own sibling modules. Uses ast, so import-looking lines inside
        # generated-script templates do not count.
        allowed = {"os", "sys", "re", "json", "argparse", "subprocess", "socket", "time",
                   "signal", "random", "datetime", "importlib", "math", "hashlib", "io",
                   "contextlib", "shutil", "tempfile", "collections", "itertools", "glob",
                   "pathlib", "typing", "zipfile", "plistlib", "xml", "csv", "statistics",
                   "ledger", "num", "validate", "urllib", "html", "textwrap", "difflib",
                   "base64", "binascii", "unicodedata", "fnmatch", "string", "ast",
                   "tokenize", "plistlib"}
        for d in skill_dirs():
            for name in os.listdir(os.path.join(SKILLS_DIR, d)):
                if not name.endswith(".py"):
                    continue
                for top in imported_top_modules(os.path.join(SKILLS_DIR, d, name)):
                    self.assertIn(top, allowed, "%s/%s imports %r (not stdlib)" % (d, name, top))

    def test_no_em_dash_in_shipped_prose(self):
        # Our shipped skills and tests use plain hyphens. The top-level planning
        # docs (PLAN.md etc.) are not shipped with the skills and are exempt.
        # EM is built at runtime so this file does not contain the character it bans.
        EM = chr(0x2014)
        for path in text_files(SHIPPED):
            with open(path, encoding="utf-8") as fh:
                self.assertNotIn(EM, fh.read(), "em dash in %s" % os.path.relpath(path, ROOT))

    def test_originality_no_source_names(self):
        this_file = os.path.abspath(__file__)
        for path in text_files(SHIPPED):
            if os.path.abspath(path) == this_file:
                continue  # this file holds the forbidden list on purpose
            rel = os.path.relpath(path, ROOT)
            with open(path, encoding="utf-8") as fh:
                low = fh.read().lower()
            for bad in FORBIDDEN:
                self.assertNotIn(bad, low, "%s mentions %r (keep credits in the README only)" % (rel, bad))


if __name__ == "__main__":
    unittest.main()
