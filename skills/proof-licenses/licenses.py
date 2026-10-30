#!/usr/bin/env python3
"""Check the licences of a project's dependencies and write a software bill of materials.

Standard library only, no network. It reads what the project declares and has
installed (npm lockfiles and node_modules, yarn and pnpm lockfiles, Python
site-packages metadata or requirements and pyproject), classifies each licence as
permissive, weak copyleft, strong copyleft, network copyleft or unknown, judges each
against the project's own licence, and writes a CycloneDX 1.5 SBOM.

    python3 licenses.py scan .
    python3 licenses.py scan . --site-packages .venv/Lib/site-packages --project-license MIT
    python3 licenses.py record . --deny AGPL-3.0-only
    python3 licenses.py sbom . --out sbom.cdx.json
    python3 licenses.py status
"""

import argparse
import datetime
import json
import os
import re
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-licenses"

PERMISSIVE, WEAK, STRONG, NETWORK, UNKNOWN = "permissive", "weak-copyleft", "strong-copyleft", "network-copyleft", "unknown"
# For OR, the most permissive licence wins; for AND, the strictest. Unknown loses an OR
# to any known licence, and sits between weak and strong copyleft for AND.
OR_RANK = {PERMISSIVE: 0, WEAK: 1, STRONG: 2, NETWORK: 3, UNKNOWN: 4}
AND_RANK = {PERMISSIVE: 0, WEAK: 1, UNKNOWN: 2, STRONG: 3, NETWORK: 4}

PERMISSIVE_IDS = {"MIT", "MIT-0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "BSD-3-CLAUSE-CLEAR", "BSD-4-CLAUSE", "0BSD",
                  "APACHE-2.0", "APACHE-1.1", "ISC", "UNLICENSE", "ZLIB", "CC0-1.0", "CC-BY-4.0", "CC-BY-3.0",
                  "PYTHON-2.0", "PSF-2.0", "PSF", "BSL-1.0", "WTFPL", "X11", "ARTISTIC-2.0", "BLUEOAK-1.0.0",
                  "UPL-1.0", "NCSA", "POSTGRESQL", "HPND", "PUBLIC-DOMAIN"}
SEVERITY = ["info", "low", "medium", "high", "critical"]
_SKIP_DIRS = {".git", "dist", "build", ".next", ".expo", "__pycache__", "proof"}


# ---------------------------------------------------------------- classify

def normalise(raw):
    """A licence string as an SPDX-ish id or expression; '' when nothing usable."""
    s = " ".join(str(raw or "").split())
    if not s:
        return ""
    low = s.lower()
    if low in ("unlicensed", "none", "unknown", "proprietary", "commercial", "other") or low.startswith("see licen"):
        return ""
    words = [
        (r"^(the )?mit( license)?$", "MIT"),
        (r"^apache( software)?( license)?[ ,]*(version |v)?2(\.0)?$", "Apache-2.0"),
        (r"^apache software license$", "Apache-2.0"),
        (r"^(new |modified |revised )?bsd( 3-clause| three-clause)?( license)?$", "BSD-3-Clause"),
        (r"^(simplified |freebsd )?bsd 2-clause( license)?$", "BSD-2-Clause"),
        (r"^isc( license)?$", "ISC"),
        (r"^mozilla public license 2\.0.*$", "MPL-2.0"),
        (r"^(gnu )?lesser general public license.*$", "LGPL-3.0"),
        (r"^(gnu )?library or lesser general public license.*$", "LGPL-2.1"),
        (r"^(gnu )?affero general public license.*$", "AGPL-3.0"),
        (r"^(gnu )?general public license v?3.*$", "GPL-3.0"),
        (r"^(gnu )?general public license v?2.*$", "GPL-2.0"),
        (r"^(gnu )?general public license.*$", "GPL"),
        (r"^gplv?3\+?$", "GPL-3.0"),
        (r"^gplv?2\+?$", "GPL-2.0"),
        (r"^lgplv?3\+?$", "LGPL-3.0"),
        (r"^lgplv?2(\.1)?\+?$", "LGPL-2.1"),
        (r"^agplv?3\+?$", "AGPL-3.0"),
        (r"^python software foundation license$", "PSF-2.0"),
        (r"^public domain$", "Public-Domain"),
        (r"^the unlicense.*$", "Unlicense"),
        (r"^eclipse public license.*$", "EPL-2.0"),
    ]
    for rx, out in words:
        if re.match(rx, low):
            return out
    return s


