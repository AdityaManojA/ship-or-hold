# Web security audit: the checklist

This is the list of weakness classes `proof-sec-web` works through when auditing a
web app and its API **that you own or are authorised to test**. For each class:
what to look for, how to confirm it safely (read-only, non-destructive, never
against a third party and never in a way that could delete data or deny service),
what evidence proves it, a severity guide, and the direction of the fix. Each class
cites one section of the public OWASP Web Security Testing Guide by its id so you
can read more; the text here is our own.

The goal is to find real, fixable weaknesses and record each with proof, not to
produce a long list of maybes. A finding counts only once it is verified.

The machine-readable list `secweb.py` reads:

```json
[
  {"id": "authn", "title": "Authentication weaknesses", "group": "access"},
  {"id": "session", "title": "Session management", "group": "access"},
  {"id": "authz-object", "title": "Object-level authorisation (one user reaching another's data)", "group": "access"},
  {"id": "authz-function", "title": "Function-level authorisation (reaching admin-only actions)", "group": "access"},
  {"id": "injection-sql", "title": "SQL / ORM injection", "group": "injection"},
  {"id": "injection-nosql", "title": "NoSQL / query injection", "group": "injection"},
  {"id": "injection-cmd", "title": "OS command injection", "group": "injection"},
  {"id": "injection-template", "title": "Server-side template injection", "group": "injection"},
  {"id": "xss", "title": "Cross-site scripting", "group": "client"},
  {"id": "csrf", "title": "Cross-site request forgery", "group": "client"},
  {"id": "ssrf", "title": "Server-side request forgery", "group": "server"},
  {"id": "xxe", "title": "XML external entities", "group": "server"},
  {"id": "deserialization", "title": "Unsafe deserialization", "group": "server"},
  {"id": "upload-traversal", "title": "File upload and path traversal", "group": "server"},
  {"id": "open-redirect", "title": "Open redirect", "group": "client"},
  {"id": "business-logic", "title": "Business-logic abuse (workflow, price and quantity tampering, races)", "group": "logic"},
  {"id": "rate-limit", "title": "Missing rate limiting and automation abuse", "group": "logic"},
  {"id": "mass-assignment", "title": "Mass assignment", "group": "logic"},
  {"id": "cors", "title": "CORS misconfiguration", "group": "config"},
  {"id": "headers", "title": "Security headers and cookie flags", "group": "config"},
  {"id": "clickjacking", "title": "Clickjacking (framing)", "group": "config"},
  {"id": "transport", "title": "Transport security and mixed content", "group": "config"},
  {"id": "info-disclosure", "title": "Verbose errors and information disclosure", "group": "config"},
  {"id": "client-secrets", "title": "Secrets exposed to the browser", "group": "config"},
  {"id": "deps", "title": "Vulnerable dependencies", "group": "supply"},
  {"id": "logging", "title": "Logging and monitoring gaps", "group": "ops"}
]
```

## access

**authn** (WSTG-ATHN) - Look for: weak or missing password rules, no lockout or
back-off on repeated sign-in failures, password-reset tokens that do not expire or
are guessable, login that confirms whether an account exists. Confirm safely: on a
test account you control, observe the responses to a handful of wrong passwords and
the reset flow; never run high-volume guessing against anyone. Evidence: the saved
responses. Severity: high when accounts can be taken over. Fix: enforce strong
secrets, rate-limit and back off, expire single-use reset tokens, keep responses
uniform.

**session** (WSTG-SESS) - Look for: session ids that do not change after login,
cookies without expiry, sessions that never time out, logout that leaves the token
valid. Confirm safely: with your own two test accounts, inspect the cookies before
and after login and after logout. Evidence: cookie captures. Severity: high.
Fix: rotate on login, set idle and absolute timeouts, invalidate on logout.

**authz-object** (WSTG-ATHZ-04) - Look for: an id in a URL or body that, changed to
another user's id, returns that user's data. Confirm safely: with two test accounts
you own, request account A's own resource, then from account B request A's id and
see whether it is returned. Change nothing; read only. Evidence: the two responses
side by side. Severity: critical when private data is exposed. Fix: check ownership
on every request server-side.

