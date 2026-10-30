# Launch-readiness checklist

The operational things that decide whether launch day goes well. Each item needs
evidence: a file under `proof/` that shows the thing working, not a "yes". The JSON
block below is the machine-readable list `release.py` reads; it is the single source
of truth for ids, titles, groups and which items block a launch. Edit it here and the
tool follows.

```json
[
  {"id": "error-tracking", "title": "Error tracking receives a deliberate test error", "group": "observability", "critical": true},
  {"id": "logs-searchable", "title": "Production logs are kept and searchable", "group": "observability", "critical": false},
  {"id": "alerts-reach-human", "title": "An alert fires and reaches a named human", "group": "observability", "critical": true},
  {"id": "uptime-check", "title": "An external uptime check watches the main URL", "group": "observability", "critical": false},
  {"id": "backups-exist", "title": "Automatic backups exist and are recent", "group": "recovery", "critical": true},
  {"id": "restore-tested", "title": "A restore from backup was actually performed", "group": "recovery", "critical": true},
  {"id": "rollback-tried", "title": "Rollback procedure is written and was tried", "group": "recovery", "critical": true},
  {"id": "migration-reversible", "title": "The launch database migration can be reversed", "group": "recovery", "critical": false},
  {"id": "secrets-managed", "title": "Secrets live in a manager, not in code", "group": "access", "critical": true},
  {"id": "least-privilege", "title": "Production access is limited to who needs it", "group": "access", "critical": false},
  {"id": "two-factor", "title": "Two-factor sign-in on every critical account", "group": "access", "critical": true},
  {"id": "domain-tls-renew", "title": "Domain and TLS certificate renew automatically", "group": "access", "critical": true},
  {"id": "rate-limits", "title": "Rate limits protect sign-in, sign-up and costly endpoints", "group": "traffic", "critical": false},
  {"id": "cdn-caching", "title": "Static assets are cached or served from a CDN", "group": "traffic", "critical": false},
  {"id": "load-tested", "title": "The expected launch traffic was load tested", "group": "traffic", "critical": false},
  {"id": "on-call-owner", "title": "An on-call owner is named for the launch window", "group": "people", "critical": true},
  {"id": "status-comms", "title": "A status page or comms channel is ready", "group": "people", "critical": false},
  {"id": "support-inbox", "title": "The support inbox is monitored during launch", "group": "people", "critical": false},
  {"id": "known-issues", "title": "A known issues list exists and is shared", "group": "people", "critical": false},
  {"id": "legal-pages", "title": "Privacy policy and terms are published and linked", "group": "legal", "critical": true},
  {"id": "analytics-consent", "title": "Analytics and tracking respect consent", "group": "legal", "critical": false},
  {"id": "kill-switches", "title": "Feature flags can turn risky features off", "group": "legal", "critical": false}
]
```

Critical items block the launch: if one is not done, launching means accepting a
risk that can turn a bad hour into a lost week. Non-critical items are strongly
advised; skip one only on purpose, with a note.

For each item: what good looks like, how to prove it, and what counts as evidence.
Save evidence under `proof/evidence/proof-release/`.

## Observability

### error-tracking

- **Good:** an error thrown in production shows up in the error tracker within
  minutes, with a stack trace and the release version.