def _classify_id(lid):
    u = lid.strip().strip("()").upper()
    if not u:
        return UNKNOWN
    if u.endswith("+"):
        u = u[:-1]
    for suffix in ("-ONLY", "-OR-LATER"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
    if u in PERMISSIVE_IDS or u.startswith("BSD-") or u.startswith("MIT"):
        return PERMISSIVE
    if u.startswith("AGPL") or u.startswith("SSPL"):
        return NETWORK
    if u.startswith("LGPL") or u.startswith("MPL") or u.startswith("EPL") or u.startswith("CDDL") or u.startswith("EUPL"):
        return WEAK
    if u.startswith("GPL"):
        return STRONG
    return UNKNOWN


def _tokens(expr):
    return re.findall(r"\(|\)|[^\s()]+", expr)


def classify(raw):
    """Category of a licence string or SPDX expression (OR, AND, WITH, parentheses)."""
    expr = normalise(raw)
    if not expr:
        return UNKNOWN
    toks = _tokens(expr)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def atom():
        t = peek()
        if t is None:
            return UNKNOWN
        pos[0] += 1
        if t == "(":
            cat = or_expr()
            if peek() == ")":
                pos[0] += 1
            return cat
        cat = _classify_id(t)
        if (peek() or "").upper() == "WITH":  # an exception only loosens a licence; keep the base category
            pos[0] += 2
        return cat

    def and_expr():
        cats = [atom()]
        while (peek() or "").upper() == "AND":
            pos[0] += 1
            cats.append(atom())
        return max(cats, key=lambda c: AND_RANK[c])

    def or_expr():
        cats = [and_expr()]
        while (peek() or "").upper() == "OR":
            pos[0] += 1
            cats.append(and_expr())
        return min(cats, key=lambda c: OR_RANK[c])

    if not re.search(r"\b(OR|AND|WITH)\b", expr, re.I) and " " in expr.strip("()"):
        return _classify_id(expr)  # a free-text name that normalise did not recognise
    return or_expr()


def _project_kind(project_license):
    p = (project_license or "proprietary").strip()
    if p.lower() in ("proprietary", "closed", "commercial", "unlicensed", ""):
        return "proprietary"
    return classify(p)


def severity_for(category, project_license, dev=False):
    """Severity of using a dependency of this category in a project under project_license; None means fine."""
    kind = _project_kind(project_license)
    sev = None
    if kind == "proprietary":
        sev = {NETWORK: "high", STRONG: "medium", WEAK: "low", UNKNOWN: "low"}.get(category)
    elif kind == PERMISSIVE:
        sev = {NETWORK: "medium", STRONG: "medium", UNKNOWN: "low"}.get(category)
    elif kind == WEAK:
        sev = {NETWORK: "medium", STRONG: "medium", UNKNOWN: "low"}.get(category)
    elif kind == STRONG:
        sev = {NETWORK: "low", UNKNOWN: "low"}.get(category)
    else:
        sev = {UNKNOWN: "low"}.get(category)
    if dev and sev and SEVERITY.index(sev) > SEVERITY.index("low"):
        sev = "low"
    return sev


# ---------------------------------------------------------------- collect

def _read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _pkg_license(info):
    lic = info.get("license")
    if isinstance(lic, dict):
        lic = lic.get("type")
    if not lic and isinstance(info.get("licenses"), list):
        names = [x.get("type") if isinstance(x, dict) else x for x in info["licenses"]]
        names = [n for n in names if n]
        lic = " OR ".join(names) if names else None
    return lic or None


def _npm_lock(path):
    out = {}
    try:
        obj = json.loads(_read(path))
    except ValueError:
        return out
    for p, info in (obj.get("packages") or {}).items():
        if not p or not isinstance(info, dict) or "node_modules/" not in p:
            continue
        name = p.split("node_modules/")[-1]
        if info.get("link"):
            continue
        out[name] = {"name": name, "version": info.get("version") or "", "license": _pkg_license(info),
                     "dev": bool(info.get("dev")), "ecosystem": "npm", "source": os.path.basename(path)}

    def walk(deps):
        for name, info in (deps or {}).items():
            if isinstance(info, dict):
                out.setdefault(name, {"name": name, "version": info.get("version") or "", "license": None,
                                      "dev": bool(info.get("dev")), "ecosystem": "npm",
                                      "source": os.path.basename(path)})
                walk(info.get("dependencies"))
    if not obj.get("packages"):
        walk(obj.get("dependencies"))
    return out


def _yarn_lock(path):
    out = {}
    current = []
    for line in _read(path).splitlines():
        if line and not line.startswith(" ") and line.rstrip().endswith(":") and not line.startswith("#"):
            current = []
            for spec in line.rstrip(":").split(","):
                spec = spec.strip().strip('"')
                m = re.match(r"^(@?[^@\s]+)@", spec)
                if m:
                    current.append(m.group(1))
        m = re.match(r'^\s+version:?\s+"?([^"\s]+)"?', line)
        if m and current:
            for name in current:
                out.setdefault(name, {"name": name, "version": m.group(1), "license": None, "dev": False,
                                      "ecosystem": "npm", "source": os.path.basename(path)})
            current = []
    return out


def _pnpm_lock(path):
    out = {}
    in_packages = False
    for line in _read(path).splitlines():
        if re.match(r"^(packages|snapshots):\s*$", line):
            in_packages = line.startswith("packages")
            continue
        if line and not line.startswith(" "):
            in_packages = False
        if not in_packages:
            continue
        m = re.match(r"^\s{2}['\"]?/?(@?[^@\s'\"/]+(?:/[^@\s'\"/]+)?)[@/]([0-9][^:'\"(\s]*)", line)
        if m:
            name, ver = m.group(1), m.group(2)
            out.setdefault(name, {"name": name, "version": ver, "license": None, "dev": False,
                                  "ecosystem": "npm", "source": os.path.basename(path)})
    return out


def _node_modules(root, comps):
    nm = os.path.join(root, "node_modules")
    if not os.path.isdir(nm):
        return
    for entry in os.listdir(nm):
        dirs = [os.path.join(nm, entry)]
        if entry.startswith("@") and os.path.isdir(dirs[0]):
            dirs = [os.path.join(nm, entry, sub) for sub in os.listdir(dirs[0])]
        for d in dirs:
            pj = os.path.join(d, "package.json")
            if not os.path.isfile(pj):
                continue
            try:
                info = json.loads(_read(pj))
            except ValueError:
                continue
            name = info.get("name") or os.path.relpath(d, nm).replace(os.sep, "/")
            lic = _pkg_license(info)
            c = comps.get(name)
            if c is None:
                comps[name] = {"name": name, "version": info.get("version") or "", "license": lic, "dev": False,
                               "ecosystem": "npm", "source": "node_modules"}
            elif not c.get("license") and lic:
                c["license"] = lic


CLASSIFIER_MAP = [
    ("MIT License", "MIT"), ("BSD License", "BSD-3-Clause"), ("Apache Software License", "Apache-2.0"),
    ("ISC License", "ISC"), ("Mozilla Public License 2.0", "MPL-2.0"),
    ("GNU Affero General Public License", "AGPL-3.0"),
    ("GNU Lesser General Public License v3", "LGPL-3.0"), ("GNU Lesser General Public License v2", "LGPL-2.1"),
    ("GNU Library or Lesser General Public License", "LGPL-2.1"),
    ("GNU General Public License v3", "GPL-3.0"), ("GNU General Public License v2", "GPL-2.0"),
    ("GNU General Public License", "GPL"), ("Python Software Foundation License", "PSF-2.0"),
    ("The Unlicense", "Unlicense"), ("Public Domain", "Public-Domain"), ("Eclipse Public License", "EPL-2.0"),
]


def _metadata_license(text):
    expr = lic = None
    classifiers = []
    for line in text.split("\n\n", 1)[0].splitlines():
        if line.startswith("License-Expression:"):
            expr = line.split(":", 1)[1].strip()
        elif line.startswith("License:"):
            lic = line.split(":", 1)[1].strip()
        elif line.startswith("Classifier: License ::"):
            classifiers.append(line.split("::", 1)[1].strip())
    if expr:
        return expr
    if lic and len(lic) <= 60 and "\n" not in lic and lic.upper() not in ("UNKNOWN", ""):
        return lic
    ids = []
    for c in classifiers:
        for needle, spdx in CLASSIFIER_MAP:
            if needle in c and spdx not in ids:
                ids.append(spdx)
                break
    if ids:
        return " OR ".join(ids)
    return lic or None


def _site_packages(path):
    out = {}
    if not path or not os.path.isdir(path):
        return out
    for entry in sorted(os.listdir(path)):
        if not entry.endswith(".dist-info"):
            continue
        meta = os.path.join(path, entry, "METADATA")
        if not os.path.isfile(meta):
            continue
        text = _read(meta)
        name = re.search(r"^Name:\s*(.+)$", text, re.M)
        ver = re.search(r"^Version:\s*(.+)$", text, re.M)
        if not name:
            continue
        key = name.group(1).strip()
        out[key.lower()] = {"name": key, "version": ver.group(1).strip() if ver else "",
                            "license": _metadata_license(text), "dev": False, "ecosystem": "pypi",
                            "source": "site-packages"}
    return out


def _requirements(path):
    out = {}
    for line in _read(path).splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9._\-]+)(?:\[[^\]]*\])?\s*(?:==\s*([0-9A-Za-z.\-+]+))?", line)
        if m:
            out[m.group(1).lower()] = {"name": m.group(1), "version": m.group(2) or "", "license": None,
                                       "dev": False, "ecosystem": "pypi", "source": os.path.basename(path)}
    return out


