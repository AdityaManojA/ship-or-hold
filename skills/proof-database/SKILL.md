---
name: proof-database
description: >-
  Reviews a Postgres or Supabase database's safety from saved files, never
  connecting to it: public tables with row-level security off, policies that let
  anyone read or write everything, public storage buckets, the service-role key
  reachable from client code, migrations that drop, rewrite or lock data, and
  schema smells (no primary key, unindexed foreign keys, secrets stored as plain
  text). Every problem points to a file and becomes a verified finding. Optional,
  not part of a full check. Use when the user says "check my database", "is RLS
  on", "Supabase security", "row level security", "are my buckets public", "is the
  service role key exposed", "review my migrations", or "database audit".
argument-hint: "[queries | scan | record] [--rls f] [--policies f] [--buckets f] [--schema f]"
---

# proof-database

The most common way a Supabase or Postgres app leaks data is not a clever attack:
a table with row-level security switched off, a policy whose condition is just
`true`, a public storage bucket, or the service-role key shipped inside the app.
Migrations cause the other big class of incidents: a dropped column, a rewritten
table or an index build that locks writes during the day. This skill checks all of
that from files you export, and points at each problem.

It is optional and never part of a full check. Run it on its own for any app with
a Postgres or Supabase database.

What the user typed after `/proof-database`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/dbcheck.py" queries
python3 "${CLAUDE_SKILL_DIR}/dbcheck.py" queries --out proof/database/queries.sql
python3 "${CLAUDE_SKILL_DIR}/dbcheck.py" scan --rls rls.json --policies policies.json --buckets buckets.json
python3 "${CLAUDE_SKILL_DIR}/dbcheck.py" scan --migrations backend/alembic/versions --client-src frontend --client-src mobile
python3 "${CLAUDE_SKILL_DIR}/dbcheck.py" record --rls rls.json --policies policies.json --buckets buckets.json \
  --schema schema.sql --migrations supabase/migrations --client-src frontend
python3 "${CLAUDE_SKILL_DIR}/dbcheck.py" status
```

The tool never connects to a database and sends nothing anywhere.

## Step 1: export the facts

`queries` prints four read-only queries (`--out` saves them to a file). Run each
yourself in `psql` or the Supabase SQL editor, against a database you own or are
authorised to inspect, and save each result as JSON or CSV:

- `rls.json`: every table in the `public` schema and whether row-level security is on
- `policies.json`: every policy, its command, roles and conditions
- `buckets.json`: Supabase storage buckets and whether each is public
- `schema.json`: columns, primary keys, foreign keys and indexes. Instead of this
  one, `pg_dump --schema-only` (or `supabase db dump`) gives a `schema.sql` the
  tool also reads.

The Supabase SQL editor can download results as CSV; `psql` can write CSV with
`\copy (...) TO 'rls.csv' CSV HEADER`. Only the facts in the files are read.

## Step 2: scan

```bash
python3 "${CLAUDE_SKILL_DIR}/dbcheck.py" scan --rls rls.json --policies policies.json --buckets buckets.json \
  --schema schema.sql --migrations backend/alembic/versions --client-src frontend
```

`--migrations` and `--client-src` can each be given more than once. `--json`
prints everything as JSON; `--show` sets how many problems print per check.

| Check | What it flags | Severity |
| --- | --- | --- |
| rls-off | A `public` table with row-level security off | high |
| rls-permissive | A policy for `anon` or `public` whose condition is `true`: writes high, reads medium (fine only for truly public data) | high / medium |
| public-buckets | A public storage bucket; high when the name suggests private files (documents, uploads, exports) | medium / high |
| service-role-in-client | A JWT whose payload role is `service_role`, or a `NEXT_PUBLIC_` / `EXPO_PUBLIC_` service-role variable, in client code | critical |
| service-role-in-client | `SUPABASE_SERVICE_ROLE_KEY` referenced in the client folder: confirm it only runs on the server | medium |
| migrations | In Alembic `upgrade()` steps and SQL migrations: dropped tables or columns, type changes, renames, NOT NULL without a default, indexes built without CONCURRENTLY, TRUNCATE | medium |
| schema | Tables with no primary key, foreign keys with no index (low); password, secret or key columns stored as plain text (medium) | low / medium |

Alembic `downgrade()` steps are skipped, because dropping things is their job.
Keys are always masked to their first four characters.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/dbcheck.py" record --rls rls.json --policies policies.json --buckets buckets.json \
  --schema schema.sql --migrations supabase/migrations --client-src frontend
```

One coverage check per row above. A check with no input is marked not applicable
with the reason, never passed. The input files and a `db-scan.json` of every hit
are copied into evidence, and each problem becomes a finding verified by them.
Recording again replaces the earlier findings with the new snapshot, but keeps any
finding you rejected as a false positive.

## Step 4: fix and re-check

Turn row-level security on and write policies tied to the user
(`auth.uid() = user_id`), make private buckets private and use signed URLs,
rotate any service-role key that was in client code, and rework risky migrations
(expand and contract, `CREATE INDEX CONCURRENTLY`, a default before NOT NULL).
Export again and re-run `record`.

## Honest limits

This reads a snapshot of exported facts, not the live database, so it is only as
current as the export. Whether a policy is right depends on your data and roles:
a `true` read policy is correct for a public catalogue and a leak for user data,
so a person still reviews each one. Policies with real conditions are not
evaluated. The client search finds keys written into files, not keys fetched at
runtime. The migration rules flag risks to confirm, not certain bugs.

## Output / next

`proof/coverage/proof-database.json`, findings under
`proof/findings/proof-database/`, the inputs and `db-scan.json` under
`proof/evidence/proof-database/`. Then `/proof-report`.
