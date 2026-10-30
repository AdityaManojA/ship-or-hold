import base64
import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

db = load_tool("proof-database", "dbcheck.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = db.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


def jwt(role):
    enc = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return "%s.%s.%s" % (enc({"alg": "HS256", "typ": "JWT"}), enc({"iss": "supabase", "role": role}), "abcdefghijklmnop")


RLS = [{"schemaname": "public", "tablename": "profiles", "rowsecurity": True},
       {"schemaname": "public", "tablename": "documents", "rowsecurity": False},
       {"schemaname": "auth", "tablename": "users", "rowsecurity": False}]
RLS_CSV = "schemaname,tablename,rowsecurity\npublic,messages,f\npublic,notes,t\n"
POLICIES = [
    {"tablename": "posts", "policyname": "public read", "cmd": "SELECT", "roles": "{public}", "qual": "true",
     "with_check": None},
    {"tablename": "posts", "policyname": "anyone writes", "cmd": "INSERT", "roles": "{anon}", "qual": None,
     "with_check": "(true)"},
    {"tablename": "notes", "policyname": "own rows", "cmd": "ALL", "roles": "{authenticated}",
     "qual": "(auth.uid() = user_id)", "with_check": "(auth.uid() = user_id)"},
]
BUCKETS = [{"id": "avatars", "public": True}, {"id": "user-documents", "public": "true"},
           {"id": "exports", "public": False}]

ALEMBIC = '''"""add things"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.drop_column("users", "legacy")
    op.add_column("users", sa.Column("plan", sa.String(), nullable=False))
    op.add_column("users", sa.Column("tier", sa.String(), nullable=False, server_default="free"))
    op.alter_column(
        "messages", "body",
        type_=sa.Text(),
    )
    op.create_index("ix_messages_user", "messages", ["user_id"])
    op.create_index("ix_ok", "messages", ["x"], postgresql_concurrently=True)
    op.execute("TRUNCATE sessions")


def downgrade():
    op.drop_table("users")
'''

SQL_MIGRATION = """-- fine comment: DROP TABLE nothing
ALTER TABLE public.orders ADD COLUMN total numeric NOT NULL;
ALTER TABLE public.orders ADD COLUMN note text NOT NULL DEFAULT '';
CREATE INDEX orders_user_idx ON public.orders (user_id);
CREATE INDEX CONCURRENTLY orders_ok_idx ON public.orders (created_at);
ALTER TABLE public.orders RENAME COLUMN qty TO quantity;
DROP TABLE public.old_orders;
"""

SCHEMA_SQL = """CREATE TABLE public.users (
    id uuid NOT NULL,
    email text,
    password text,
    password_hash text
);

CREATE TABLE public.events (
    name text,
    user_id uuid REFERENCES public.users(id)
);

CREATE TABLE public.orders (
    id bigint PRIMARY KEY,
    user_id uuid
);

ALTER TABLE ONLY public.users ADD CONSTRAINT users_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.orders ADD CONSTRAINT orders_user_fk FOREIGN KEY (user_id) REFERENCES public.users(id);
CREATE INDEX orders_user_idx ON public.orders USING btree (user_id);
"""


class Database(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, rel, content):
        p = os.path.join(self.tp.base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))
        return p

    def test_rls_json_and_csv(self):
        hits = db.check_rls(db.load_rows(self.file("rls.json", RLS)))
        self.assertEqual([h["key"] for h in hits], ["documents"])  # auth schema ignored
        self.assertEqual(hits[0]["severity"], "high")
        hits = db.check_rls(db.load_rows(self.file("rls.csv", RLS_CSV)))
        self.assertEqual([h["key"] for h in hits], ["messages"])

    def test_policies(self):
        hits = {h["key"]: h["severity"] for h in db.check_policies(POLICIES)}
        self.assertEqual(hits, {"posts-public read": "medium", "posts-anyone writes": "high"})

    def test_buckets(self):
        hits = {h["key"]: h["severity"] for h in db.check_buckets(BUCKETS)}
        self.assertEqual(hits, {"avatars": "medium", "user-documents": "high"})

    def test_service_role_in_client(self):
        src = os.path.join(self.tp.base, "frontend")
        self.file("frontend/lib/supabase.ts", "const key = '%s';\nconst anon = '%s';\n" % (jwt("service_role"), jwt("anon")))
        self.file("frontend/.env.local", "NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY=abc\n")
        self.file("frontend/app/api/admin/route.ts", "const k = process.env.SUPABASE_SERVICE_ROLE_KEY;\n")
        self.file("frontend/node_modules/x/index.js", "'%s'" % jwt("service_role"))
        hits = db.check_client([src])
        sev = sorted(h["severity"] for h in hits)
        self.assertEqual(sev, ["critical", "critical", "medium"])
        crit = [h for h in hits if "service-role key" in h["title"]][0]
        self.assertNotIn(jwt("service_role"), json.dumps(hits))  # masked
        self.assertTrue(crit["location"].endswith("supabase.ts:1"))

    def test_alembic_migrations_skip_downgrade(self):
        self.file("mig/versions/001_add.py", ALEMBIC)
        hits = db.check_migrations([os.path.join(self.tp.base, "mig")])
        titles = " | ".join(h["title"] for h in hits)
        self.assertIn("drops a column", titles)
        self.assertIn("adds a NOT NULL column without a server default", titles)
        self.assertIn("changes a column's type", titles)
        self.assertIn("without CONCURRENTLY", titles)
        self.assertIn("deletes every row", titles)
        self.assertNotIn("drops a table", titles)  # only in downgrade()
        self.assertEqual(sum("NOT NULL" in h["title"] for h in hits), 1)  # tier has a server default
        self.assertEqual(sum("CONCURRENTLY" in h["title"] for h in hits), 1)
        self.assertTrue(any(h["location"].endswith("001_add.py:7") for h in hits))

    def test_sql_migrations(self):
        self.file("supabase/migrations/2026_x.sql", SQL_MIGRATION)
        hits = db.check_migrations([os.path.join(self.tp.base, "supabase")])
        lines = sorted(int(h["location"].rsplit(":", 1)[1]) for h in hits)
        self.assertEqual(lines, [2, 4, 6, 7])  # comment, defaulted column and CONCURRENTLY index are fine

    def test_schema_sql(self):
        hits = {h["key"]: h["severity"] for h in db.check_schema(self.file("schema.sql", SCHEMA_SQL))}
        self.assertEqual(hits.get("secret-users-password"), "medium")
        self.assertNotIn("secret-users-password_hash", hits)
        self.assertEqual(hits.get("nopk-events"), "low")
        self.assertNotIn("nopk-users", hits)  # primary key added by ALTER TABLE
        self.assertNotIn("nopk-orders", hits)
        self.assertEqual(hits.get("fkidx-events-user_id"), "low")
        self.assertNotIn("fkidx-orders-user_id", hits)  # indexed

    def test_schema_json(self):
        data = {"columns": [{"table_name": "keys", "column_name": "api_key", "data_type": "text"},
                            {"table_name": "keys", "column_name": "id", "data_type": "uuid"}],
                "primary_keys": [{"table_name": "keys"}],
                "foreign_keys": [{"table_name": "keys", "column_name": "owner_id"}],
                "indexes": [{"tablename": "keys", "indexdef": "CREATE INDEX k ON public.keys USING btree (id)"}]}
        hits = {h["key"] for h in db.check_schema(self.file("schema.json", [{"json_build_object": data}]))}
        self.assertEqual(hits, {"secret-keys-api_key", "fkidx-keys-owner_id"})

    def test_queries_print_and_guarded_out(self):
        rc, out = run("--dir", self.d, "queries")
        self.assertEqual(rc, 0)
        self.assertIn("relrowsecurity", out)
        self.assertIn("pg_policies", out)
        self.assertIn("storage.buckets", out)
        mine = self.file("mine.sql", "SELECT 1;")
        rc, out = run("--dir", self.d, "queries", "--out", mine)
        self.assertNotEqual(rc, 0)
        self.assertIn("--force", out)

    def test_record_and_rerecord(self):
        args = ["--dir", self.d, "record", "--rls", self.file("rls.json", RLS),
                "--policies", self.file("policies.json", POLICIES), "--buckets", self.file("buckets.json", BUCKETS),
                "--migrations", os.path.dirname(self.file("mig/001.py", ALEMBIC))]
        rc, out = run(*args)
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-database")
        self.assertTrue(fs)
        self.assertTrue(all(f["status"] == "verified" for f in fs))
        cats = {f["category"] for f in fs}
        self.assertIn("database:rls-off", cats)
        self.assertIn("database:migrations", cats)
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-database")["checks"]}
        self.assertEqual(cov["rls-off"], "covered")
        self.assertEqual(cov["service-role-in-client"], "not_applicable")
        self.assertEqual(cov["schema"], "not_applicable")
        # Rule one out as a false positive, fix RLS, record again.
        rls = next(f for f in fs if f["category"] == "database:rls-off")
        ledger.reject(self.d, "proof-database", rls["id"], reason="documents is a public catalogue")
        fixed = [dict(r, rowsecurity=True) for r in RLS]
        args[args.index("--rls") + 1] = self.file("rls2.json", fixed)
        rc, out = run(*args)
        self.assertEqual(rc, 0, out)
        ids = {f["id"]: f["status"] for f in ledger.load_findings(self.d, "proof-database")}
        self.assertEqual(ids.get(rls["id"]), "false_positive")
        self.assertEqual(sum(1 for f in ledger.load_findings(self.d, "proof-database")
                             if f["category"] == "database:rls-off"), 1)

    def test_scan_marks_missing_inputs(self):
        rc, out = run("--dir", self.d, "scan", "--buckets", self.file("b.json", BUCKETS))
        self.assertEqual(rc, 0, out)
        self.assertIn("not checked", out)
        self.assertIn("user-documents", out)


if __name__ == "__main__":
    unittest.main()