def _pyproject(path):
    out = {}
    text = _read(path)
    m = re.search(r"^\s*dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
    if not m:
        return out
    for spec in re.findall(r"[\"']([^\"']+)[\"']", m.group(1)):
        n = re.match(r"^([A-Za-z0-9._\-]+)\s*(?:\[[^\]]*\])?\s*(?:==\s*([0-9A-Za-z.\-+]+))?", spec)
        if n:
            out[n.group(1).lower()] = {"name": n.group(1), "version": n.group(2) or "", "license": None,
                                       "dev": False, "ecosystem": "pypi", "source": "pyproject.toml"}
    return out


def collect(roots, site_packages=None):
    """All components found under the given project folders, deduplicated by ecosystem and name."""
    comps = {}
    for root in roots:
        npm, py = {}, {}
        found = {n: os.path.join(root, n) for n in ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
                                                    "pnpm-lock.yaml", "requirements.txt", "pyproject.toml")
                 if os.path.isfile(os.path.join(root, n))}
        for n in ("package-lock.json", "npm-shrinkwrap.json"):
            if n in found:
                npm.update(_npm_lock(found[n]))
        if "yarn.lock" in found:
            for k, v in _yarn_lock(found["yarn.lock"]).items():
                npm.setdefault(k, v)
        if "pnpm-lock.yaml" in found:
            for k, v in _pnpm_lock(found["pnpm-lock.yaml"]).items():
                npm.setdefault(k, v)
        _node_modules(root, npm)
        if "pyproject.toml" in found:
            py.update(_pyproject(found["pyproject.toml"]))
        if "requirements.txt" in found:
            py.update(_requirements(found["requirements.txt"]))
        for k, v in npm.items():
            comps.setdefault(("npm", k.lower()), v)
        for k, v in py.items():
            comps.setdefault(("pypi", k.lower()), v)
    for k, v in _site_packages(site_packages).items():
        key = ("pypi", k)
        if key in comps and comps[key].get("license"):
            continue
        prev = comps.get(key)
        if prev and prev.get("version") and not v.get("version"):
            v["version"] = prev["version"]
        comps[key] = v
    out = sorted(comps.values(), key=lambda c: (c["ecosystem"], c["name"].lower()))
    for c in out:
        c["license"] = normalise(c.get("license")) or None
        c["category"] = classify(c.get("license"))
    return out