**authz-function** (WSTG-ATHZ-02) - Look for: admin or privileged actions reachable
by a normal user who knows the URL. Confirm safely: as a normal test account,
request a privileged read-only endpoint; do not trigger a destructive action to
prove it. Evidence: the response. Severity: high to critical. Fix: enforce role
checks server-side on every action.

## injection

For every injection class (**injection-sql**, **injection-nosql**,
**injection-cmd**, **injection-template**; WSTG-INPV) the safe method is the same:
in a test environment you own, with data you can restore, send inputs that would
reveal unsafe handling through a benign signal (an error, a timing difference, a
reflected marker) rather than through any payload that reads, changes or destroys
data or runs commands. Prefer the project's own code: grep for queries and shell
calls built by string concatenation and confirm from the source. Evidence: the
source location plus the benign signal captured. Severity: critical when it reaches
data or the OS. Fix: parameterised queries, safe APIs, allow-lists, and a sandbox
with no shell for templates. Never run destructive proofs; a source-level finding
with a safe signal is enough.

## client

**xss** (WSTG-CLNT) - Look for: user input rendered into the page without escaping.
Confirm safely: in a test environment, submit a harmless marker that would only
appear changed if it were not escaped, and inspect the rendered DOM; do not script
anything that acts on a real user. Evidence: the DOM capture. Severity: high.
Fix: context-aware output encoding and a content security policy.

**csrf** (WSTG-SESS-05) - Look for: state-changing requests with no anti-forgery
token or `SameSite` cookie. Confirm safely: inspect a state-changing request on
your own account for a token and the cookie attributes. Evidence: the request.
Severity: medium to high. Fix: anti-forgery tokens and `SameSite` cookies.

**open-redirect** (WSTG-CLNT-04) - Look for: a redirect target taken from a
parameter. Confirm safely: on your own instance, set the parameter to another path
you control and see whether it redirects off-site. Evidence: the response.
Severity: medium. Fix: allow-list redirect targets.

## server

**ssrf** (WSTG-ATHZ), **xxe** (WSTG-INPV-07), **deserialization** (WSTG-INPV),
**upload-traversal** (WSTG-ATHZ-01) - These can reach internal systems, so confirm
them from the source wherever possible: find where the server fetches a
user-supplied URL, parses XML with external entities enabled, deserialises
untrusted input, or writes an uploaded file using a user-supplied path. A benign
confirmation in a test environment (for example a request to a harmless address you
control, or a file whose name contains a path segment) is enough; never pivot into
internal infrastructure. Evidence: source plus the benign signal. Severity: high to
critical. Fix: allow-list outbound targets, disable external entities, avoid native
deserialisation, store uploads by a server-generated name outside the web root.

## logic

**business-logic** (WSTG-BUSL), **rate-limit** (WSTG-BUSL), **mass-assignment**
(WSTG-INPV) - Look for: steps that can be skipped or repeated, prices or quantities
the client can set, two requests that race to spend the same balance, request
bodies that set fields the user should not control. Confirm safely: on your own
test account, step through the flow and observe; for a race, two ordinary requests
are enough to show the window; do not run floods. Evidence: the sequence captured.
Severity: depends on what it costs. Fix: enforce the state machine, prices and
ownership server-side; lock or make critical steps idempotent.

## config

**cors** (WSTG-CONF-07), **headers**, **clickjacking** (WSTG-CLNT-09),
**transport** (WSTG-CONF), **info-disclosure** (WSTG-ERRH),
**client-secrets** - These are read-only checks on responses and shipped assets.
`secweb.py scan-headers` reads a response you captured and reports missing or weak
security headers, cookie flags, a permissive CORS policy, and framing protection.
For client secrets, search the built front-end bundle for keys and tokens. Evidence:
the captured response or the file and line. Severity: usually medium, higher when a
live secret is exposed. Fix: set the standard security headers, tighten CORS, serve
over TLS only, return generic errors, keep secrets on the server.

## supply / ops

**deps** (WSTG-CONF) - Hand off to `proof-sec-deps`; recorded here only as covered
or not. **logging** - Look for: security events (sign-in, failure, privilege
change) that are not logged, or logs that store secrets. Confirm from configuration
and a sample log. Severity: medium. Fix: log security events without secrets, and
alert on the ones that matter.
