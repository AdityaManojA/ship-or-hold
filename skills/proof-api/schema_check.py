#!/usr/bin/env python3
"""Check an API against its OpenAPI contract and plan awkward-input cases, as ledger evidence.

Standard library only, no network. This tool never sends a request. It reads an
OpenAPI 3 spec (JSON) and the responses that were captured to files under
proof/evidence/proof-api/, and does the bookkeeping: which operations the spec
declares, which captured responses match their declared schema, and which
awkward-input cases were tried. The requests themselves are made with other tools
(the built-in browser, or curl run by the user), and only against a target the
user owns or is authorised to test.

    python3 schema_check.py endpoints --spec openapi.json
    python3 schema_check.py validate --spec openapi.json --response proof/evidence/proof-api/get-users-id-200.json \\
        --endpoint "GET /users/{id}" --status 200 --record
    python3 schema_check.py cases --spec openapi.json [--out cases.json]
    python3 schema_check.py record-case post-users-missing-required pass --evidence evidence/proof-api/post-users-missing-required.json
    python3 schema_check.py record-case get-users-id-no-auth fail --evidence evidence/proof-api/get-users-id-no-auth.json \\
        --title "GET /users/{id} answers 200 without a token" --severity high
    python3 schema_check.py skip fuzz-run --note "no fuzzer installed"
    python3 schema_check.py verify <finding-id> --by evidence-file
    python3 schema_check.py status

Exit codes: 0 done (for validate: the response matches), 1 a refusal or, for
validate, a mismatch, 2 the spec or the arguments cannot be used.
"""

import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-api"
METHODS = ["get", "put", "post", "delete", "options", "head", "patch", "trace"]
FUZZ_CASE = "fuzz-run"

# Sample values the cases use. They are plain data sent to see how the API handles
# them; nothing here is meant to change or break anything.
WRONG_TYPE = {"string": 12345, "integer": "twelve", "number": "twelve", "boolean": "yes",
              "array": {"not": "a list"}, "object": "not an object"}
HUGE_LENGTH = 100000
SQLISH = "Smith' OR 'a'='a"
HTMLISH = "<b>Jo</b> & <script>x</script>"
EXTRA_FIELD = "unexpected_field"
BAD_CHOICE = "not-a-valid-choice"

_STATUS_LINE = re.compile(r"^HTTP/\S+\s+(\d{3})")


class SpecError(Exception):
    """The spec cannot be used as given. The message says why."""


def _fail(message, code=2):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return {str: "string", list: "array", dict: "object"}.get(type(value), type(value).__name__)


# ------------------------------------------------------------------- spec

def load_spec(path):
    """Read an OpenAPI 3 JSON document, or raise SpecError with a plain message."""
    if not os.path.isfile(path):
        raise SpecError("spec file not found: %s" % path)
    if path.lower().endswith((".yaml", ".yml")):
        raise SpecError("%s looks like YAML; this tool reads JSON only. Export the spec as JSON "
                        "(most frameworks serve it at /openapi.json) and pass that file." % path)
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    try:
        spec = json.loads(text)
    except ValueError as e:
        hint = ""
        if text.strip() and not text.lstrip().startswith(("{", "[")):
            hint = " (is it YAML? this tool reads JSON only)"
        raise SpecError("%s is not valid JSON: %s%s" % (path, e, hint))
    if not isinstance(spec, dict):
        raise SpecError("%s: an OpenAPI document is a JSON object, got %s" % (path, _type_name(spec)))
    if "swagger" in spec and "openapi" not in spec:
        raise SpecError("%s is a Swagger 2 document (swagger: %r); convert it to OpenAPI 3 first"
                        % (path, spec["swagger"]))
    version = spec.get("openapi")
    if not isinstance(version, str) or not version.startswith("3"):
        raise SpecError("%s is not an OpenAPI 3 document: the 'openapi' field is %r" % (path, version))
    if not isinstance(spec.get("paths"), dict):
        raise SpecError("%s has no 'paths' object" % path)
    _check_refs(spec)
    return spec


def _check_refs(spec):
    """Every $ref in the document must be local and must resolve. One walk over the
    document tree (not the reference graph), so a recursive schema cannot loop it."""
    stack = [(spec, "#")]
    while stack:
        node, where = stack.pop()
        if isinstance(node, dict):
            if "$ref" in node:
                _resolve(node, spec, where)
            for key, value in node.items():
                if key != "$ref":
                    stack.append((value, "%s/%s" % (where, key)))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                stack.append((value, "%s/%d" % (where, i)))


def _spec_sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _resolve(obj, spec, where):
    """Follow local $ref chains (#/components/...). Anything else is an error."""
    hops = 0
    while isinstance(obj, dict) and "$ref" in obj:
        ref = obj["$ref"]
        hops += 1
        if hops > 32:
            raise SpecError("%s: $ref chain is too long (circular?)" % where)
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise SpecError("%s: only local $ref (#/...) is supported, got %r" % (where, ref))
        node = spec
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise SpecError("%s: cannot resolve $ref %r" % (where, ref))
        obj = node
    return obj


def _types(schema):
    """The declared types of a schema, without 'null' (3.1 lists it as a type)."""
    t = schema.get("type")
    if isinstance(t, str):
        return [] if t == "null" else [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str) and x != "null"]
    return []


def _first_type(schema):
    if not isinstance(schema, dict):
        return None
    types = _types(schema)
    if types:
        return types[0]
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return None


