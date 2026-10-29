---
name: proof-api
description: >-
  Contract and awkward-input testing of an HTTP API that you own or are authorised
  to test, with every result recorded as evidence. Reads an OpenAPI 3 spec, lists
  every operation, generates a checklist of awkward inputs per endpoint (missing and
  wrongly typed fields, nulls, extra fields, huge strings, negative numbers,
  query-like and markup-like text sent as plain data, no credentials, another user's
  id), checks each captured response against the schema the spec declares, and
  records passes, failures and coverage in the ledger. The tool sends nothing; the
  requests are made with the browser tools or curl. Use when the user says "test the
  API", "contract test", "does the API match its spec", "check the responses against
  the schema", "OpenAPI", "fuzz the endpoints", "awkward inputs", or "API test".
argument-hint: "[path to openapi.json, or an endpoint such as 'POST /users' to focus on]"
---

# proof-api

Test an API two ways and leave proof of both: does each response match the contract
the spec declares, and what does each endpoint do with input it was not expecting.
The tool here reads files only: the spec, and responses saved under
`proof/evidence/proof-api/`. You make the real requests with other tools, save what
came back, and the tool checks and records it. Nothing counts without a saved
response behind it.

What the user typed after `/proof-api`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" <command>   # endpoints, cases, validate, record-case, skip, verify, status
```

A small complete spec to try it on is `${CLAUDE_SKILL_DIR}/openapi.example.json`;
the case list it produces is `${CLAUDE_SKILL_DIR}/cases.example.json`.

## Authorisation

`schema_check.py` never connects to anything. The requests this skill asks you to
make are active testing. Before making any, read `proof/target.json` if it exists: a
local server (localhost, 127.0.0.1) is always fine; any other host only when
`authorised` is true. If it is false, stop and ask the user to confirm ownership or
written permission (`/proof-plan` records it). Never send the awkward-input cases to
a third party's API. Keep every request non-destructive on shared data: create and
change only test records, use a test account, and never run a huge-string or fuzz
case against production.

## Step 1: get the spec

The tool reads OpenAPI 3 as JSON. Most frameworks serve it (`/openapi.json`,
`/swagger/v1/swagger.json`, `/api-docs`, `/v3/api-docs`); otherwise ask the user
where it lives. A YAML spec needs exporting to JSON first (the tool says so and
stops). No spec at all? Write a minimal one with the user for the endpoints that
matter. It is the contract the rest of the run checks against, so it has to come
from them, not from guessing at responses.

## Step 2: list the operations and plan coverage

```bash
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" endpoints --spec openapi.json
```

Prints every `METHOD path` with its parameters, the request body's required fields,
the declared response codes and whether it needs credentials, and creates the
coverage ledger with one check per operation, all not run. A malformed spec is
refused with a plain message (exit 2), including Swagger 2 and a `$ref` that does
not resolve.

## Step 3: generate the awkward-input cases

```bash
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" cases --spec openapi.json
```

Writes `proof/api/cases.json` and `proof/api/checklist.md`: per operation, no
credentials, another user's id, an unknown id, missing or wrongly typed parameters,
and for JSON bodies missing required fields, wrong types, nulls where not nullable,
a value outside an enum, an undeclared extra field, a 100000-character string,
query-like and markup-like text sent as plain data, and -1 and 0 in every number.
Each case says what to send and what a sound API does. The cases become coverage
checks, so each one ends as covered (with evidence) or not applicable (with a
reason).

## Step 4: pick the path with proof-doctor

Run `/proof-doctor --for proof-api`. When Schemathesis is found, the user can run it
against the local server following its own documentation: property-based fuzzing
over the whole spec tries far more inputs than this checklist. Save its output to
`proof/evidence/proof-api/fuzz-run.txt`, record the `fuzz-run` case pass or fail
from it, and record each failure it reports as its own finding with `record-case`.
When no fuzzer is installed, mark the case not applicable and use the example-based
path below:

```bash
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" skip fuzz-run --note "no fuzzer installed"
```

## Step 5: make the requests and save the responses

For each operation, call it the normal way for every declared status you can reach
(a good request, a bad body for the 400, an unknown id for the 404), then work
through its cases. Make the requests with whatever is at hand: the built-in browser
or fetch tools, or `curl` run by the user, for example
`curl -s -i http://localhost:8080/users/7 > proof/evidence/proof-api/get-users-id-200.json`.
Save every response under `proof/evidence/proof-api/`, one file per request, named
after the operation and status or after the case id. The tool reads a bare JSON body
or raw `curl -i` output (status line and headers included).

## Step 6: validate each response against the contract

```bash
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" validate --spec openapi.json \
  --response proof/evidence/proof-api/get-users-id-200.json \
  --endpoint "GET /users/{id}" --status 200 --record
```

The validator checks type (including nullable and the 3.1 list form), required
fields, enums, nested objects and arrays, `additionalProperties: false`, and follows
`$ref`, `allOf` and `anyOf`/`oneOf`. It lists every mismatch with its JSON path. A
status the spec does not declare is itself a mismatch. With `--record` the operation
is marked covered with the file as evidence, and a mismatch becomes a medium finding
in the `contract` category, unverified until you confirm it:

```bash
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" verify <finding-id> --by evidence-file
```

Before verifying, read the spec once more. When the spec is out of date and the API
is right, the fix is the spec: record that with `reject <finding-id> --reason "..."`
so it stays in the report as ruled out.

## Step 7: record the cases

```bash
# the API handled it well:
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" record-case post-users-missing-required pass \
  --evidence evidence/proof-api/post-users-missing-required.json

# it did not (writes a finding; --verify promotes it by its evidence file):
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" record-case get-users-id-no-auth fail \
  --evidence evidence/proof-api/get-users-id-no-auth.json \
  --title "GET /users/{id} returns the full record without a token" --severity high \
  --repro "curl -s -i http://localhost:8080/users/7" --verify

# cannot apply here:
python3 "${CLAUDE_SKILL_DIR}/schema_check.py" skip get-health-no-auth --note "a public liveness check by design"
```

Two rules: a pass or a fail needs an evidence file that exists and is not empty (the
tool refuses otherwise), and never fail a case on one odd response. Repeat the
request, save it, then record it. `status` shows what is still open.

## What it writes

`proof/api/endpoints.json`, `proof/api/cases.json`, `proof/api/checklist.md`,
`proof/coverage/proof-api.json` (operations and cases together), findings under
`proof/findings/proof-api/`, evidence under `proof/evidence/proof-api/`. Re-running
`endpoints` or `cases` against a changed spec keeps the status of checks that kept
their id and drops the rest, and says so.

## Honest limits

A passing contract check means the shape matched on the responses you captured, not
that the endpoint is correct or safe: a well-formed wrong answer passes. The
validator ignores formats, ranges and patterns, treats `oneOf` as `anyOf`, and a
status the spec declares without a schema cannot fail. The generated cases are a
starting set, not an exhaustive one: they look one level into request bodies and not
at headers, pagination or concurrency. The spec itself can be wrong, in which case a
correct API looks broken. Everything rests on files you saved: a response the tool
never saw was never tested.

## Output / next

`proof/coverage/proof-api.json`, `proof/findings/proof-api/`, `proof/api/cases.json`,
evidence under `proof/evidence/proof-api/`. Access-control problems the cases turn
up belong in `/proof-sec-web` as well. Then `/proof-report`.