# ---------------------------------------------------------------- judge

def _matches(lic, names):
    if not lic:
        return False
    ids = {t.upper() for t in _tokens(lic) if t.upper() not in ("AND", "OR", "WITH", "(", ")")}
    return any(n.upper() in ids or (lic.upper() == n.upper()) for n in names)


def judge(comps, project_license="proprietary", allow=(), deny=()):
    """Return [(component, severity, reason)] for every component that needs attention."""
    out = []
    for c in comps:
        lic = c.get("license")
        if deny and _matches(lic, deny):
            out.append((c, "high", "on the deny list"))
            continue
        if allow and _matches(lic, allow):
            continue
        sev = severity_for(c["category"], project_license, c["dev"])
        if sev:
            out.append((c, sev, c["category"]))
    return out


def _purl(c):
    if c["ecosystem"] == "npm":
        name = c["name"]
        if name.startswith("@") and "/" in name:
            scope, base = name[1:].split("/", 1)
            name = "%40" + scope + "/" + base
        base = "pkg:npm/" + name
    else:
        base = "pkg:pypi/" + re.sub(r"[-_.]+", "-", c["name"]).lower()
    return base + ("@" + c["version"] if c.get("version") else "")


def sbom(comps, project_name):
    components = []
    for c in comps:
        item = {"type": "library", "name": c["name"], "version": c.get("version") or "", "purl": _purl(c),
                "bom-ref": _purl(c)}
        lic = c.get("license")
        if lic:
            if re.search(r"\b(OR|AND|WITH)\b", lic):
                item["licenses"] = [{"expression": lic}]
            elif re.match(r"^[A-Za-z0-9.\-+]+$", lic):
                item["licenses"] = [{"license": {"id": lic}}]
            else:
                item["licenses"] = [{"license": {"name": lic}}]
        if c.get("dev"):
            item["scope"] = "excluded"
        components.append(item)
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": "urn:uuid:%s" % uuid.uuid4(),
            "version": 1,
            "metadata": {"timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "tools": {"components": [{"type": "application", "name": SKILL}]},
                         "component": {"type": "application", "name": project_name, "bom-ref": "root"}},
            "components": components}


