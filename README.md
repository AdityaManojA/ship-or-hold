# Proof

**An evidence-first testing and audit pack for Claude.** Free, MIT, offline, no API
key, nothing to sign up for.

Most testing tools let the model *say* something passed. Proof does not. Every skill
produces a **reproducible artifact** (a failing test, a working proof, a measured
number, a screenshot) and the final verdict is **computed by a small Python tool
from a ledger of verified evidence**, never written by the model. A finding counts
only once it has been verified; a missing input makes the verdict `Incomplete`,
never a silent pass.

It covers the whole surface of a product: web and mobile testing, security and
dependency and secret audits, performance, accessibility, SEO and AI-search
readiness, mutation and load testing, LLM-feature safety, the finance behind the
product, and a tamper-evident evidence pack at the end. **24 skills**, all driven by
one shared ledger, each usable on its own.

```
/proof-plan  ->  the testing skills you choose  ->  /proof-report  ->  /proof-audit-trail
```

## Install

As a plugin in Claude Code:

```
/plugin marketplace add AdityaManojA/the-startup-skill
/plugin install proof-skill@proof-skill
```

Or copy the skills into your Claude skills folder:

```bash
git clone https://github.com/AdityaManojA/the-startup-skill.git
cp -r the-startup-skill/skills/proof-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. The tools need Python 3.8 or newer and nothing to `pip install`.

## How it works

Everything a run produces lives under a `proof/` folder in your project:

| file | written by | holds |
| --- | --- | --- |
| `target.json` | `/proof-plan` | what is under test, whether you are authorised to test it, and the skills the verdict waits for |
| `findings/<skill>/<id>.json` | any skill | one finding each; counts only when its `status` is `verified` |
| `coverage/<skill>.json` | any skill | which checks were looked at, not applicable, or not yet run |
| `scores/<skill>.json` | measuring skills | measured numbers against budgets |
| `evidence/<skill>/...` | any skill | screenshots, logs, captures, reports |
| `report.md`, `verdict.json` | `/proof-report` | the compiled report and the computed verdict |

Three rules hold across every skill: a finding is born unverified and only counts
once verified by an evidence file, a reproduced run, or a human note; coverage is
recorded, not assumed, so the report can say *what was looked at* as well as *what
broke*; and a missing input is an error, never a zero. `proof-core` documents and
enforces all of it.

**Every skill is optional and standalone.** You pick the ones the verdict should
wait for (its `required_skills`); any other can still be run on its own, and not
running it never counts against the verdict. Building something you are not ready to
make public? Leave SEO and GEO off the list. No mobile app? Leave the mobile skills
off. Nothing breaks.

**Authorisation.** The security and load skills act only against a target you mark
as owned or authorised in `target.json`; they refuse a live third-party target.

**External tools are optional.** Proof's own tools are standard-library Python and
make no network calls. When a tool like Playwright, Lighthouse, Semgrep, k6, Maestro
or TruffleHog is installed, the matching skill uses it for stronger evidence; when it
is not, the skill falls back to a slower built-in path. `/proof-doctor` tells you
what is installed and what each missing tool would add.

## The 24 skills

### Core

- **`/proof-plan`** - describe the target and choose what to test.
  `target.py init --name "App" --kind web --authorised yes --url ...` ·
  `target.py require add proof-web-e2e ...` · `target.py recommend --kind web --public --has-llm` · `target.py show`
- **`/proof-report`** - compile the run and compute the verdict.
  `verdict.py` · `verdict.py --check` (exit 0 Ship, 1 Not yet, 2 Incomplete) · `verdict.py --fail-on medium`
- **`/proof-core`** - the shared evidence ledger, schema and rules.
  `validate.py --dir proof` checks a `proof/` folder is well formed.
- **`/proof-doctor`** - detect optional external tools.
  `doctor.py` · `doctor.py --for proof-web-e2e` · `doctor.py --json`
- **`/proof-swarm`** - a deterministic, seeded crowd of persona sub-agents, shared by
  other skills. `swarm.py init --profile p.json --brief b.md [--quick]` ·
  `swarm.py prompts` · `swarm.py check` · `swarm.py tally`

### Web

- **`/proof-web-e2e`** - end-to-end user-flow testing with a screenshot behind each
  pass. `flows.py plan flows.json` · `flows.py result <id> pass --evidence ...` ·
  `flows.py result <id> fail --title ... --severity ... --verify` ·
  `flows.py edge-cases <id>` · `flows.py script <id> --out ... --python` ·
  `serve.py --run "npm run dev" --port 3000 -- <cmd>` (starts a dev server, waits, runs, tears down)
- **`/proof-web-perf`** - measure performance against a budget.
  `budget.py template --out budget.json` · `budget.py record --budget b.json --measured m.json` ·
  `budget.py from-lighthouse --json lhr.json --budget b.json` · `budget.py skip <id> --note ...`
- **`/proof-a11y`** - accessibility: a WCAG contrast checker plus a manual-review
  checklist. `contrast.py ratio --fg "#767676" --bg "#fff"` ·
  `contrast.py check --pairs pairs.json --record` · `contrast.py checklist --record`
- **`/proof-api`** - test an API against its OpenAPI contract and with awkward
  inputs. `schema_check.py endpoints --spec openapi.json` ·
  `schema_check.py validate --spec ... --response resp.json --endpoint "GET /users/{id}" --status 200` ·
  `schema_check.py cases --spec ... --out cases.json`

### Security and audit

- **`/proof-sec-web`** - a defensive, authorised-only web security audit across a
  checklist of weakness classes, with a verification gate. `secweb.py classes` ·
  `secweb.py start` · `secweb.py cover <class> --evidence ...` · `secweb.py na <class> --note ...` ·
  `secweb.py finding add <class> --title ... --severity ...` · `secweb.py finding verify <id> --by evidence-file` ·
  `secweb.py scan-headers response.txt`
- **`/proof-sec-secrets`** - scan for committed secrets, values always masked.
  `entropy.py scan .` · `entropy.py record .`
- **`/proof-sec-deps`** - audit dependencies against an advisory file.
  `advisories.py deps .` · `advisories.py audit . --advisories adv.json` · `advisories.py template --out adv.json`
- **`/proof-sec-diff`** - scope a security review to what a branch changed.
  `diffscope.py changed --base main` · `git diff main... | diffscope.py changed --stdin` · `diffscope.py record --base main`

### Mobile

- **`/proof-mobile-e2e`** - mobile flow testing, with Maestro flow generation.
  `flowgen.py plan flows.json` · `flowgen.py maestro <id> --out f.yaml --app com.example.app` ·
  `flowgen.py result <id> pass --evidence ...`
- **`/proof-mobile-audit`** - inspect an APK or IPA for permissions, cleartext
  traffic and embedded URLs. `apkscan.py apk app.apk` · `apkscan.py ios MyApp.ipa` · `apkscan.py record apk app.apk`

### Quality depth

- **`/proof-mutation`** - measure whether the tests catch bugs, by mutation testing.
  `mutate.py sites src/x.py` · `mutate.py run --src src/ --test-cmd "python -m pytest -q" --max 40`
- **`/proof-load`** - plan and score a load test against latency and throughput
  budgets. `loadplan.py plan --target URL --vus 50 --duration 60` ·
  `loadplan.py from-k6 --summary k6-summary.json --budget budget.json`
- **`/proof-explore`** - exploratory testing with a persona swarm.
  `explore.py scaffold --target "a web checkout"` · `explore.py harvest --results proof/swarm/<run>/results.json`
- **`/proof-restraint`** - find code, files and dependencies that should not exist.
  `bloat.py scan .` · `bloat.py deadcode src/` · `bloat.py deps .` · `bloat.py record .`

### SEO and AI search

- **`/proof-seo`** - a technical and on-page SEO audit with a computed score.
  `seo_score.py page page.html` · `seo_score.py site --pages dir --robots robots.txt --sitemap sitemap.xml` ·
  `seo_score.py record page.html`
- **`/proof-geo`** - how readable and citable the site is to AI search, with a
  computed citability score. `geo_score.py page page.html` ·
  `geo_score.py access --robots robots.txt --llms llms.txt` · `geo_score.py record page.html`

### AI, finance, packaging

- **`/proof-ai`** - test your own LLM feature across the OWASP LLM Top 10 categories.
  `redteam.py deck` · `redteam.py plan` · `redteam.py result <category> pass --evidence ...` ·
  `redteam.py result <category> fail --title ... --verify`
- **`/proof-finance`** - the unit economics behind the product.
  `unit_economics.py --template numbers.json` · `unit_economics.py numbers.json` · `unit_economics.py numbers.json --record`
- **`/proof-audit-trail`** - package the run into a hash-stamped evidence folder.
  `pack.py build --dir proof` · `pack.py verify proof/audit/run-<timestamp>`

## A worked run

```bash
# 1. describe the target and pick what the verdict waits for
python3 skills/proof-plan/target.py init --name "Acme Shop" --kind web --authorised yes \
  --url http://localhost:3000 --require proof-web-e2e --require proof-sec-web