- **Prove it:** trigger a harmless test error on the production build (a hidden
  test route, or the tracker's own test call) and find it in the tracker.
- **Evidence:** a screenshot of the test error in the tracker, with its timestamp.

### logs-searchable

- **Good:** application logs from production are kept for at least a week and you
  can search them by request id, user id or error text.
- **Prove it:** search the log tool for a request you just made and find it.
- **Evidence:** a screenshot or saved query result showing that request.

### alerts-reach-human

- **Good:** when the error rate jumps or the site goes down, a named person gets a
  page, call or message they will see at 3am, not an email nobody reads.
- **Prove it:** fire a test alert (most alerting tools have a test button, or lower
  a threshold briefly) and confirm the person received it on their phone.
- **Evidence:** a screenshot of the alert arriving on the receiving device.

### uptime-check

- **Good:** an outside service checks the main URL every few minutes from more than
  one region, and alerts on failure.
- **Prove it:** open the check's history and confirm it is running against the
  production URL.
- **Evidence:** a screenshot of the check configuration and its recent results.

## Recovery

### backups-exist

- **Good:** the production database and any user uploads are backed up
  automatically, at least daily, to a location separate from production.
- **Prove it:** list the backups and confirm the newest is from the last day.
- **Evidence:** the backup listing with dates, saved as text or a screenshot.

### restore-tested

- **Good:** someone has restored a recent backup into a scratch environment and
  checked the data is there. A backup that was never restored is a hope.
- **Prove it:** restore the latest backup to a non-production database and count
  rows in a key table against production.
- **Evidence:** the restore log or terminal output, with the row counts.

### rollback-tried

- **Good:** there is a written, step-by-step way to return to the previous release,
  and it was tried at least once on staging.
- **Prove it:** deploy a release to staging, roll it back with the written steps,
  and time it.
- **Evidence:** the written procedure plus the log of the rollback run.

### migration-reversible

- **Good:** the database migration that ships with launch has a tested down step,
  or the change is additive so the old code still runs against the new schema.
- **Prove it:** run the migration up and down on a copy of production data.
- **Evidence:** the migration tool output for both directions.

## Access

### secrets-managed

- **Good:** API keys, database passwords and signing keys are read from a secrets
  manager or the host's environment settings, never committed to the repository.
- **Prove it:** run a secret scan over the repository and history (the
  `/proof-sec-secrets` skill does this) and check where the app reads its keys.
- **Evidence:** the scan output showing no live secrets, or its ledger file.

### least-privilege

- **Good:** only the people who need production access have it, each with their
  own account, and former contributors are removed.
- **Prove it:** export the member list of the cloud account, database and hosting
  dashboard and review it.
- **Evidence:** the reviewed member list with a note of what was removed.

### two-factor

- **Good:** two-factor sign-in is on for the domain registrar, DNS, hosting, cloud,
  code host, email and payment accounts, for every person with access.
- **Prove it:** check each account's security page or the organisation's
  enforcement setting.
- **Evidence:** screenshots of the enforcement setting or each account's status.

### domain-tls-renew

- **Good:** the domain is set to auto-renew with a valid card, and the TLS
  certificate renews automatically well before it expires.
- **Prove it:** check the registrar's renewal setting and the certificate's expiry
  date and renewal method.
- **Evidence:** a screenshot of auto-renew and the certificate details.

## Traffic

### rate-limits

- **Good:** sign-in, sign-up, password reset and any endpoint that costs money or
  sends email refuse a flood of requests from one client.
- **Prove it:** send a burst of requests to one of them on staging and see the
  limit respond.
- **Evidence:** the responses showing the limit (for example status 429).

### cdn-caching

- **Good:** images, scripts and styles are served with cache headers or from a CDN,
  so a traffic spike does not hit the app server for every file.
- **Prove it:** fetch a static asset twice and read its cache headers.
- **Evidence:** the saved response headers.

### load-tested

- **Good:** the app was run at the traffic you expect on launch day, plus headroom,
  and stayed inside its latency and error budgets.
- **Prove it:** run a load test against staging; the `/proof-load` skill plans and
  records one.
- **Evidence:** the load test summary, or the proof-load scores file.

## People

### on-call-owner

- **Good:** one named person owns production for the launch window, with a backup,
  and both know it and are reachable.
- **Prove it:** write the rota down with names, hours and contact routes, and get
  both people to confirm.
- **Evidence:** the rota document with the confirmations.

### status-comms

- **Good:** there is a status page, or at least a public channel, where you will
  tell users what is happening if something breaks.
- **Prove it:** post a test update, or confirm the page is reachable and someone
  can edit it.
- **Evidence:** a screenshot of the status page or channel.

### support-inbox

- **Good:** the support address works, lands somewhere a person reads, and someone
  is assigned to answer during launch.
- **Prove it:** send a test message to the support address and confirm it arrived.
- **Evidence:** a screenshot of the test message received.

### known-issues

- **Good:** the bugs you are launching with are written down with workarounds, so
  support can answer quickly and nobody is surprised.
- **Prove it:** write the list and share it with everyone on the launch.
- **Evidence:** the known issues document.

## Legal and product

### legal-pages

- **Good:** a privacy policy and terms of service that match what the product
  actually does are published and linked from the site and the sign-up flow.
- **Prove it:** open both pages on the production URL and follow the links from
  sign-up.
- **Evidence:** screenshots of both pages live, with their URLs.

### analytics-consent

- **Good:** analytics and tracking scripts load only as the law and your policy
  allow, and the consent choice is honoured.
- **Prove it:** decline consent and check no tracking requests go out. The
  `/proof-privacy` skill covers this in depth.
- **Evidence:** a capture of network requests after declining.

### kill-switches

- **Good:** risky or new features sit behind flags that can be turned off without a
  deploy, and someone on call knows how.
- **Prove it:** toggle one flag off and on in production or staging and watch the
  feature disappear and return.
- **Evidence:** screenshots or a log of the toggle.