def _allows_null(schema):
    """3.0 says nullable: true; 3.1 lists null as a type; an enum may contain null.
    A schema with no type and no enum constrains nothing, so null is fine there too."""
    if schema.get("nullable") is True:
        return True
    t = schema.get("type")
    if t == "null" or (isinstance(t, list) and "null" in t):
        return True
    if isinstance(schema.get("enum"), list) and None in schema["enum"]:
        return True
    return "type" not in schema and "enum" not in schema


def _json_media(content):
    """Pick the media type to check: application/json first, then any *json*, else the first.
    Returns (media type, schema or None, whether it is JSON)."""
    if not isinstance(content, dict) or not content:
        return None, None, False
    keys = list(content)
    pick = None
    for k in keys:
        if k.split(";")[0].strip().lower() == "application/json":
            pick = k
            break
    if pick is None:
        for k in keys:
            if "json" in k.lower():
                pick = k
                break
    if pick is None:
        pick = keys[0]
    entry = content[pick]
    schema = entry.get("schema") if isinstance(entry, dict) else None
    return pick, schema, "json" in pick.lower()


def _object_shape(schema, spec, where, depth=0):
    """(properties, required, closed) of an object schema, following $ref and allOf."""
    props, required, closed = {}, [], False
    if depth > 16 or not isinstance(schema, dict):
        return props, required, closed
    schema = _resolve(schema, spec, where)
    if not isinstance(schema, dict):
        return props, required, closed
    for sub in schema.get("allOf") or []:
        p, r, c = _object_shape(sub, spec, where, depth + 1)
        props.update(p)
        required += [x for x in r if x not in required]
        closed = closed or c
    if isinstance(schema.get("properties"), dict):
        for name, sub in schema["properties"].items():
            sub = _resolve(sub, spec, "%s.%s" % (where, name))
            props[name] = sub if isinstance(sub, dict) else {}
    for name in schema.get("required") or []:
        if isinstance(name, str) and name not in required:
            required.append(name)
    if schema.get("additionalProperties") is False:
        closed = True
    return props, required, closed


def _security(op, spec):
    """(secured, scheme names). An operation-level list overrides the document's;
    an empty list means open."""
    sec = op["security"] if "security" in op else spec.get("security")
    if not isinstance(sec, list):
        return False, []
    names = sorted({k for req in sec if isinstance(req, dict) for k in req})
    return bool(sec), names


def operations(spec):
    """Every METHOD path in the spec, in document order, with what a tester needs to know."""
    ops, slugs = [], set()
    for route, item in spec["paths"].items():
        if not isinstance(item, dict):
            raise SpecError("paths.%s is not an object" % route)
        shared = item.get("parameters") or []
        if not isinstance(shared, list):
            raise SpecError("paths.%s.parameters is not a list" % route)
        for method in METHODS:
            if method not in item:
                continue
            op = item[method]
            if not isinstance(op, dict):
                raise SpecError("paths.%s.%s is not an object" % (route, method))
            key = "%s %s" % (method.upper(), route)
            slug = base = ledger.slug(key)
            n = 2
            while slug in slugs:
                slug = "%s-%d" % (base, n)
                n += 1
            slugs.add(slug)
            ops.append(_describe(spec, key, slug, method, route, op, shared))
    if not ops:
        raise SpecError("the spec declares no operations under 'paths'")
    return ops


def _describe(spec, key, slug, method, route, op, shared):
    own = op.get("parameters") or []
    if not isinstance(own, list):
        raise SpecError("%s: parameters is not a list" % key)
    params = {}
    for raw in list(shared) + list(own):
        p = _resolve(raw, spec, key + " parameters")
        if not isinstance(p, dict) or "name" not in p or "in" not in p:
            raise SpecError("%s has a parameter without a name or a location ('in')" % key)
        schema = _resolve(p.get("schema") or {}, spec, "%s parameter %s" % (key, p["name"]))
        if not isinstance(schema, dict):
            schema = {}
        params[(p["in"], p["name"])] = {
            "name": p["name"], "in": p["in"],
            "required": bool(p.get("required")) or p["in"] == "path",
            "type": _first_type(schema), "enum": schema.get("enum") if isinstance(schema.get("enum"), list) else None}

    body = None
    if op.get("requestBody") is not None:
        rb = _resolve(op["requestBody"], spec, key + " requestBody")
        if not isinstance(rb, dict):
            raise SpecError("%s: requestBody is not an object" % key)
        media, schema, is_json = _json_media(rb.get("content"))
        fields = []
        if is_json and schema is not None:
            props, required, closed = _object_shape(schema, spec, key + " requestBody")
            for name, sub in props.items():
                fields.append({"name": name, "type": _first_type(sub), "required": name in required,
                               "nullable": _allows_null(sub),
                               "enum": sub.get("enum") if isinstance(sub.get("enum"), list) else None})
        else:
            closed = False
        body = {"required": bool(rb.get("required")), "media": media, "fields": fields,
                "required_fields": [f["name"] for f in fields if f["required"]], "closed": closed}

    responses = op.get("responses")
    if not isinstance(responses, dict) or not responses:
        raise SpecError("%s declares no responses" % key)
    codes = []
    for code, raw in responses.items():
        resp = _resolve(raw, spec, "%s response %s" % (key, code))
        if not isinstance(resp, dict):
            raise SpecError("%s: response %s is not an object" % (key, code))
        media, schema, is_json = _json_media(resp.get("content"))
        codes.append({"status": str(code), "media": media, "has_schema": schema is not None})

    secured, names = _security(op, spec)
    return {"id": slug, "endpoint": key, "method": method.upper(), "path": route,
            "operation_id": op.get("operationId"),
            "summary": (op.get("summary") or op.get("description") or "").strip().splitlines()[0]
            if (op.get("summary") or op.get("description")) else "",
            "parameters": list(params.values()), "body": body, "responses": codes,
            "secured": secured, "security": names}