# 2. test the critical flows, recording evidence for each
python3 skills/proof-web-e2e/flows.py plan flows.json
python3 skills/proof-web-e2e/flows.py result checkout pass --evidence evidence/proof-web-e2e/checkout.png

# 3. work the web security checklist
python3 skills/proof-sec-web/secweb.py start
python3 skills/proof-sec-web/secweb.py cover authz-object --evidence evidence/proof-sec-web/idor.txt --note "two test accounts, no cross-access"

# 4. compile the computed verdict
python3 skills/proof-report/verdict.py --check     # Ship / Not yet / Incomplete, with a CI exit code

# 5. freeze the evidence
python3 skills/proof-audit-trail/pack.py build --dir proof
```

In normal use you drive this through Claude with the `/proof-*` skills, which do the
recording for you; the commands above are the same thing by hand.

## Honest limits

Proof makes claims checkable; it does not make them true. A screenshot shows one
moment, a repro shows one machine, a measured number is only as good as the tool
that measured it, and a swarm of simulated users shares one model's blind spots.
"Ship" means the checks you required passed on the build you tested, not that no bug
exists. For anything high-stakes, have a person re-run the repros that matter. The
security skills are a structured review, not a guarantee, and not a substitute for a
professional audit where the stakes call for one. The finance tool produces
projections from your inputs, not a forecast, and is not financial advice.

## Tests and contributing

```bash
python3 -m unittest discover -s tests -v
```

Everything is standard-library Python, runs offline, and works on Windows, macOS and
Linux. `CONTRIBUTING.md` has the rules every skill follows; the test suite enforces
them, including that each skill is standalone, that a verified finding has real
evidence behind it, and that nothing in the code traces back to another project.

## Credits

Proof is **original work**. It takes inspiration, ideas and patterns, from a number
of excellent open-source projects, but copies no code, text, schema, name or asset
from any of them. With thanks to the authors of the prediction-engine, business-test,
app-clone, security-audit, web-testing, SEO and restraint packs whose ideas shaped
the approach, and to the OWASP Web, Mobile and LLM testing guides.

## License

MIT. Take it, change it, ship it.