# ---------------------------------------------------------------- commands

def _scan(a):
    roots = a.paths or ["."]
    for r in roots:
        if not os.path.isdir(r):
            raise SystemExit("no folder at %s" % r)
    comps = collect(roots, a.site_packages)
    issues = judge(comps, a.project_license, a.allow, a.deny)
    return roots, comps, issues


def _counts(comps):
    out = dict((k, 0) for k in (PERMISSIVE, WEAK, STRONG, NETWORK, UNKNOWN))
    for c in comps:
        out[c["category"]] += 1
    return out


def cmd_scan(a):
    roots, comps, issues = _scan(a)
    if a.json:
        print(json.dumps({"project_license": a.project_license, "counts": _counts(comps), "components": comps,
                          "issues": [{"name": c["name"], "version": c["version"], "license": c["license"],
                                      "severity": s, "why": w, "dev": c["dev"]} for c, s, w in issues]}, indent=2))
        return 0
    counts = _counts(comps)
    print("%d dependencies (project licence: %s)." % (len(comps), a.project_license))
    for k, n in counts.items():
        print("  %-17s %d" % (k, n))
    for c, sev, why in sorted(issues, key=lambda x: -SEVERITY.index(x[1]))[:a.show]:
        print("  %s [%s] %s %s: %s%s (%s)" % (ledger.BAD, sev, c["name"], c["version"], c["license"] or "no licence",
                                             " (dev)" if c["dev"] else "", why))
    if not issues:
        print("%s nothing to act on." % ledger.OK)
    return 0


CHECKS = [("inventory", "Dependencies found and read"),
          ("network-copyleft", "No network-copyleft (AGPL, SSPL) dependency where it conflicts"),
          ("strong-copyleft", "No strong-copyleft (GPL) dependency where it conflicts"),
          ("weak-copyleft", "Weak-copyleft (LGPL, MPL) dependencies reviewed"),
          ("unknown", "Every dependency has a known licence"),
          ("policy", "The allow and deny lists hold"),
          ("sbom", "A software bill of materials was written")]
FIXES = {
    "network-copyleft": "Replace it, keep it out of anything you distribute or serve, or get a commercial licence.",
    "strong-copyleft": "Replace it with a permissively licensed alternative, or confirm with counsel how it is used.",
    "weak-copyleft": "Keep it dynamically linked and unmodified, or publish your changes to it; check how a mobile bundle links it.",
    "unknown": "Find the real licence (repository LICENSE file) before shipping, or replace the package.",
    "policy": "Remove the denied dependency or change the policy deliberately.",
}