def _find_op(ops, wanted):
    text = (wanted or "").strip()
    if " " in text:
        method, path = text.split(None, 1)
        key = "%s %s" % (method.upper(), path.strip())
        for o in ops:
            if o["endpoint"] == key:
                return o
    for o in ops:
        if o["id"] == text:
            return o
    shown = ", ".join(o["endpoint"] for o in ops[:12]) + (", ..." if len(ops) > 12 else "")
    _fail("no operation %r in the spec. Declared: %s" % (wanted, shown))


# -------------------------------------------------------------- validator

class _Validator:
    """A focused JSON Schema subset for OpenAPI responses: type (3.0 and 3.1 forms),
    nullable, enum, required, properties, additionalProperties: false, items, plus
    $ref, allOf and anyOf/oneOf (treated alike: one branch must match). Formats,
    ranges and patterns are not checked."""

    def __init__(self, spec):
        self.spec = spec
        self.problems = []
        self.checked = 0

    def check(self, value, schema, path, depth=0):
        if depth > 64:
            self.problems.append("%s: schema nesting is too deep (circular $ref?)" % path)
            return
        schema = _resolve(schema, self.spec, path)
        if not isinstance(schema, dict):
            return  # a bare true/false or garbage schema says nothing we can check
        self.checked += 1
        for sub in schema.get("allOf") or []:
            self.check(value, sub, path, depth + 1)
        for key in ("anyOf", "oneOf"):
            branches = schema.get(key)
            if isinstance(branches, list) and branches:
                self._one_of(value, branches, path, depth, key)
        if value is None:
            if not _allows_null(schema):
                self.problems.append("%s: null is not allowed (the schema is not nullable)" % path)
            return
        types = _types(schema)
        if types and not any(_is_type(value, t) for t in types):
            self.problems.append("%s: expected %s, got %s" % (path, " or ".join(types), _type_name(value)))
            return
        enum = schema.get("enum")
        if isinstance(enum, list) and not any(_same(value, e) for e in enum):
            self.problems.append("%s: %s is not one of %s" % (path, json.dumps(value), ", ".join(json.dumps(e) for e in enum)))
        if isinstance(value, dict):
            props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            for key in schema.get("required") or []:
                if isinstance(key, str) and key not in value:
                    self.problems.append("%s.%s: required field is missing" % (path, key))
            for key, sub in props.items():
                if key in value:
                    self.check(value[key], sub, "%s.%s" % (path, key), depth + 1)
            extra = schema.get("additionalProperties", True)
            if extra is False:
                for key in sorted(k for k in value if k not in props):
                    self.problems.append("%s: unexpected field '%s' (additionalProperties is false)" % (path, key))
            elif isinstance(extra, dict):
                for key in sorted(k for k in value if k not in props):
                    self.check(value[key], extra, "%s.%s" % (path, key), depth + 1)
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                self.check(item, schema["items"], "%s[%d]" % (path, i), depth + 1)

    def _one_of(self, value, branches, path, depth, key):
        first = None
        for branch in branches:
            v = _Validator(self.spec)
            v.check(value, branch, path, depth + 1)
            if not v.problems:
                self.checked += v.checked
                return
            if first is None and v.problems:
                first = v.problems[0]
        self.problems.append("%s: matches none of the %d %s alternatives (first: %s)" % (path, len(branches), key, first))


def _is_type(value, typ):
    if typ == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "string":
        return isinstance(value, str)
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "array":
        return isinstance(value, list)
    if typ == "object":
        return isinstance(value, dict)
    if typ == "null":
        return value is None
    return True  # an unknown type word cannot be checked


