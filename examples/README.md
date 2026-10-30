# Examples

## The demo shop

`demo-shop/` is a tiny web shop with problems planted on purpose, so you can watch
Proof catch them. Nothing in it is a pattern to copy.

```bash
python3 examples/run_demo.py
```

It starts the shop on a free local port, captures its home page and first-visit
headers, runs the secrets, dependency, infra, privacy, SEO and web-security
skills, and compiles the verdict, a SARIF file and an HTML report into
`examples/demo-shop/proof/`. The expected verdict is Not yet, with every planted
problem listed:

| planted problem | caught by |
| --- | --- |
| a hard-coded secret in `config.py` | `/proof-sec-secrets` |
| a pinned dependency with a known advisory | `/proof-sec-deps` |
| a Dockerfile that runs as root on an unpinned image | `/proof-infra` |
| an analytics tag that loads before consent, no consent banner | `/proof-privacy` |
| a signup form that posts personal data over plain HTTP | `/proof-privacy` |
| an advertising cookie on the first visit | `/proof-privacy` |
| no meta description, an image without alt text | `/proof-seo` |
| missing security headers, weak session cookie flags | `/proof-sec-web` |

The same run is a test (`tests/test_demo.py`) and a CI job, so the pack is checked
against a real running app on every push.

The secret and the advisory are fake: the secret is not a credential for any
service, and the advisory exists only in `demo-shop/advisories.json`.

## The board meeting

`board-meeting.html` is a real board meeting held on the demo shop's evidence: nine
director sub-agents (CEO, CTO, CISO, CFO, CMO, Sales, COO, CPO, Legal) heard the
presentation and voted, each applying the researched thinking of one person and quoting them with sources. Open it in a browser and it plays by itself. The
board held the launch: the CISO's and Legal's vetoes fired from verified high
findings, and every director voted Hold. To hold your own, run `/proof-board`
after the testing skills.

## GitHub Action

`github-workflow.yml` is a workflow to copy into your own repo as
`.github/workflows/proof.yml`. It uses the action in this repository to validate
your `proof/` folder, compute the verdict, post the report to the job summary,
upload verified findings as code scanning alerts, and fail the check unless the
verdict is Ship.