def cmd_record(a):
    roots, comps, issues = _scan(a)
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    bom_path = os.path.join(ev_dir, "sbom.cdx.json")
    with open(bom_path, "w", encoding="utf-8") as fh:
        json.dump(sbom(comps, os.path.basename(os.path.abspath(roots[0])) or "project"), fh, indent=2)
    scan_path = os.path.join(ev_dir, "licenses.json")
    with open(scan_path, "w", encoding="utf-8") as fh:
        json.dump({"project_license": a.project_license, "counts": _counts(comps), "components": comps}, fh, indent=2)
    rel = lambda p: os.path.relpath(p, ledger.root(a.dir)).replace(os.sep, "/")
    ev = [rel(scan_path), rel(bom_path)]
    ledger.init_coverage(a.dir, SKILL, CHECKS)
    if not comps:
        for cid, _ in CHECKS:
            ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note="no lockfile, manifest or installed packages found")
        print("%s no dependencies found under %s." % (ledger.BAD, ", ".join(roots)))
        return 0
    for cid, _ in CHECKS:
        if cid == "policy" and not (a.allow or a.deny):
            ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note="no --allow or --deny list given")
        else:
            ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=ev)
    groups = {}
    for c, sev, why in issues:
        cid = "policy" if why == "on the deny list" else c["category"]
        g = groups.setdefault(cid, {"sev": sev, "items": []})
        if SEVERITY.index(sev) > SEVERITY.index(g["sev"]):
            g["sev"] = sev
        g["items"].append(c)
    keep = set()
    for cid, g in groups.items():
        fid = "licenses-%s" % cid
        keep.add(fid)
        path = ledger.finding_path(a.dir, SKILL, fid)
        if os.path.exists(path) and ledger.load_finding(a.dir, SKILL, fid)["status"] == "false_positive":
            continue
        names = ", ".join("%s %s (%s%s)" % (c["name"], c["version"], c["license"] or "no licence",
                                            ", dev" if c["dev"] else "") for c in g["items"][:20])
        more = len(g["items"]) - 20
        title = "%d dependenc%s %s for a %s project" % (
            len(g["items"]), "y is" if len(g["items"]) == 1 else "ies are",
            "on the deny list" if cid == "policy" else cid.replace("-", " "), a.project_license)
        f = ledger.new_finding(SKILL, title[:160], g["sev"], id=fid, category="licence:%s" % cid, evidence=ev,
                               description=(names + (" and %d more" % more if more > 0 else ""))[:1000],
                               fix=FIXES.get(cid, ""), location=roots[0])
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    for old in ledger.load_findings(a.dir, SKILL):
        if old["id"] not in keep and old["status"] != "false_positive":
            os.remove(ledger.finding_path(a.dir, SKILL, old["id"]))
    print("%s recorded %d dependencies, %d finding(s); SBOM at %s." % (
        ledger.OK if not groups else ledger.BAD, len(comps), len(groups), rel(bom_path)))
    return 0


def cmd_sbom(a):
    roots, comps, _ = _scan(a)
    doc = sbom(comps, os.path.basename(os.path.abspath(roots[0])) or "project")
    out = a.out or os.path.join(ledger.evidence_dir(a.dir, SKILL), "sbom.cdx.json")
    ledger.guard_out(out, a.dir, a.force, ours='"bomFormat": "CycloneDX"')
    parent = os.path.dirname(os.path.abspath(out))
    os.makedirs(parent, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    print("%s wrote a CycloneDX 1.5 SBOM with %d components to %s" % (ledger.OK, len(comps), out))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no licence check recorded yet. Run: licenses.py record .")
        return 0
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, func, hlp in (("scan", cmd_scan, "list dependencies and licence issues"),
                            ("record", cmd_record, "write findings, coverage and the SBOM to the ledger"),
                            ("sbom", cmd_sbom, "write a CycloneDX SBOM")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("paths", nargs="*", help="project folders (default: here)")
        p.add_argument("--site-packages", help="an installed Python environment's site-packages folder")
        p.add_argument("--project-license", default="proprietary",
                       help="your project's licence, e.g. MIT or GPL-3.0-only (default proprietary)")
        p.add_argument("--allow", action="append", default=[], help="a licence id that is always fine (repeatable)")
        p.add_argument("--deny", action="append", default=[], help="a licence id that is never allowed (repeatable)")
        if name == "scan":
            p.add_argument("--json", action="store_true")
            p.add_argument("--show", type=int, default=30, help="issues to print")
        if name == "sbom":
            p.add_argument("--out", help="where to write it (default: the evidence folder)")
            p.add_argument("--force", action="store_true", help="overwrite a file Proof did not write")
        p.set_defaults(func=func)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