def _same(a, b):
    """Equality that keeps true apart from 1 and 1 apart from 1.0 is not needed; only booleans
    need care, since Python treats True == 1."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def validate_body(spec, schema, body):
    """Return (problems, values checked) for a parsed JSON body against a schema."""
    v = _Validator(spec)
    v.check(body, schema, "body")
    return v.problems, v.checked


def _response_schema(spec, op, status):
    """(matched code or None, declared codes, media, schema, is_json) for an operation and status."""
    responses = spec["paths"][op["path"]][op["method"].lower()]["responses"]
    codes = [str(c) for c in responses]
    code = None
    for candidate in (status, status[0] + "XX", status[0] + "xx", "default"):
        if candidate in responses:
            code = candidate
            break
    if code is None:
        return None, codes, None, None, False
    resp = _resolve(responses[code], spec, "%s response %s" % (op["endpoint"], code))
    media, schema, is_json = _json_media(resp.get("content") if isinstance(resp, dict) else None)
    return code, codes, media, schema, is_json


def check_response(spec, op, status, body_text):
    """Compare a captured body with the schema the spec declares for this operation and status.
    Returns (problems, notes, values checked). An undeclared status is a problem; a status
    with no declared body cannot fail, and a note says so."""
    problems, notes = [], []
    code, codes, media, schema, is_json = _response_schema(spec, op, status)
    if code is None:
        problems.append("status %s is not declared for %s (declared: %s)" % (status, op["endpoint"], ", ".join(codes)))
        return problems, notes, 0
    if code != status:
        notes.append("no exact %s response is declared; checked against the '%s' response" % (status, code))
    if schema is None:
        if media is None:
            notes.append("the spec declares no body for %s %s, so the body was not checked" % (op["endpoint"], status))
        else:
            notes.append("the spec declares no schema for %s (%s), so the body was not checked" % (status, media))
        return problems, notes, 0
    if not is_json:
        notes.append("the declared media type %s is not JSON; only JSON bodies are checked" % media)
        return problems, notes, 0
    if not body_text.strip():
        problems.append("the body is empty but the spec declares a schema for %s" % status)
        return problems, notes, 0
    try:
        body = json.loads(body_text)
    except ValueError as e:
        problems.append("the body is not valid JSON (%s)" % e)
        return problems, notes, 0
    found, checked = validate_body(spec, schema, body)
    return found, notes, checked


def split_capture(text):
    """A bare JSON body, or a raw HTTP capture (status line, headers, blank line, body).
    Returns (status from the capture or None, body text). Several HTTP blocks in a row
    (100 Continue, redirects followed) are skipped down to the last one."""
    status = None
    while True:
        text = text.lstrip()
        m = _STATUS_LINE.match(text)
        if not m:
            break
        status = m.group(1)
        parts = re.split(r"\r?\n\r?\n", text, maxsplit=1)
        text = parts[1] if len(parts) == 2 else ""
    return status, text


# ------------------------------------------------------------------ files

def _api_dir(d):
    return os.path.join(ledger.root(d), "api")


def _endpoints_path(d):
    return os.path.join(_api_dir(d), "endpoints.json")


def _cases_path(d):
    return os.path.join(_api_dir(d), "cases.json")


def _checklist_path(d):
    return os.path.join(_api_dir(d), "checklist.md")


def _read_json_quiet(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def _evidence_rel(d, full):
    """The ledger-relative form of a path under proof/, or None when it is outside."""
    base = ledger.root(d)
    full = os.path.abspath(full)
    try:
        if os.path.commonpath([full, base]) != base:
            return None
    except ValueError:
        return None
    return os.path.relpath(full, base).replace(os.sep, "/")


def _evidence_arg(d, given):
    """Normalise a user-given evidence path so the ledger can check it; anything the
    ledger would refuse is passed through unchanged so its own message is shown."""
    if os.path.isfile(given):
        return _evidence_rel(d, given) or given
    return given.replace(os.sep, "/")


def _current_cases(d, sha):
    """The case list on disk when it was generated from the spec with this hash, else None."""
    doc = _read_json_quiet(_cases_path(d))
    if doc and doc.get("spec_sha256") == sha and isinstance(doc.get("cases"), list):
        return doc["cases"]
    return None


def _sync_coverage(d, ops, cases):
    """One check per operation, then one per case. Statuses already recorded are kept."""
    checks = [(o["id"], o["endpoint"] + (" - " + o["summary"] if o["summary"] else "")) for o in ops]
    checks += [(c["id"], "%s: %s" % (c["endpoint"], c["title"])) for c in cases]
    ledger.init_coverage(d, SKILL, checks)


def _write_endpoints(d, spec_path, sha, spec, ops):
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    return _write_json(_endpoints_path(d), {
        "spec": os.path.basename(spec_path), "spec_sha256": sha,
        "title": info.get("title"), "version": info.get("version"),
        "generated": ledger.now(), "operations": ops})


def _target_note(d):
    try:
        t = ledger.load_target(d)
    except (ledger.LedgerError, ValueError, OSError):
        return "target.json does not validate; fix it (proof-core validate) before relying on it."
    if t is None or t.get("authorised"):
        return None
    return ("target.json says authorised: false. This tool sends nothing, but the requests this plan "
            "asks for are active testing: make them only against a local server until the user confirms "
            "ownership or written permission (/proof-plan sets authorised: true).")


def _load_spec_and_ops(path):
    try:
        spec = load_spec(path)
        return spec, operations(spec), _spec_sha(path)
    except SpecError as e:
        _fail("spec error: %s" % e)


# -------------------------------------------------------------- endpoints

def cmd_endpoints(a):
    spec, ops, sha = _load_spec_and_ops(a.spec)
    notes = []
    previous = _read_json_quiet(_endpoints_path(a.dir))
    if previous and previous.get("spec_sha256") not in (None, sha):
        notes.append("the spec changed since the last run: checks that kept their id keep their status, "
                     "the rest start not run")
    cases = _current_cases(a.dir, sha)
    if cases is None and os.path.isfile(_cases_path(a.dir)):
        os.remove(_cases_path(a.dir))
        notes.append("the case list came from a different spec and was removed; re-run: "
                     "schema_check.py cases --spec %s" % a.spec)
    _write_endpoints(a.dir, a.spec, sha, spec, ops)
    _sync_coverage(a.dir, ops, cases or [])
    if a.json:
        print(json.dumps(ops, indent=2))
        return 0

    for op in ops:
        auth = ("auth: " + ", ".join(op["security"])) if op["secured"] else "open (no auth declared)"
        print("%s  %s  [%s]" % (op["endpoint"], op["operation_id"] or op["summary"] or "", auth))
        if op["parameters"]:
            print("    params: " + "; ".join("%s (%s, %s%s)" % (p["name"], p["in"], p["type"] or "any",
                                                                ", required" if p["required"] else "")
                                             for p in op["parameters"]))
        else:
            print("    params: none")
        body = op["body"]
        if body is None:
            print("    body: none")
        elif body["fields"]:
            optional = [f["name"] for f in body["fields"] if not f["required"]]
            print("    body: %s%s; required: %s%s" % (
                body["media"], "" if body["required"] else " (optional)",
                ", ".join(body["required_fields"]) or "none",
                ("; optional: " + ", ".join(optional)) if optional else ""))
        else:
            print("    body: %s (no object fields declared)" % (body["media"] or "undeclared media type"))
        print("    responses: " + ", ".join("%s%s" % (r["status"], "" if r["has_schema"] else " (no body)")
                                            for r in op["responses"]))
        print("    evidence: evidence/%s/%s-<status>.json" % (SKILL, op["id"]))
    total = len(ops) + len(cases or [])
    print("\n%s %d operation(s) listed; coverage ledger has %d check(s)%s." % (
        ledger.OK, len(ops), total, " (%d of them cases)" % len(cases) if cases else ""))
    for n in notes:
        print("note: " + n)
    warn = _target_note(a.dir)
    if warn:
        print("%s %s" % (ledger.BAD, warn))
    print("Next: schema_check.py cases --spec %s, then make the requests, save each response under "
          "proof/evidence/%s/ and run validate --record on it." % (a.spec, SKILL))
    return 0


# --------------------------------------------------------------- validate

def cmd_validate(a):
    if not re.fullmatch(r"\d{3}", a.status or ""):
        _fail("--status must be a three-digit code such as 200")
    spec, ops, sha = _load_spec_and_ops(a.spec)
    op = _find_op(ops, a.endpoint)

    full = None
    for candidate in (a.response, os.path.join(ledger.root(a.dir), a.response)):
        if os.path.isfile(candidate):
            full = os.path.abspath(candidate)
            break
    if full is None:
        _fail("response file not found: %s (looked as given and under %s)" % (a.response, ledger.root(a.dir)))
    with open(full, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    captured, body_text = split_capture(text)
    if captured and captured != a.status:
        _fail("the capture's status line says %s but you asked to check against %s; pass --status %s "
              "or check a different capture" % (captured, a.status, captured))
    rel = _evidence_rel(a.dir, full)
    if a.record and rel is None:
        _fail("to record this response as evidence it must be saved under %s, for example "
              "evidence/%s/%s-%s.json" % (ledger.root(a.dir), SKILL, op["id"], a.status))

    try:
        problems, notes, checked = check_response(spec, op, a.status, body_text)
    except SpecError as e:
        _fail("spec error: %s" % e)
    for n in notes:
        print("note: " + n)
    if problems:
        print("%s %s %s: %d problem(s) against the schema" % (ledger.BAD, op["endpoint"], a.status, len(problems)))
        for p in problems:
            print("  %s %s" % (ledger.BAD, p))
    else:
        print("%s %s %s: the response matches its schema (%d value(s) checked)"
              % (ledger.OK, op["endpoint"], a.status, checked))

    if a.record:
        if ledger.load_coverage(a.dir, SKILL) is None:
            _write_endpoints(a.dir, a.spec, sha, spec, ops)
            _sync_coverage(a.dir, ops, _current_cases(a.dir, sha) or [])
            print("(no coverage ledger yet; created one from the spec)")
        try:
            ledger.mark_coverage(a.dir, SKILL, op["id"], "covered",
                                 note=_validated_note(a.dir, op["id"], a.status), evidence=[rel])
        except ledger.LedgerError as e:
            _fail("cannot record this response: %s (run endpoints --spec %s first if the ledger is stale)"
                  % (e, a.spec), 1)
        if problems:
            title = "Response for %s does not match its schema: %s" % (op["endpoint"], problems[0])
            f = ledger.new_finding(
                SKILL, title, "medium", id=ledger.slug("%s contract %s" % (op["id"], problems[0])),
                category="contract", location=op["endpoint"], evidence=[rel],
                description="Checked %s against the %s response schema. Problems:\n- %s"
                % (rel, a.status, "\n- ".join(problems)),
                repro="schema_check.py validate --spec %s --response %s --endpoint \"%s\" --status %s"
                % (a.spec, rel, op["endpoint"], a.status))
            ledger.write_finding(a.dir, f)
            _after_finding(a, f)
        else:
            print("%s recorded: %s covered, evidence %s" % (ledger.OK, op["id"], rel))
    return 1 if problems else 0


def _validated_note(d, check_id, status):
    seen = []
    cov = ledger.load_coverage(d, SKILL)
    for c in (cov or {}).get("checks", []):
        if c["id"] == check_id and c.get("note", "").startswith("validated: "):
            seen = [s.strip() for s in c["note"][len("validated: "):].split(",") if s.strip()]
    if status not in seen:
        seen.append(status)
    return "validated: " + ", ".join(seen)


def _after_finding(a, f):
    if a.verify:
        try:
            ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")
        except ledger.LedgerError as e:
            _fail("finding written but could not verify: %s" % e, 1)
        print("%s finding %r written and verified by its evidence file." % (ledger.BAD, f["id"]))
    else:
        print("%s finding %r written (unverified). Verify it before it counts:" % (ledger.BAD, f["id"]))
        print("   schema_check.py verify %s --by evidence-file" % f["id"])


# ------------------------------------------------------------------ cases

def _case(op, kind, title, expect, severity="medium", fields=None, value=None, values=None):
    c = {"id": "%s-%s" % (op["id"], kind), "endpoint": op["endpoint"], "kind": kind, "title": title,
         "expect": expect, "severity_hint": severity,
         "evidence_hint": "evidence/%s/%s-%s.json" % (SKILL, op["id"], kind)}
    if fields:
        c["fields"] = fields
    if value is not None:
        c["value"] = value
    if values:
        c["values"] = values
    return c


def generate_cases(ops):
    """A starting checklist of awkward inputs per operation. The tool does not send them."""
    cases = []
    for op in ops:
        params, body = op["parameters"], op["body"]
        fields = body["fields"] if body else []

        if op["secured"]:
            cases.append(_case(op, "no-auth", "Call with no credentials at all (no Authorization header, no cookie)",
                               "401 or 403 with the declared error shape and no data; the spec says this needs %s"
                               % ", ".join(op["security"]), "high"))
        else:
            cases.append(_case(op, "no-auth", "Call with no credentials; the spec marks this operation as open",
                               "2xx is right only if it is meant to be public: confirm that with the user, "
                               "and that nothing private is in the body"))
        path_params = [p["name"] for p in params if p["in"] == "path"]
        if path_params:
            cases.append(_case(op, "other-user", "As test account B, use a value of %s that belongs to account A"
                               % ", ".join(path_params), "403 or 404, and none of A's data in the body", "high",
                               fields=path_params))
            cases.append(_case(op, "unknown-id", "Use a value that matches no record: %s"
                               % ", ".join("%s=999999999" % n for n in path_params),
                               "404 with the declared error shape; never a 500", fields=path_params,
                               value=999999999))
        required = [p for p in params if p["required"] and p["in"] != "path"]
        if required:
            cases.append(_case(op, "missing-params", "Omit each required parameter in turn: %s"
                               % ", ".join("%s (%s)" % (p["name"], p["in"]) for p in required),
                               "400 or 422 naming the parameter; never a silent default",
                               fields=[p["name"] for p in required]))
        typed = [p for p in params if p["type"] in ("integer", "number", "boolean")]
        if typed:
            cases.append(_case(op, "wrong-type-params", "Send text where a number or boolean is expected: %s"
                               % ", ".join("%s=twelve" % p["name"] for p in typed),
                               "400 or 422; never a 500 and never a silently coerced value",
                               fields=[p["name"] for p in typed], value="twelve"))

        if fields:
            req = [f["name"] for f in fields if f["required"]]
            if req:
                cases.append(_case(op, "missing-required", "Send the body without each required field in turn: %s"
                                   % ", ".join(req), "400 or 422 naming the field; nothing created or changed",
                                   fields=req))
            typed = [f for f in fields if f["type"] in WRONG_TYPE]
            if typed:
                cases.append(_case(op, "wrong-types", "Send each field with a value of the wrong type: %s"
                                   % ", ".join("%s=%s" % (f["name"], json.dumps(WRONG_TYPE[f["type"]])) for f in typed),
                                   "400 or 422; the value must not be coerced silently (for example \"12\" stored as 12)",
                                   fields=[f["name"] for f in typed],
                                   values={f["name"]: WRONG_TYPE[f["type"]] for f in typed}))
            not_null = [f["name"] for f in fields if not f["nullable"]]
            if not_null:
                cases.append(_case(op, "null-fields", "Send null in each field that is not nullable: %s"
                                   % ", ".join(not_null), "400 or 422 for each one; never a 500",
                                   fields=not_null, value=None))
            enums = [f["name"] for f in fields if f["enum"]]
            if enums:
                cases.append(_case(op, "bad-enum", "Send a value outside the allowed set: %s"
                                   % ", ".join("%s=%s" % (n, BAD_CHOICE) for n in enums),
                                   "400 or 422 listing the allowed values", fields=enums, value=BAD_CHOICE))
            cases.append(_case(op, "extra-field", "Add a field the schema does not declare: \"%s\": \"proof\"" % EXTRA_FIELD,
                               "ignored, or 400 when the schema forbids extra fields%s; never stored or echoed "
                               "back as if it were real" % (" (this one does)" if body["closed"] else ""),
                               fields=[EXTRA_FIELD], value="proof"))
            strings = [f["name"] for f in fields if f["type"] == "string"]
            if strings:
                cases.append(_case(op, "huge-strings", "Put a %d-character string in each string field in turn: %s"
                                   % (HUGE_LENGTH, ", ".join(strings)),
                                   "413 or 400 within the usual response time; never a timeout, a 500 or an "
                                   "unbounded stored value", fields=strings,
                                   value="the letter a repeated %d times" % HUGE_LENGTH))
                cases.append(_case(op, "sqlish-strings", "Send text that looks like a query fragment, as plain data, "
                                   "in each string field: %s" % json.dumps(SQLISH),
                                   "stored and returned exactly as sent (it is only text), or rejected by a length "
                                   "or format rule; never a 500 and never a changed result set",
                                   fields=strings, value=SQLISH))
                cases.append(_case(op, "htmlish-strings", "Send text that looks like markup, as plain data, in each "
                                   "string field: %s" % json.dumps(HTMLISH),
                                   "returned exactly as sent, as JSON data; never a 500 (escaping on display is "
                                   "the web app's job)", fields=strings, value=HTMLISH))

        numbers = ["%s (%s)" % (p["name"], p["in"]) for p in params if p["type"] in ("integer", "number")]
        numbers += ["%s (body)" % f["name"] for f in fields if f["type"] in ("integer", "number")]
        if numbers:
            cases.append(_case(op, "negative-zero-numbers", "Send -1 and then 0 in each numeric input: %s"
                               % ", ".join(numbers), "rejected when the schema sets a minimum above it, otherwise "
                               "a sensible result; never a 500", fields=numbers, values={"first": -1, "then": 0}))

    cases.append({"id": FUZZ_CASE, "endpoint": "*", "kind": "fuzz",
                  "title": "Property-based fuzz run over the whole spec, when a fuzzer such as Schemathesis "
                           "is installed (check with /proof-doctor --for proof-api)",
                  "expect": "no 500s and no response that breaks its schema; save the run's output as evidence. "
                            "Without a fuzzer: skip %s --note \"no fuzzer installed\"" % FUZZ_CASE,
                  "severity_hint": "medium", "evidence_hint": "evidence/%s/%s.txt" % (SKILL, FUZZ_CASE)})
    return cases


def _write_checklist(d, ops, cases):
    L = ["# API checklist", "",
         "Make each request yourself, only against a target you own or may test. Save the response "
         "under `evidence/%s/`, then record it with `schema_check.py`." % SKILL, ""]
    for op in ops:
        L.append("## %s%s" % (op["endpoint"], (" - " + op["summary"]) if op["summary"] else ""))
        L.append("")
        L.append("- [ ] capture each declared response (%s) and run `validate --record` on it"
                 % ", ".join(r["status"] for r in op["responses"]))
        for c in cases:
            if c["endpoint"] == op["endpoint"]:
                L.append("- [ ] `%s`: %s. Expect: %s" % (c["id"], c["title"], c["expect"]))
        L.append("")
    for c in cases:
        if c["endpoint"] == "*":
            L.append("## Whole spec")
            L.append("")
            L.append("- [ ] `%s`: %s. Expect: %s" % (c["id"], c["title"], c["expect"]))
            L.append("")
    path = _checklist_path(d)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return path


def cmd_cases(a):
    spec, ops, sha = _load_spec_and_ops(a.spec)
    cases = generate_cases(ops)
    doc = {"spec": os.path.basename(a.spec), "spec_sha256": sha, "generated": ledger.now(), "cases": cases}
    canonical = _write_json(_cases_path(a.dir), doc)
    written = [canonical]
    if a.out and os.path.abspath(a.out) != os.path.abspath(canonical):
        ledger.guard_out(a.out, a.dir, a.force, ours='"spec_sha256"')
        written.append(_write_json(os.path.abspath(a.out), doc))
    _write_endpoints(a.dir, a.spec, sha, spec, ops)
    _sync_coverage(a.dir, ops, cases)
    written.append(_write_checklist(a.dir, ops, cases))

    width = max(len(c["id"]) for c in cases)
    endpoint = None
    for c in cases:
        if c["endpoint"] != endpoint:
            endpoint = c["endpoint"]
            print("\n%s" % ("Whole spec" if endpoint == "*" else endpoint))
        print("  %-*s %s" % (width, c["id"], c["title"]))
        print("  %-*s expect: %s" % (width, "", c["expect"]))
    print("\n%s %d case(s) for %d operation(s). Written: %s" % (ledger.OK, len(cases), len(ops), ", ".join(written)))
    warn = _target_note(a.dir)
    if warn:
        print("%s %s" % (ledger.BAD, warn))
    print("These are a to-do list, not a run: this tool sends nothing. Make each request with the browser "
          "tools or curl, save the response under proof/evidence/%s/, then: record-case <id> pass|fail "
          "--evidence <that file>. A case that cannot apply: skip <id> --note \"why\"." % SKILL)
    return 0


# ------------------------------------------------------------ record-case

def _load_cases(d):
    doc = _read_json_quiet(_cases_path(d))
    if doc is None or not isinstance(doc.get("cases"), list):
        _fail("no case list yet. Run: schema_check.py cases --spec <openapi.json>", 1)
    return {c["id"]: c for c in doc["cases"] if isinstance(c, dict) and "id" in c}


def cmd_record_case(a):
    cases = _load_cases(a.dir)
    if a.case_id not in cases:
        _fail("no case %r in the list. See: schema_check.py status" % a.case_id, 1)
    case = cases[a.case_id]
    evidence = _evidence_arg(a.dir, a.evidence)

    if a.outcome == "pass":
        try:
            ledger.mark_coverage(a.dir, SKILL, a.case_id, "covered", note=a.note, evidence=[evidence])
        except ledger.LedgerError as e:
            _fail("cannot record a pass: %s" % e, 1)
        print("%s %s passes, evidence recorded." % (ledger.OK, a.case_id))
        return 0

    if not a.title:
        _fail("a failing case needs --title saying what the API did wrong", 1)
    # The evidence rule applies to a fail too: coverage is marked first, and it refuses
    # a missing or empty file before any finding is written.
    try:
        ledger.mark_coverage(a.dir, SKILL, a.case_id, "covered", note=a.note, evidence=[evidence])
    except ledger.LedgerError as e:
        _fail("cannot record a fail: %s" % e, 1)
    severity = a.severity or case.get("severity_hint") or "medium"
    if not a.severity:
        print("(no --severity given; using %s, the hint for this kind of case)" % severity)
    f = ledger.new_finding(
        SKILL, a.title, severity, id=ledger.slug("%s %s" % (a.case_id, a.title)),
        category="case:%s" % case.get("kind", "input"), location=a.endpoint or case.get("endpoint"),
        repro=a.repro, evidence=[evidence], fix=a.fix,
        description="Case %s: %s. Expected: %s" % (a.case_id, case.get("title", ""), case.get("expect", "")))
    ledger.write_finding(a.dir, f)
    _after_finding(a, f)
    return 0


def cmd_skip(a):
    try:
        ledger.mark_coverage(a.dir, SKILL, a.check_id, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        _fail(str(e), 1)
    print("%s %s marked not applicable." % (ledger.OK, a.check_id))
    return 0


def _finding_must_exist(d, fid):
    if not os.path.isfile(ledger.finding_path(d, SKILL, fid)):
        known = [f["id"] for f in ledger.load_findings(d, SKILL)]
        _fail("no finding %r for %s. Known: %s" % (fid, SKILL, ", ".join(known) or "none"), 1)


def cmd_verify(a):
    _finding_must_exist(a.dir, a.id)
    try:
        ledger.verify(a.dir, SKILL, a.id, by=a.by, note=a.note)
    except ledger.LedgerError as e:
        _fail(str(e), 1)
    print("%s finding %r verified by %s." % (ledger.OK, a.id, a.by))
    return 0


def cmd_reject(a):
    _finding_must_exist(a.dir, a.id)
    try:
        ledger.reject(a.dir, SKILL, a.id, a.reason)
    except ledger.LedgerError as e:
        _fail(str(e), 1)
    print("%s finding %r marked a false positive." % (ledger.OK, a.id))
    return 0


# ----------------------------------------------------------------- status

def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no plan yet. Run: schema_check.py endpoints --spec <openapi.json>")
        return 0
    doc = _read_json_quiet(_cases_path(a.dir)) or {}
    case_ids = {c["id"] for c in doc.get("cases", []) if isinstance(c, dict) and "id" in c}
    marks = {"covered": ledger.OK, "not_applicable": "--", "not_run": "..."}
    for label, group in (("operations", [c for c in cov["checks"] if c["id"] not in case_ids]),
                         ("cases", [c for c in cov["checks"] if c["id"] in case_ids])):
        if not group:
            continue
        counts = {s: sum(1 for c in group if c["status"] == s) for s in ledger.COVERAGE_STATUSES}
        print("%s: %d covered, %d not applicable, %d not run" % (label, counts["covered"], counts["not_applicable"], counts["not_run"]))
        for c in group:
            note = (" (%s)" % c["note"]) if c.get("note") else ""
            print("  %s %s: %s%s" % (marks[c["status"]], c["id"], c["title"], note))
    findings = ledger.load_findings(a.dir, SKILL)
    if findings:
        print("findings:")
        for f in findings:
            print("  [%s] %s (%s) %s" % (f["severity"], f["title"], f["status"], f["id"]))
    return 0


# ------------------------------------------------------------------- main

def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("endpoints", help="list every operation in an OpenAPI 3 JSON spec and plan coverage")
    e.add_argument("--spec", required=True, help="path to the OpenAPI 3 document (JSON)")
    e.add_argument("--json", action="store_true", help="print the operations as JSON")
    e.set_defaults(func=cmd_endpoints)

    v = sub.add_parser("validate", help="check a captured response against the schema the spec declares")
    v.add_argument("--spec", required=True)
    v.add_argument("--response", required=True, help="a captured JSON body, or raw HTTP output (curl -i)")
    v.add_argument("--endpoint", required=True, help='for example "GET /users/{id}"')
    v.add_argument("--status", required=True, help="the status code the capture came back with, such as 200")
    v.add_argument("--record", action="store_true",
                   help="mark the operation covered with this file as evidence; write a finding on mismatch")
    v.add_argument("--verify", action="store_true", help="with --record: also verify the finding by its evidence file")
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("cases", help="write a checklist of awkward-input cases per operation")
    c.add_argument("--spec", required=True)
    c.add_argument("--out", help="also write a copy of the case list here")
    c.add_argument("--force", action="store_true", help="overwrite the file even if it is not one Proof wrote")
    c.set_defaults(func=cmd_cases)

    r = sub.add_parser("record-case", help="record a case pass or fail with its evidence")
    r.add_argument("case_id")
    r.add_argument("outcome", choices=["pass", "fail"])
    r.add_argument("--evidence", required=True, help="path under proof/ to the saved response")
    r.add_argument("--note")
    r.add_argument("--title", help="what the API did wrong (required for fail)")
    r.add_argument("--severity", choices=ledger.SEVERITIES)
    r.add_argument("--endpoint", help="override the finding's location")
    r.add_argument("--repro")
    r.add_argument("--fix")
    r.add_argument("--verify", action="store_true", help="also verify the finding by its evidence file")
    r.set_defaults(func=cmd_record_case)

    s = sub.add_parser("skip", help="mark an operation or case not applicable, with a reason")
    s.add_argument("check_id")
    s.add_argument("--note", required=True)
    s.set_defaults(func=cmd_skip)

    fv = sub.add_parser("verify", help="promote a finding to verified")
    fv.add_argument("id")
    fv.add_argument("--by", required=True, choices=["evidence-file", "repro-run", "manual"])
    fv.add_argument("--note")
    fv.set_defaults(func=cmd_verify)

    fr = sub.add_parser("reject", help="mark a finding a false positive, with a reason")
    fr.add_argument("id")
    fr.add_argument("--reason", required=True)
    fr.set_defaults(func=cmd_reject)

    sub.add_parser("status", help="coverage and findings so far").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
