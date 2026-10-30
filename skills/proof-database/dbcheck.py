#!/usr/bin/env python3
"""Review a Postgres or Supabase database's safety from saved files. Offline, read-only.

Standard library only, no network, and it never connects to a database. You run
the read-only queries it prints (psql or the Supabase SQL editor), save the results
as JSON or CSV, and it checks them: tables with row-level security off, policies
that let anyone write, public storage buckets, the service-role key reachable from
client code, risky migrations, and schema smells. Every hit points to a file.

    python3 dbcheck.py queries
    python3 dbcheck.py scan --rls rls.json --policies policies.json --buckets buckets.json
    python3 dbcheck.py scan --migrations backend/alembic/versions --client-src frontend
    python3 dbcheck.py record --rls rls.json --policies policies.json --schema schema.sql
    python3 dbcheck.py status
"""

import argparse
import base64
import csv
import io
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-database"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", ".nuxt", "dist", "build",
             ".expo", "Pods", "proof", "coverage", ".turbo", ".vercel"}
CLIENT_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro", ".dart", ".swift",
              ".kt", ".java", ".html", ".json"}

# id, title, needs (which inputs make it applicable), fix
CHECKS = [
    ("rls-off", "Every public table has row-level security on", ("rls",),
     "Enable row-level security on the table (ALTER TABLE ... ENABLE ROW LEVEL SECURITY) and add policies "
     "for the access each role really needs."),
    ("rls-permissive", "No policy lets anyone read or write everything", ("policies",),
     "Replace the always-true condition with one tied to the user, for example auth.uid() = user_id."),
    ("public-buckets", "Storage buckets are not public unless meant to be", ("buckets",),
     "Make the bucket private and hand out signed URLs, or confirm it only holds public files."),
    ("service-role-in-client", "The service-role key never reaches client code", ("client",),
     "Remove the key from client code, rotate it now, and call privileged operations from the server."),
    ("migrations", "Migrations do not destroy or lock data without a plan", ("migrations",),
     "Confirm each change is intended, back up first, and use a safe pattern (expand and contract, "
     "CREATE INDEX CONCURRENTLY, a default before NOT NULL)."),
    ("schema", "The schema has keys, indexed foreign keys and no plain-text secrets", ("schema",),
     "Add a primary key, index foreign key columns, and store only hashes of passwords and keys."),
]
CHECK_BY_ID = dict((c[0], c) for c in CHECKS)

PRIVATE_BUCKET = re.compile(r"(?i)(document|upload|private|export|backup|invoice|receipt|report|attachment|"
                            r"kyc|passport|id-?card|medical|statement|file)")
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.(eyJ[A-Za-z0-9_-]{8,})\.[A-Za-z0-9_-]{8,}")
PUBLIC_SERVICE_VAR = re.compile(r"\b(?:NEXT_PUBLIC|EXPO_PUBLIC|VITE|REACT_APP|PUBLIC|NUXT_PUBLIC)_[A-Z0-9_]*SERVICE_?ROLE[A-Z0-9_]*")
SERVICE_VAR = re.compile(r"\bSUPABASE_SERVICE_ROLE[A-Z0-9_]*|\bSERVICE_ROLE_KEY\b")
SECRET_COLUMN = re.compile(r"(?i)^(password|passwd|pass|secret|api_?key|access_?token|refresh_?token|private_?key|"
                           r"client_?secret)$")
HASHED = re.compile(r"(?i)(hash|digest|encrypted|enc_|_enc|cipher|bcrypt|argon)")

