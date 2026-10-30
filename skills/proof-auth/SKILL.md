---
name: proof-auth
description: >-
  Login and session safety for an app you own or are authorised to test, with
  evidence for every result: password reset, session expiry, logout really ending
  the session, where tokens are stored, session cookie flags, token hygiene, OAuth
  redirect allowlists, rate limiting, account enumeration, a second factor that
  cannot be skipped, and the password policy. Offline analysers read what you
  captured (a token, sign-in response headers, two responses, a log of repeated
  attempts); tokens are decoded, never verified, never printed in full. Optional,
  not part of a full check. Use when the user says "test my login", "auth security",
  "is logout safe", "session handling", "check my JWT", "cookie flags", "rate limit
  the login", "account enumeration", or "password reset security".
argument-hint: "[a check to focus on, or blank for all]"
---

# proof-auth

Most account takeovers go through the doors around the password: a reset link that
works twice, a session that survives logout, a sign-in that answers "no such user"
differently from "wrong password", a login with no rate limit. This skill walks
those doors on your own app and records what it found, with the captured requests
and responses as proof.

It is optional and not part of `/proof-full-check`. `/proof-sec-web` covers
authentication as one line of its checklist; this goes deeper.

What the user typed after `/proof-auth`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/auth.py" plan
python3 "${CLAUDE_SKILL_DIR}/auth.py" jwt token.txt --max-lifetime 24 --record
python3 "${CLAUDE_SKILL_DIR}/auth.py" cookies login-headers.txt --max-age-days 30 --record
python3 "${CLAUDE_SKILL_DIR}/auth.py" enum unknown-user.txt wrong-password.txt --size-tolerance 0.1 --record
python3 "${CLAUDE_SKILL_DIR}/auth.py" ratelimit attempts.txt --attempts 20 --record
python3 "${CLAUDE_SKILL_DIR}/auth.py" result logout-invalidates fail --evidence replay.txt \
  --title "Old session token still works after logout" --severity high --verify
python3 "${CLAUDE_SKILL_DIR}/auth.py" na mfa --note "the app offers no second factor"
python3 "${CLAUDE_SKILL_DIR}/auth.py" status
```

## Authorisation

Only test an app you own or have written permission to test, with test accounts
you control. Recording a result refuses unless `proof/target.json` says
`authorised: true` (set it with `/proof-plan`). Analysing a file you already saved
is always allowed. Keep attempt counts modest on shared environments, and never
point the rate-limit test at someone else's service.

## Step 1: the checklist

`plan` creates one coverage check per item and prints how to test each one and
what safe looks like: `password-reset`, `session-expiry`, `logout-invalidates`,
`token-storage`, `cookie-flags`, `jwt-hygiene`, `oauth-redirects`, `rate-limiting`,
`enumeration`, `mfa`, `password-policy`.

## Step 2: capture and analyse

- **Token:** copy an access token your test account was issued (from the
  browser's storage or an Authorization header) into a file and run `jwt`. It
  flags an unsigned token, a missing `exp`, a lifetime over `--max-lifetime` hours,
  shared-secret signing to double-check, and secret-looking claims. Only the first
  four characters are ever printed.
- **Cookies:** save the response headers of a sign-in (`curl -si`, or the
  browser's network panel) and run `cookies`. Session-like cookies must be
  HttpOnly, Secure and SameSite, with a lifetime under `--max-age-days`.
- **Enumeration:** save the full response to a sign-in with an unknown email and
  with a known email and a wrong password, then run `enum`. Different status codes,
  messages or sizes beyond `--size-tolerance` reveal which accounts exist. Repeat
  for sign-up and reset.
- **Rate limiting:** send repeated failed sign-ins from one client against your
  own staging app, save one line per attempt with its status code (or a JSON list
  of `{status, body}`), and run `ratelimit`. It passes when a 429, 423 or a lockout
  message appears within `--attempts`.

Add `--record` to any analyser to cover its check and write a verified finding
when it found a problem, with the captured file as evidence.

## Step 3: the checks only you can perform

For `password-reset`, `session-expiry`, `logout-invalidates`, `token-storage`,
`oauth-redirects`, `mfa` and `password-policy`, follow the steps `plan` printed,
save the request and response (or a screenshot) and record the outcome with
`result <check> pass|fail --evidence <file>`. A failure needs `--title`; add
`--severity`, `--repro`, `--fix`, and `--verify` once the evidence shows the
problem. Mark what does not apply with `na <check> --note "..."`.

## Honest limits

These checks show what one test account saw at one moment. Decoding a token says
nothing about whether the server checks its signature; replaying a token after
logout does. Timing differences that reveal accounts need many samples, which this
tool does not measure. Social sign-in redirect rules are partly enforced by the
provider, so a refused redirect may be the provider's doing. A clean result is not
a penetration test; have a professional confirm critical findings.

## Output / next

`proof/coverage/proof-auth.json`, findings under `proof/findings/proof-auth/`, the
captured files under `proof/evidence/proof-auth/`. Pairs with `/proof-sec-web`
and, for mobile token storage, `/proof-react-native`. Then `/proof-report`.
