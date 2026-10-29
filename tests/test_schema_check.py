import contextlib
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

sc = load_tool("proof-api", "schema_check.py")
ledger = load_tool("proof-core", "ledger.py")

EXAMPLE = os.path.join(SKILLS_DIR, "proof-api", "openapi.example.json")
EXAMPLE_CASES = os.path.join(SKILLS_DIR, "proof-api", "cases.example.json")
OPS = ["get-health", "get-users", "post-users", "get-users-id", "delete-users-id"]

GOOD_USER = {"id": 7, "email": "jo@example.test", "name": "Jo", "role": "member",
             "nickname": None, "created": "2026-10-30T10:00:00Z"}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sc.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


class SchemaCheck(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write_spec(self, obj, name="spec.json"):
        path = os.path.join(self.tp.base, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return path

    def response(self, name, body):
        """Save a captured body under evidence/ and return its ledger-relative path."""
        text = body if isinstance(body, str) else json.dumps(body)
        return self.tp.write("evidence/proof-api/%s" % name, text)

    def validate(self, rel, endpoint, status, *extra):
        return run("--dir", self.d, "validate", "--spec", EXAMPLE, "--response", os.path.join(self.d, rel),
                   "--endpoint", endpoint, "--status", status, *extra)

    # ----------------------------------------------------------- endpoints

    def test_endpoints_lists_operations_and_creates_coverage(self):
        rc, out = run("--dir", self.d, "endpoints", "--spec", EXAMPLE)
        self.assertEqual(rc, 0, out)
        for key in ("GET /health", "GET /users", "POST /users", "GET /users/{id}", "DELETE /users/{id}"):
            self.assertIn(key, out)
        self.assertIn("required: email, name", out)
        self.assertIn("responses: 204 (no body), 404", out)
        cov = ledger.load_coverage(self.d, "proof-api")
        self.assertEqual([c["id"] for c in cov["checks"]], OPS)
        self.assertTrue(all(c["status"] == "not_run" for c in cov["checks"]))
        self.assertTrue(os.path.exists(os.path.join(self.d, "api", "endpoints.json")))

    def test_endpoints_json_describes_parameters_body_and_responses(self):
        rc, out = run("--dir", self.d, "endpoints", "--spec", EXAMPLE, "--json")
        self.assertEqual(rc, 0, out)
        ops = {o["endpoint"]: o for o in json.loads(out)}
        post = ops["POST /users"]
        self.assertEqual(post["body"]["required_fields"], ["email", "name"])
        self.assertEqual([r["status"] for r in post["responses"]], ["201", "400", "401"])
        self.assertTrue(post["secured"])
        self.assertFalse(ops["GET /health"]["secured"])  # security: [] overrides the document default
        self.assertEqual(ops["GET /users/{id}"]["parameters"],
                         [{"name": "id", "in": "path", "required": True, "type": "integer", "enum": None}])
        self.assertFalse(ops["DELETE /users/{id}"]["responses"][0]["has_schema"])

    def test_endpoints_warns_when_target_is_not_authorised(self):
        ledger.write_target(self.d, {"name": "x", "kind": "api", "authorised": False, "required_skills": []})
        rc, out = run("--dir", self.d, "endpoints", "--spec", EXAMPLE)
        self.assertEqual(rc, 0, out)
        self.assertIn("authorised: false", out)

    def test_endpoints_refuses_malformed_specs(self):
        broken_ref = {"openapi": "3.0.0", "paths": {"/x": {"get": {"responses": {"200": {
            "description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Nope"}}}}}}}}}
        for bad, words in (
            ({"swagger": "2.0", "paths": {}}, "Swagger 2"),
            ({"openapi": "3.0.0"}, "paths"),
            ({"openapi": "3.0.0", "paths": {}}, "no operations"),
            (broken_ref, "$ref"),
            ({"openapi": "3.0.0", "paths": {"/x": {"get": "nope"}}}, "not an object"),
            ({"openapi": "3.0.0", "paths": {"/x": {"get": {}}}}, "no responses"),
            ([1, 2], "JSON object"),
        ):
            rc, out = run("--dir", self.d, "endpoints", "--spec", self.write_spec(bad))
            self.assertEqual(rc, 2, out)
            self.assertIn(words, out)
        yaml_like = os.path.join(self.tp.base, "spec.json")
        with open(yaml_like, "w", encoding="utf-8") as fh:
            fh.write("openapi: 3.0.0\npaths: {}\n")
        rc, out = run("--dir", self.d, "endpoints", "--spec", yaml_like)
        self.assertEqual(rc, 2)
        self.assertIn("JSON", out)
        rc, out = run("--dir", self.d, "endpoints", "--spec", os.path.join(self.tp.base, "missing.json"))
        self.assertEqual(rc, 2)
        self.assertIsNone(ledger.load_coverage(self.d, "proof-api"))

    # ------------------------------------------------------------ validate

    def test_validate_passes_a_good_response(self):
        rc, out = self.validate(self.response("ok.json", GOOD_USER), "GET /users/{id}", "200")
        self.assertEqual(rc, 0, out)
        self.assertIn("matches its schema", out)

    def test_validate_flags_missing_required_field(self):
        body = dict(GOOD_USER)
        del body["email"]
        rc, out = self.validate(self.response("r.json", body), "GET /users/{id}", "200")
        self.assertEqual(rc, 1)
        self.assertIn("body.email: required field is missing", out)

    def test_validate_flags_wrong_type(self):
        rc, out = self.validate(self.response("r.json", dict(GOOD_USER, id="7")), "GET /users/{id}", "200")
        self.assertEqual(rc, 1)
        self.assertIn("body.id: expected integer, got string", out)

    def test_validate_catches_extra_field_when_closed(self):
        rc, out = self.validate(self.response("r.json", dict(GOOD_USER, debug=True)), "GET /users/{id}", "200")
        self.assertEqual(rc, 1)
        self.assertIn("unexpected field 'debug'", out)

    def test_validate_catches_enum_violation(self):
        rc, out = self.validate(self.response("r.json", dict(GOOD_USER, role="owner")), "GET /users/{id}", "200")
        self.assertEqual(rc, 1)
        self.assertIn('body.role: "owner" is not one of "admin", "member"', out)

    def test_validate_null_only_where_nullable(self):
        # nickname is nullable (and null in GOOD_USER); name is not.
        rc, out = self.validate(self.response("r.json", dict(GOOD_USER, name=None)), "GET /users/{id}", "200")
        self.assertEqual(rc, 1)
        self.assertIn("body.name: null is not allowed", out)
        self.assertNotIn("nickname", out)

    def test_validate_checks_array_items_through_refs(self):
        body = {"items": [GOOD_USER, dict(GOOD_USER, id="x")], "total": 2}
        rc, out = self.validate(self.response("list.json", body), "GET /users", "200")
        self.assertEqual(rc, 1)
        self.assertIn("body.items[1].id: expected integer, got string", out)

    def test_validate_undeclared_status_is_a_mismatch(self):
        rc, out = self.validate(self.response("r.json", GOOD_USER), "GET /users/{id}", "418")
        self.assertEqual(rc, 1)
        self.assertIn("not declared", out)

    def test_validate_reads_raw_http_capture(self):
        raw = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps(GOOD_USER)
        rel = self.response("raw.json", raw)
        rc, out = self.validate(rel, "GET /users/{id}", "200")
        self.assertEqual(rc, 0, out)
        rc, out = self.validate(rel, "GET /users/{id}", "404")  # the capture disagrees
        self.assertEqual(rc, 2)
        self.assertIn("status line", out)

    def test_validate_no_declared_body_cannot_fail(self):
        rel = self.tp.write("evidence/proof-api/del.json", "HTTP/1.1 204 No Content\r\n\r\n")
        rc, out = self.validate(rel, "DELETE /users/{id}", "204")
        self.assertEqual(rc, 0, out)
        self.assertIn("not checked", out)

    def test_validate_bad_arguments(self):
        rel = self.response("r.json", GOOD_USER)
        rc, out = self.validate(rel, "GET /nope", "200")
        self.assertEqual(rc, 2)
        self.assertIn("no operation", out)
        rc, out = self.validate(rel, "GET /users/{id}", "two hundred")
        self.assertEqual(rc, 2)
        rc, out = self.validate("evidence/proof-api/absent.json", "GET /users/{id}", "200")
        self.assertEqual(rc, 2)
        self.assertIn("not found", out)

    def test_record_marks_coverage_and_writes_finding_on_mismatch(self):
        run("--dir", self.d, "endpoints", "--spec", EXAMPLE)
        rel = self.response("get-users-id-200.json", GOOD_USER)
        rc, out = self.validate(rel, "GET /users/{id}", "200", "--record")
        self.assertEqual(rc, 0, out)
        cov = {c["id"]: c for c in ledger.load_coverage(self.d, "proof-api")["checks"]}
        self.assertEqual(cov["get-users-id"]["status"], "covered")
        self.assertEqual(cov["get-users-id"]["evidence"], [rel])
        self.assertEqual(ledger.load_findings(self.d, "proof-api"), [])

        bad = self.response("post-users-201.json", dict(GOOD_USER, id="8"))
        rc, out = self.validate(bad, "POST /users", "201", "--record")
        self.assertEqual(rc, 1, out)
        fs = ledger.load_findings(self.d, "proof-api")
        self.assertEqual(len(fs), 1)
        f = fs[0]
        self.assertEqual((f["severity"], f["category"], f["status"], f["location"]),
                         ("medium", "contract", "unverified", "POST /users"))
        self.assertTrue(f["title"].startswith("Response for POST /users does not match its schema: body.id"), f["title"])
        self.assertEqual(f["evidence"], [bad])
        self.assertIn("verify %s --by evidence-file" % f["id"], out)
        self.assertEqual({c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-api")["checks"]}["post-users"], "covered")

        rc, out = run("--dir", self.d, "verify", f["id"], "--by", "evidence-file")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_finding(self.d, "proof-api", f["id"])["status"], "verified")

    def test_record_refuses_a_response_outside_proof(self):
        outside = os.path.join(self.tp.base, "resp.json")
        with open(outside, "w", encoding="utf-8") as fh:
            json.dump(GOOD_USER, fh)
        base = ["--dir", self.d, "validate", "--spec", EXAMPLE, "--response", outside,
                "--endpoint", "GET /users/{id}", "--status", "200"]
        rc, out = run(*(base + ["--record"]))
        self.assertEqual(rc, 2)
        self.assertIn("under", out)
        rc, out = run(*base)  # a plain check works from anywhere
        self.assertEqual(rc, 0, out)

    def test_record_creates_coverage_when_missing(self):
        rel = self.response("get-users-id-200.json", GOOD_USER)
        rc, out = self.validate(rel, "GET /users/{id}", "200", "--record")
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, "proof-api")
        self.assertEqual([c["id"] for c in cov["checks"]], OPS)

    def test_validator_subset_directly(self):
        spec = {"openapi": "3.0.0", "paths": {}, "components": {"schemas": {"Node": {
            "type": "object", "required": ["v"],
            "properties": {"v": {"type": ["integer", "null"]}, "next": {"$ref": "#/components/schemas/Node"}}}}}}
        node = {"$ref": "#/components/schemas/Node"}
        self.assertEqual(sc.validate_body(spec, node, {"v": 1, "next": {"v": None, "next": {"v": 3}}})[0], [])
        self.assertEqual(sc.validate_body(spec, node, {"v": True})[0], ["body.v: expected integer, got boolean"])
        self.assertEqual(sc.validate_body(spec, node, {"v": 1, "next": {}})[0], ["body.next.v: required field is missing"])
        either = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        self.assertEqual(sc.validate_body(spec, either, "x")[0], [])
        self.assertEqual(sc.validate_body(spec, either, 2)[0], [])
        self.assertEqual(len(sc.validate_body(spec, either, 2.5)[0]), 1)
        both = {"allOf": [{"type": "object", "required": ["a"]}, {"type": "object", "required": ["b"]}]}
        self.assertEqual(sc.validate_body(spec, both, {"a": 1})[0], ["body.b: required field is missing"])
        self.assertEqual(len(sc.validate_body(spec, {"enum": [1, 2]}, True)[0]), 1)  # true is not 1
        self.assertEqual(sc.validate_body(spec, {"type": "integer"}, 1.0)[0], [])
        self.assertEqual(sc.validate_body(spec, {"type": "string", "nullable": True}, None)[0], [])
        extra = {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": {"type": "string"}}
        self.assertEqual(sc.validate_body(spec, extra, {"a": 1, "b": 2})[0], ["body.b: expected string, got integer"])

    # --------------------------------------------------------------- cases

    def test_cases_writes_a_non_empty_list_and_coverage(self):
        rc, out = run("--dir", self.d, "cases", "--spec", EXAMPLE)
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.d, "api", "cases.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        ids = [c["id"] for c in doc["cases"]]
        self.assertGreaterEqual(len(ids), 20)
        self.assertEqual(len(ids), len(set(ids)))
        for expected in ("post-users-missing-required", "post-users-wrong-types", "post-users-null-fields",
                         "post-users-extra-field", "post-users-huge-strings", "post-users-sqlish-strings",
                         "post-users-htmlish-strings", "post-users-bad-enum", "post-users-negative-zero-numbers",
                         "get-users-id-no-auth", "get-users-id-other-user", "get-users-id-unknown-id",
                         "get-users-wrong-type-params", "fuzz-run"):
            self.assertIn(expected, ids)
            self.assertIn(expected, out)
        self.assertNotIn("get-health-other-user", ids)  # no path parameter there
        by = {c["id"]: c for c in doc["cases"]}
        self.assertEqual(by["post-users-missing-required"]["fields"], ["email", "name"])
        self.assertEqual(by["post-users-null-fields"]["fields"], ["email", "name", "role", "age"])  # nickname is nullable
        self.assertEqual(by["get-users-id-no-auth"]["severity_hint"], "high")
        self.assertIn("open", by["get-health-no-auth"]["title"])
        for c in doc["cases"]:
            for key in ("id", "endpoint", "kind", "title", "expect", "severity_hint", "evidence_hint"):
                self.assertIn(key, c)
        cov_ids = [c["id"] for c in ledger.load_coverage(self.d, "proof-api")["checks"]]
        self.assertEqual(cov_ids, OPS + ids)
        self.assertTrue(os.path.exists(os.path.join(self.d, "api", "checklist.md")))

    def test_cases_keep_recorded_status_and_endpoints_keeps_cases(self):
        run("--dir", self.d, "endpoints", "--spec", EXAMPLE)
        self.validate(self.response("get-users-id-200.json", GOOD_USER), "GET /users/{id}", "200", "--record")
        run("--dir", self.d, "cases", "--spec", EXAMPLE)
        cov = {c["id"]: c for c in ledger.load_coverage(self.d, "proof-api")["checks"]}
        self.assertEqual(cov["get-users-id"]["status"], "covered")
        run("--dir", self.d, "endpoints", "--spec", EXAMPLE)
        self.assertIn("fuzz-run", {c["id"] for c in ledger.load_coverage(self.d, "proof-api")["checks"]})

    def test_cases_out_copy_matches_the_shipped_example(self):
        copy = os.path.join(self.tp.base, "cases-copy.json")
        rc, out = run("--dir", self.d, "cases", "--spec", EXAMPLE, "--out", copy)
        self.assertEqual(rc, 0, out)
        with open(copy, encoding="utf-8") as fh:
            generated = json.load(fh)["cases"]
        with open(EXAMPLE_CASES, encoding="utf-8") as fh:
            shipped = json.load(fh)["cases"]
        self.assertEqual(generated, shipped)

    def test_stale_cases_from_another_spec_are_dropped(self):
        run("--dir", self.d, "cases", "--spec", EXAMPLE)
        other = self.write_spec({"openapi": "3.0.0", "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}}})
        rc, out = run("--dir", self.d, "endpoints", "--spec", other)
        self.assertEqual(rc, 0, out)
        self.assertIn("different spec", out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "api", "cases.json")))
        self.assertEqual([c["id"] for c in ledger.load_coverage(self.d, "proof-api")["checks"]], ["get-x"])

    # --------------------------------------------------------- record-case

    def test_record_case_pass_needs_existing_evidence(self):
        run("--dir", self.d, "cases", "--spec", EXAMPLE)
        rc, out = run("--dir", self.d, "record-case", "post-users-missing-required", "pass",
                      "--evidence", "evidence/proof-api/missing.json")
        self.assertNotEqual(rc, 0)
        self.assertIn("does not exist", out)
        empty = self.tp.write("evidence/proof-api/empty.json", b"")
        rc, out = run("--dir", self.d, "record-case", "post-users-missing-required", "pass", "--evidence", empty)
        self.assertNotEqual(rc, 0)
        self.assertIn("empty", out)
        rel = self.response("post-users-missing-required.json", {"code": "bad_request", "message": "email is required"})
        rc, out = run("--dir", self.d, "record-case", "post-users-missing-required", "pass", "--evidence", rel)
        self.assertEqual(rc, 0, out)
        cov = {c["id"]: c for c in ledger.load_coverage(self.d, "proof-api")["checks"]}
        self.assertEqual(cov["post-users-missing-required"]["status"], "covered")
        self.assertEqual(cov["post-users-missing-required"]["evidence"], [rel])
        # an absolute path under proof/ is accepted too
        rc, out = run("--dir", self.d, "record-case", "post-users-extra-field", "pass",
                      "--evidence", os.path.join(self.d, rel))
        self.assertEqual(rc, 0, out)

    def test_record_case_fail_writes_finding(self):
        run("--dir", self.d, "cases", "--spec", EXAMPLE)
        rel = self.response("get-users-id-no-auth.json", GOOD_USER)
        rc, out = run("--dir", self.d, "record-case", "get-users-id-no-auth", "fail", "--evidence", rel)
        self.assertNotEqual(rc, 0)
        self.assertIn("title", out)
        rc, out = run("--dir", self.d, "record-case", "get-users-id-no-auth", "fail", "--evidence", rel,
                      "--title", "Full record returned without a token", "--repro", "curl -s -i http://localhost:8080/users/7")
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-api")
        self.assertEqual(len(fs), 1)
        f = fs[0]
        self.assertEqual(f["severity"], "high")  # the hint for a no-auth case
        self.assertEqual(f["category"], "case:no-auth")
        self.assertEqual(f["location"], "GET /users/{id}")
        self.assertEqual(f["status"], "unverified")
        self.assertEqual(f["evidence"], [rel])
        cov = {c["id"]: c for c in ledger.load_coverage(self.d, "proof-api")["checks"]}
        self.assertEqual(cov["get-users-id-no-auth"]["status"], "covered")
        rc, out = run("--dir", self.d, "record-case", "get-users-id-no-auth", "fail", "--evidence", rel,
                      "--title", "Token is not checked at all", "--severity", "critical", "--verify")
        self.assertEqual(rc, 0, out)
        verified = [x for x in ledger.load_findings(self.d, "proof-api") if x["status"] == "verified"]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["severity"], "critical")

    def test_record_case_fail_needs_existing_evidence_too(self):
        run("--dir", self.d, "cases", "--spec", EXAMPLE)
        rc, out = run("--dir", self.d, "record-case", "get-users-id-no-auth", "fail",
                      "--evidence", "evidence/proof-api/nope.json", "--title", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("does not exist", out)
        self.assertEqual(ledger.load_findings(self.d, "proof-api"), [])

    def test_record_case_unknown_or_no_plan(self):
        rc, out = run("--dir", self.d, "record-case", "nope", "pass", "--evidence", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("no case list", out)
        run("--dir", self.d, "cases", "--spec", EXAMPLE)
        rc, out = run("--dir", self.d, "record-case", "nope", "pass", "--evidence", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("no case 'nope'", out)

    def test_skip_needs_note_then_status_shows_it(self):
        run("--dir", self.d, "cases", "--spec", EXAMPLE)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                sc.main(["--dir", self.d, "skip", "fuzz-run"])
        rc, out = run("--dir", self.d, "skip", "fuzz-run", "--note", "no fuzzer installed")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "skip", "not-a-check", "--note", "x")
        self.assertNotEqual(rc, 0)
        rc, out = run("--dir", self.d, "status")
        self.assertEqual(rc, 0, out)
        self.assertIn("operations: 0 covered, 0 not applicable, 5 not run", out)
        self.assertIn("cases:", out)
        self.assertIn("-- fuzz-run", out)

    def test_status_before_any_plan(self):
        rc, out = run("--dir", self.d, "status")
        self.assertEqual(rc, 0)
        self.assertIn("no plan yet", out)

    def test_verify_and_reject_unknown_finding(self):
        rc, out = run("--dir", self.d, "verify", "nope", "--by", "manual", "--note", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("no finding", out)
        rc, out = run("--dir", self.d, "reject", "nope", "--reason", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("no finding", out)


if __name__ == "__main__":
    unittest.main()