QUERIES = [
    ("rls.json", "Tables and whether row-level security is on",
     "SELECT n.nspname AS schemaname, c.relname AS tablename, c.relrowsecurity AS rowsecurity\n"
     "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace\n"
     "WHERE c.relkind IN ('r', 'p') AND n.nspname = 'public'\nORDER BY 2;"),
    ("policies.json", "Row-level security policies",
     "SELECT schemaname, tablename, policyname, cmd, roles, qual, with_check\n"
     "FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, policyname;"),
    ("buckets.json", "Supabase storage buckets",
     "SELECT id, name, public FROM storage.buckets ORDER BY id;"),
    ("schema.json", "Columns, primary keys, foreign keys and indexes (or use pg_dump --schema-only)",
     "SELECT json_build_object(\n"
     "  'columns', (SELECT json_agg(json_build_object('table_name', table_name, 'column_name', column_name,\n"
     "              'data_type', data_type)) FROM information_schema.columns WHERE table_schema = 'public'),\n"
     "  'primary_keys', (SELECT json_agg(json_build_object('table_name', tc.table_name))\n"
     "              FROM information_schema.table_constraints tc\n"
     "              WHERE tc.table_schema = 'public' AND tc.constraint_type = 'PRIMARY KEY'),\n"
     "  'foreign_keys', (SELECT json_agg(json_build_object('table_name', kcu.table_name,\n"
     "              'column_name', kcu.column_name))\n"
     "              FROM information_schema.table_constraints tc\n"
     "              JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name\n"
     "              WHERE tc.table_schema = 'public' AND tc.constraint_type = 'FOREIGN KEY'),\n"
     "  'indexes', (SELECT json_agg(json_build_object('tablename', tablename, 'indexdef', indexdef))\n"
     "              FROM pg_indexes WHERE schemaname = 'public'));"),
]


# ---------------------------------------------------------------- reading

def _read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def load_rows(path):
    """Rows from a JSON list, a JSON object holding one list, or a CSV with a header row."""
    text = _read(path)
    stripped = text.lstrip()
    if stripped[:1] in ("[", "{"):
        data = json.loads(text)
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            data = lists[0] if len(lists) == 1 else data.get("rows", [])
        if not isinstance(data, list):
            raise SystemExit("%s: expected a list of rows" % path)
        return [r for r in data if isinstance(r, dict)]
    return list(csv.DictReader(io.StringIO(text)))


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("t", "true", "1", "yes", "y", "on")


def _get(row, *keys):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _roles(v):
    if isinstance(v, list):
        return [str(x).strip().lower() for x in v]
    return [x.strip().strip('"').lower() for x in str(v or "").strip("{}").split(",") if x.strip()]


def mask(s):
    s = str(s)
    return s[:4] + "..." if len(s) > 4 else "..."


# ---------------------------------------------------------------- checks

def check_rls(rows):
    hits = []
    for r in rows:
        schema = str(_get(r, "schemaname", "schema", "table_schema") or "public").lower()
        table = _get(r, "tablename", "table_name", "relname", "table")
        if schema != "public" or not table:
            continue
        if not _truthy(_get(r, "rowsecurity", "relrowsecurity", "rls_enabled", "rls") or False):
            hits.append({"severity": "high", "key": table, "title": "Row-level security is off on public.%s" % table,
                         "detail": "With RLS off, anyone holding the anon key can read and change every row "
                                   "through the Supabase API."})
    return hits


def _always_true(expr):
    e = re.sub(r"[\s()]", "", str(expr or "")).lower()
    return e in ("true", "1=1", "'t'", "'true'::boolean")


def check_policies(rows):
    hits = []
    for r in rows:
        table = _get(r, "tablename", "table_name", "table") or "?"
        name = _get(r, "policyname", "name") or "policy"
        cmd = str(_get(r, "cmd", "command") or "ALL").upper()
        roles = _roles(_get(r, "roles"))
        open_roles = [x for x in roles if x in ("public", "anon")] or ([] if roles else ["public"])
        if not open_roles:
            continue
        loose = [k for k in ("qual", "with_check") if _always_true(r.get(k))]
        if not loose:
            continue
        if cmd in ("SELECT", "R"):
            sev, note = "medium", "anyone can read every row; fine for truly public data, a leak otherwise"
        else:
            sev, note = "high", "anyone can %s any row" % ("change" if cmd != "INSERT" else "insert")
        hits.append({"severity": sev, "key": "%s-%s" % (table, name),
                     "title": "Policy %r on %s lets %s %s with a condition of true" % (name, table, "/".join(open_roles), cmd),
                     "detail": note})
    return hits


def check_buckets(rows):
    hits = []
    for r in rows:
        bid = _get(r, "id", "name", "bucket") or "?"
        if not _truthy(_get(r, "public", "is_public") or False):
            continue
        private_looking = bool(PRIVATE_BUCKET.search(str(bid)))
        hits.append({"severity": "high" if private_looking else "medium", "key": bid,
                     "title": "Storage bucket %r is public" % bid,
                     "detail": ("its name suggests private files; anyone with a file's URL can open it"
                                if private_looking else "anyone with a file's URL can open it; fine for public assets")})
    return hits


def _jwt_role(token_payload):
    try:
        pad = "=" * (-len(token_payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(token_payload + pad).decode("utf-8", "replace"))
        return str(data.get("role", "")) if isinstance(data, dict) else ""
    except (ValueError, TypeError):
        return ""


def _walk(root, exts=None, names=None):
    if os.path.isfile(root):
        yield root
        return
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if (exts and ext in exts) or (names and (f.startswith(".env") or f in names)):
                yield os.path.join(base, f)


def _rel(path):
    try:
        return os.path.relpath(path).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


def check_client(dirs):
    hits = []
    for root in dirs:
        for path in _walk(root, CLIENT_EXT, names={".env"}):
            if os.path.getsize(path) > 2 * 1024 * 1024:
                continue
            for n, line in enumerate(_read(path).splitlines(), 1):
                for m in JWT.finditer(line):
                    if _jwt_role(m.group(1)) == "service_role":
                        hits.append({"severity": "critical", "key": "%s:%d" % (_rel(path), n), "location": "%s:%d" % (_rel(path), n),
                                     "title": "A service-role key is in client code (%s)" % mask(m.group(0)),
                                     "detail": "it bypasses every row-level security policy; anyone who opens the "
                                               "app or page can copy it"})
                m = PUBLIC_SERVICE_VAR.search(line)
                if m:
                    hits.append({"severity": "critical", "key": "%s:%d" % (_rel(path), n), "location": "%s:%d" % (_rel(path), n),
                                 "title": "%s is exposed to the browser or app bundle" % m.group(0),
                                 "detail": "public-prefixed variables are compiled into the client"})
                    continue
                m = SERVICE_VAR.search(line)
                if m:
                    hits.append({"severity": "medium", "key": "%s:%d" % (_rel(path), n), "location": "%s:%d" % (_rel(path), n),
                                 "title": "%s is referenced in client-side code" % m.group(0),
                                 "detail": "confirm this file only runs on the server (an API route or server "
                                           "action), never in the browser or the app"})
    return hits


def _calls(text, name):
    """Yield (offset, call text) for each name( ... ) call, balancing parentheses."""
    for m in re.finditer(r"\b%s\s*\(" % re.escape(name), text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield m.start(), text[m.start():i + 1]


SQL_RULES = [
    (re.compile(r"(?i)\bDROP\s+TABLE\b"), "drops a table"),
    (re.compile(r"(?i)\bDROP\s+COLUMN\b"), "drops a column"),
    (re.compile(r"(?i)\bALTER\s+COLUMN\s+\S+\s+(?:SET\s+DATA\s+)?TYPE\b"), "changes a column's type (rewrites the table)"),
    (re.compile(r"(?i)\bRENAME\s+COLUMN\b"), "renames a column (breaks code still using the old name)"),
    (re.compile(r"(?i)\bSET\s+NOT\s+NULL\b"), "makes a column NOT NULL (fails or locks if rows hold nulls)"),
    (re.compile(r"(?i)\bTRUNCATE\b"), "deletes every row in a table"),
]
ADD_NOT_NULL = re.compile(r"(?i)\bADD\s+(?:COLUMN\s+)?\S+\s+[^,;]*\bNOT\s+NULL\b")
CREATE_INDEX = re.compile(r"(?i)\bCREATE\s+(?:UNIQUE\s+)?INDEX\b(?!\s+CONCURRENTLY)")


def _sql_hits(sql, path, base_line):
    hits = []
    for k, line in enumerate(sql.splitlines()):
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        n = base_line + k
        for rx, what in SQL_RULES:
            if rx.search(line):
                hits.append((n, what, stripped))
        if ADD_NOT_NULL.search(line) and not re.search(r"(?i)\bDEFAULT\b", line):
            hits.append((n, "adds a NOT NULL column without a default (fails on a table with rows)", stripped))
        if CREATE_INDEX.search(line):
            hits.append((n, "creates an index without CONCURRENTLY (blocks writes while it builds)", stripped))
    return [{"severity": "medium", "key": "%s:%d" % (_rel(path), n), "location": "%s:%d" % (_rel(path), n),
             "title": "Migration %s: %s" % (os.path.basename(path), what), "detail": text[:160]}
            for n, what, text in hits]


def check_migrations(dirs):
    hits = []
    for root in dirs:
        for path in _walk(root, {".py", ".sql"}):
            text = _read(path)
            if path.endswith(".sql"):
                hits += _sql_hits(text, path, 1)
                continue
            # Alembic: only the upgrade step runs forward; downgrade() is expected to drop things.
            cut = re.search(r"^def\s+downgrade\s*\(", text, re.M)
            up = text[:cut.start()] if cut else text
            line_of = lambda off: up.count("\n", 0, off) + 1
            found = []
            for off, call in _calls(up, "op.drop_table"):
                found.append((line_of(off), "drops a table", call))
            for off, call in _calls(up, "op.drop_column"):
                found.append((line_of(off), "drops a column", call))
            for off, call in _calls(up, "op.alter_column"):
                if re.search(r"\btype_\s*=", call):
                    found.append((line_of(off), "changes a column's type (rewrites the table)", call))
                if re.search(r"\bnew_column_name\s*=", call):
                    found.append((line_of(off), "renames a column (breaks code still using the old name)", call))
                if re.search(r"\bnullable\s*=\s*False", call) and "server_default" not in call:
                    found.append((line_of(off), "makes a column NOT NULL without a server default", call))
            for off, call in _calls(up, "op.add_column"):
                if re.search(r"\bnullable\s*=\s*False", call) and "server_default" not in call:
                    found.append((line_of(off), "adds a NOT NULL column without a server default", call))
            for off, call in _calls(up, "op.create_index"):
                if "postgresql_concurrently" not in call:
                    found.append((line_of(off), "creates an index without CONCURRENTLY (blocks writes while it builds)", call))
            for off, call in _calls(up, "op.execute"):
                hits += _sql_hits(call, path, line_of(off))
            for n, what, call in found:
                hits.append({"severity": "medium", "key": "%s:%d" % (_rel(path), n), "location": "%s:%d" % (_rel(path), n),
                             "title": "Migration %s: %s" % (os.path.basename(path), what),
                             "detail": " ".join(call.split())[:160]})
    return hits


def parse_schema(path):
    """{tables: {name: {columns: {col: type}, pk: bool}}, fks: [(table, col)], indexed: {(table, col)}}"""
    text = _read(path)
    tables, fks, indexed = {}, [], set()
    if text.lstrip()[:1] in ("{", "["):
        data = json.loads(text)
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        if isinstance(data, dict) and len(data) == 1 and isinstance(list(data.values())[0], dict):
            data = list(data.values())[0]  # a psql json_build_object result wrapped in a column
        for c in data.get("columns") or []:
            t = tables.setdefault(c.get("table_name"), {"columns": {}, "pk": False})
            t["columns"][c.get("column_name")] = str(c.get("data_type") or "")
        for p in data.get("primary_keys") or []:
            tables.setdefault(p.get("table_name"), {"columns": {}, "pk": False})["pk"] = True
        for f in data.get("foreign_keys") or []:
            fks.append((f.get("table_name"), f.get("column_name")))
        for i in data.get("indexes") or []:
            m = re.search(r"\(([^)]*)\)", str(i.get("indexdef") or ""))
            if m:
                indexed.add((i.get("tablename"), m.group(1).split(",")[0].strip().strip('"')))
        return tables, fks, indexed
    name = lambda s: s.split(".")[-1].strip('"')
    for m in re.finditer(r"(?is)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)\s*\((.*?)\n\);", text):
        t = tables.setdefault(name(m.group(1)), {"columns": {}, "pk": False})
        for col in m.group(2).split("\n"):
            col = col.strip().rstrip(",")
            if not col:
                continue
            if re.match(r"(?i)(CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY", col):
                t["pk"] = True
                continue
            if re.match(r"(?i)(CONSTRAINT|UNIQUE|CHECK|FOREIGN|EXCLUDE)\b", col):
                fk = re.search(r"(?i)FOREIGN\s+KEY\s*\(([^)]*)\)", col)
                if fk:
                    fks.append((name(m.group(1)), fk.group(1).split(",")[0].strip().strip('"')))
                continue
            parts = col.split(None, 2)
            if len(parts) >= 2:
                t["columns"][parts[0].strip('"')] = parts[1]
                if re.search(r"(?i)PRIMARY\s+KEY", col):
                    t["pk"] = True
                if re.search(r"(?i)\bREFERENCES\b", col):
                    fks.append((name(m.group(1)), parts[0].strip('"')))
    for m in re.finditer(r"(?is)ALTER\s+TABLE\s+(?:ONLY\s+)?([\w.\"]+)\s+ADD\s+CONSTRAINT\s+\S+\s+PRIMARY\s+KEY", text):
        tables.setdefault(name(m.group(1)), {"columns": {}, "pk": False})["pk"] = True
    for m in re.finditer(r"(?is)ALTER\s+TABLE\s+(?:ONLY\s+)?([\w.\"]+)\s+ADD\s+CONSTRAINT\s+\S+\s+FOREIGN\s+KEY\s*\(([^)]*)\)", text):
        fks.append((name(m.group(1)), m.group(2).split(",")[0].strip().strip('"')))
    for m in re.finditer(r"(?is)CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?\S+\s+ON\s+(?:ONLY\s+)?([\w.\"]+)"
                         r"(?:\s+USING\s+\w+)?\s*\(([^)]*)\)", text):
        indexed.add((name(m.group(1)), m.group(2).split(",")[0].strip().strip('"')))
    return tables, fks, indexed


def check_schema(path):
    tables, fks, indexed = parse_schema(path)
    hits = []
    for t, info in sorted(tables.items()):
        if t and not info["pk"]:
            hits.append({"severity": "low", "key": "nopk-%s" % t, "title": "Table %s has no primary key" % t,
                         "detail": "updates and replication need a way to address one row"})
        for col, typ in sorted(info["columns"].items()):
            if col and SECRET_COLUMN.match(col) and not HASHED.search(col) and re.search(r"(?i)text|char|varchar", typ or ""):
                hits.append({"severity": "medium", "key": "secret-%s-%s" % (t, col),
                             "title": "Column %s.%s looks like a secret stored as plain %s" % (t, col, typ),
                             "detail": "store a hash (passwords) or encrypt it (keys and tokens)"})
    for t, col in fks:
        if t and col and (t, col) not in indexed:
            hits.append({"severity": "low", "key": "fkidx-%s-%s" % (t, col),
                         "title": "Foreign key %s.%s has no index" % (t, col),
                         "detail": "joins and deletes on the parent table scan the whole child table"})
    return hits


def scan(a):
    """Return {check_id: hits or None when not applicable}, plus the inputs used."""
    out = {}
    out["rls-off"] = check_rls(load_rows(a.rls)) if a.rls else None
    out["rls-permissive"] = check_policies(load_rows(a.policies)) if a.policies else None
    out["public-buckets"] = check_buckets(load_rows(a.buckets)) if a.buckets else None
    out["service-role-in-client"] = check_client(a.client_src) if a.client_src else None
    out["migrations"] = check_migrations(a.migrations) if a.migrations else None
    out["schema"] = check_schema(a.schema) if a.schema else None
    return out


# ---------------------------------------------------------------- commands

NA_REASON = {
    "rls-off": "no --rls file given (run: dbcheck.py queries)",
    "rls-permissive": "no --policies file given",
    "public-buckets": "no --buckets file given (not a Supabase project, or not exported)",
    "service-role-in-client": "no --client-src folder given",
    "migrations": "no --migrations folder given",
    "schema": "no --schema file given",
}


def cmd_queries(a):
    blocks = []
    for fname, title, sql in QUERIES:
        blocks.append("-- %s\n-- save the result as %s (JSON or CSV)\n%s\n" % (title, fname, sql))
    text = "\n".join(blocks)
    if a.out:
        ledger.guard_out(a.out, a.dir, a.force, ours="-- save the result as rls.json")
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("%s wrote the queries to %s. They only read; run them yourself, then dbcheck.py scan." % (ledger.OK, a.out))
    else:
        print("Read-only queries. Run each in psql or the Supabase SQL editor and save the result:\n")
        print(text)
    return 0


def cmd_scan(a):
    res = scan(a)
    if a.json:
        print(json.dumps(res, indent=2))
        return 0
    for cid, title, _, _ in CHECKS:
        hits = res[cid]
        if hits is None:
            print("  -- %s: not checked (%s)" % (title, NA_REASON[cid]))
            continue
        print("  %s %s%s" % (ledger.BAD if hits else ledger.OK, title, (": %d" % len(hits)) if hits else ""))
        for h in hits[:a.show]:
            print("      [%s] %s%s" % (h["severity"], h["title"], ("  (%s)" % h["location"]) if h.get("location") else ""))
    return 0


def _copy(d, path):
    dest = os.path.join(ledger.evidence_dir(d, SKILL), os.path.basename(path))
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")


def cmd_record(a):
    res = scan(a)
    ledger.init_coverage(a.dir, SKILL, [(c[0], c[1]) for c in CHECKS])
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report = os.path.join(ev_dir, "db-scan.json")
    with open(report, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2)
    rep_rel = os.path.relpath(report, ledger.root(a.dir)).replace(os.sep, "/")
    inputs = {"rls-off": a.rls, "rls-permissive": a.policies, "public-buckets": a.buckets, "schema": a.schema}
    # A record is a fresh snapshot: earlier findings go, except ones a person ruled a false positive.
    kept = set()
    for f in ledger.load_findings(a.dir, SKILL):
        if f["status"] == "false_positive":
            kept.add(f["id"])
        else:
            os.remove(ledger.finding_path(a.dir, SKILL, f["id"]))
    total = 0
    for cid, title, _, fix in CHECKS:
        hits = res[cid]
        if hits is None:
            ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note=NA_REASON[cid])
            continue
        ev = [rep_rel]
        if inputs.get(cid):
            ev.insert(0, _copy(a.dir, inputs[cid]))
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=ev, note="%d problem(s)" % len(hits))
        for h in hits:
            fid = ledger.slug("db-%s-%s" % (cid, h["key"]))[:60].rstrip("-")
            if fid in kept:
                continue
            f = ledger.new_finding(SKILL, h["title"][:160], h["severity"], id=fid, category="database:%s" % cid,
                                   evidence=ev, fix=fix, description=h.get("detail"), location=h.get("location"))
            ledger.write_finding(a.dir, f)
            ledger.verify(a.dir, SKILL, fid, by="evidence-file")
            total += 1
    worst = [h for v in res.values() if v for h in v if h["severity"] in ("critical", "high")]
    print("%s recorded the database review: %d finding(s), %d high or critical." % (
        ledger.BAD if worst else ledger.OK, total, len(worst)))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no database review recorded yet. Run: dbcheck.py queries, then dbcheck.py record")
        return 0
    for c in cov["checks"]:
        print("  %-24s %s" % (c["id"], c["status"]))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def _inputs(p):
    p.add_argument("--rls", help="tables and their row-level security flag (JSON or CSV)")
    p.add_argument("--policies", help="pg_policies rows (JSON or CSV)")
    p.add_argument("--buckets", help="storage.buckets rows (JSON or CSV)")
    p.add_argument("--schema", help="pg_dump --schema-only SQL, or the schema JSON from the queries")
    p.add_argument("--migrations", action="append", default=[], help="a migrations folder (repeatable)")
    p.add_argument("--client-src", action="append", default=[], help="client code to search for the service-role key (repeatable)")


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queries", help="print the read-only queries to export")
    q.add_argument("--out", help="also write them to this file")
    q.add_argument("--force", action="store_true", help="overwrite the file even if it is not one Proof wrote")
    q.set_defaults(func=cmd_queries)
    s = sub.add_parser("scan", help="check the saved files and print what was found")
    _inputs(s)
    s.add_argument("--json", action="store_true")
    s.add_argument("--show", type=int, default=5, help="problems to print per check")
    s.set_defaults(func=cmd_scan)
    r = sub.add_parser("record", help="write the review to the ledger")
    _inputs(r)
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded coverage and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
