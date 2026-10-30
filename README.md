# Proof

Thirty-five Claude skills that test a product before you ship it, then present it to a board of directors. Free, MIT, no
signup, no API key, nothing to connect.

Most testing tools let the model say something passed. Proof does not. Every skill
records evidence (a screenshot, a failing test, a measured number, a captured
response) in a shared ledger, and the verdict is computed from that ledger by a
small Python tool: Ship, Not yet, or Incomplete. A finding only counts once it is
verified, and a missing input is an error, never a silent pass.

It covers web and mobile testing, API contracts, security, secrets, dependencies
and infrastructure config, privacy, performance, accessibility, browser
compatibility, visual regression, translations, transactional email, load, test
strength, SEO and AI-search readiness, LLM-feature safety, launch readiness, and
the unit economics behind the product. When the testing is done, a board of nine
directors (CEO, CTO, CISO, CFO, CMO, Sales, COO, CPO and Legal) hears the evidence
and votes on the launch, in an interactive meeting you can replay.

## Install

Paste this repo link into Claude and say `install skill`:

```
https://github.com/AdityaManojA/the-startup-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add AdityaManojA/the-startup-skill
/plugin install proof-skill@proof-skill
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/proof-skill:proof-plan` and so on. Copy the folders instead if you want plain
`/proof-plan`:

```bash
git clone https://github.com/AdityaManojA/the-startup-skill.git
cp -r the-startup-skill/skills/proof-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`.

The tools need Python 3.8 or newer. Nothing to pip install.

## Try it in one command

```bash
python3 examples/run_demo.py
```

This starts a small demo shop with problems planted on purpose, runs Proof against
it, and writes the verdict, an HTML report and a SARIF file. It catches all nine
planted problems. See `examples/README.md`.

## The thirty-five

| command | job | what it does |
| --- | --- | --- |
| `/proof-board` | Board of directors | Presents the evidence to nine directors (CEO, CTO, CISO, CFO, CMO, Sales, COO, CPO, Legal) who vote; computes Go, Go with conditions or Hold and writes an interactive meeting page. |
| `/proof-full-check` | Full check | Works out every skill that applies to the target, requires all of them, and walks through them in order to a verdict. |
| `/proof-full-check-no-seo` | Full check, no SEO | The same, without SEO and AI-search checks. For internal tools, stealth builds and private betas. |
| `/proof-plan` | Planner | Records what is under test, whether you are authorised to test it, and which skills the verdict waits for. |
| `/proof-report` | Verdict | Computes Ship, Not yet or Incomplete. Writes Markdown, HTML and SARIF reports, and compares runs. |
| `/proof-core` | Ledger | The shared evidence ledger, its schema and rules, and a validator. |
| `/proof-doctor` | Tool check | Shows which optional tools are installed and what each one adds. |
| `/proof-swarm` | Persona swarm | Deals a seeded crowd of sub-agent personas, sends each the same brief, and tallies their answers. |
| `/proof-watch` | Watch | Re-runs checks when the code changes and tracks how the verdict moves over time. |
| `/proof-web-e2e` | Web flows | Drives each critical user flow in a browser and records a pass with a screenshot, or a finding with a repro. |
| `/proof-web-perf` | Performance | Scores load speed against a budget, from Lighthouse or measured numbers. |
| `/proof-a11y` | Accessibility | Checks WCAG colour contrast, imports axe results, and walks a manual review checklist. |
| `/proof-compat` | Compatibility | A matrix of flows by browser and viewport, with one piece of evidence per cell. |
| `/proof-visual` | Visual | Compares screenshots against a baseline and flags layout changes, with a diff image. |
| `/proof-api` | API | Validates responses against the OpenAPI spec and generates awkward-input cases. |
| `/proof-sec-web` | Web security | Works through a checklist of weakness classes on an app you are authorised to test, with a verification gate. Imports Semgrep results. |
| `/proof-sec-secrets` | Secrets | Scans for committed keys and tokens. Values are always masked. |
| `/proof-sec-deps` | Dependencies | Matches installed packages against an advisory file and names the version to upgrade to. |
| `/proof-sec-diff` | Branch review | Scopes a security review to what a branch changed. |
| `/proof-infra` | Infrastructure | Flags risky settings in Dockerfiles, Compose, Kubernetes, Terraform and CI workflows. |
| `/proof-privacy` | Privacy | Trackers that load before consent, first-visit cookies, missing policy links, personal data in URLs. |
| `/proof-mobile-e2e` | Mobile flows | Plans mobile flows, generates Maestro files, and records each result with a screenshot. |
| `/proof-mobile-audit` | Mobile build | Reads an APK or IPA for permissions, cleartext traffic and embedded URLs. |
| `/proof-i18n` | Translations | Missing keys, placeholder mismatches, a pseudo-locale for overflow, hardcoded strings. |
| `/proof-email` | Email | Checks transactional emails for links, text parts and leaking dev hosts, and SPF and DMARC records. |
| `/proof-mutation` | Test strength | Plants small bugs one at a time and checks whether the tests catch them. |
| `/proof-load` | Load | Writes a load plan and scores the result (p95, p99, errors, throughput) against a budget. |
| `/proof-explore` | Exploratory | Sends a persona swarm through the product and turns what they hit into findings to reproduce. |
| `/proof-restraint` | Restraint | Finds dead code, unused dependencies and oversized files: what to delete. |
| `/proof-seo` | SEO | Audits on-page and technical SEO with a computed score. |
| `/proof-geo` | AI search | Scores how readable and citable each page is to AI assistants, and which AI crawlers robots.txt allows. |
| `/proof-ai` | LLM safety | Tests your own LLM feature across the OWASP LLM Top 10 categories. Imports promptfoo and garak results. |
| `/proof-finance` | Unit economics | Profit per sale, break-even, year one month by month, cash needed, and what-ifs. |
| `/proof-release` | Launch readiness | A launch-day checklist (alerts, backups, rollback, on-call) where each item needs proof. |
| `/proof-audit-trail` | Evidence pack | Packs the run into a folder with a SHA-256 for every file, and verifies it later. |

## How to use it

Start with `/proof-plan`. It writes `proof/target.json`, which every other skill
reads. Then either pick the skills you need or run a full check:

```
plan -> the skills you choose -> report -> board -> audit-trail
plan -> full-check -> report -> board -> audit-trail
```

Each skill writes into a `proof/` folder in your project: findings, coverage (what
was looked at), scores, and the evidence files behind them.

An example: an online shop, running locally, with a checkout.

1. **`/proof-plan`** asks for the name, kind and URL, and whether you own the app or
   have written permission to test it. It records the answer in `target.json`.
2. **`/proof-full-check`** picks the skills for a web app and requires them all.
3. **`/proof-web-e2e`** drives sign-in, add to cart and checkout. Checkout submits
   twice on a fast double click: that becomes a high finding, verified by its
   screenshot.
4. The remaining skills run in turn. `fullcheck.py next` names the next one;
   `status` shows what is done.
5. **`/proof-report`** computes the verdict. With the double-submit bug open it says
   Not yet, and lists the finding with its evidence. Fix it, re-run the flow, and
   it says Ship. `rundiff.py compare` shows exactly what changed between the runs.
6. **`/proof-board`** presents the evidence to the board. The CISO's veto fires on
   any verified high security finding, the CFO's on a sale that loses money, and so
   on; the rest is decided by vote. The meeting page shows the presentation, each
   director's statement with their risks linked to the evidence, the vote, the
   decision and an action list with owners.
7. **`/proof-audit-trail`** packs the run with a hash for every file, so a reviewer
   can confirm nothing changed.

Every skill also runs on its own. A skill you do not require never counts against
the verdict.

## The board meeting

`/proof-board` turns the evidence into a presentation and gives it to nine
directors, each a sub-agent with its own role:

| seat | judges | veto |
| --- | --- | --- |
| CEO | the overall call and the trade-offs | none (chairs) |
| CTO | does it work and keep working | core flows, API, load |
| CISO | security and customer data | security, secrets, dependencies, infra, AI |
| CFO | unit economics, cost of fixing versus delaying | unit economics |
| CMO | SEO, AI search, accessibility, email | none |
| Sales | can it be sold: demo, buyer security questions, pricing | none |
| COO | launch readiness, load, alerts, rollback | launch-blocking readiness items |
| CPO | the experience across devices and languages | none |
| Legal | privacy, accessibility, policies, AI | privacy |

Every high-severity risk a director raises must cite a finding or score from the
ledger, and a director cannot cite one that does not exist. Vetoes come from the
evidence itself, not from a director's mood: a verified high security finding holds
the launch whatever the votes say.

The result is `proof/board/meeting.html`, which runs the meeting step by step:
the opening presentation, then one agenda item per audit area (security, privacy,
infrastructure, user flows, reliability, reach, AI, code health, finance, launch
readiness), each with the team's slide, the directors who speak to it, and a
computed outcome (Blocker, Concerns, Clear or Not covered); then the board's
questions with the team's answers, the vote, the decision, and the action list
grouped by owner, which you can tick off. Next, Previous and the arrow keys move
through it; Show all puts it on one page.
`examples/board-meeting.html` is a real meeting held on the demo shop.

## In CI

The repo is also a GitHub Action. Add it to a workflow and every pull request gets
the verdict in the job summary, verified findings as code scanning alerts, and a
failed check unless the verdict is Ship:

```yaml
- uses: AdityaManojA/the-startup-skill@v1
  with:
    dir: proof
    fail-on: high
```

A full workflow to copy is in `examples/github-workflow.yml`.

## The tools

Standard-library Python. None touch the network.

```bash
python3 skills/proof-plan/target.py init --name "Shop" --kind web --authorised yes --url http://localhost:3000
python3 skills/proof-full-check/fullcheck.py plan              # or: plan --no-seo
python3 skills/proof-web-e2e/flows.py plan flows.json
python3 skills/proof-report/verdict.py --check                 # exit 0 Ship, 1 Not yet, 2 Incomplete
python3 skills/proof-report/verdict.py --sarif proof/proof.sarif
python3 skills/proof-report/htmlreport.py                      # proof/report.html
python3 skills/proof-report/rundiff.py save --label "after fix"
python3 skills/proof-audit-trail/pack.py build --dir proof
```

**`ledger.py`** is the shared core. A finding is born unverified and becomes
verified only through an evidence file that exists, a repro that was run, or a
note from the person who checked it. Every record is validated against the schema
on write.

**`verdict.py`** reads the ledger and computes the verdict. Incomplete when a
required skill has not reported or still has checks not run; Not yet when a
verified finding is at or above the fail-on severity or a score is over budget;
Ship otherwise. It also writes SARIF 2.1.0 for code scanning.

**`fullcheck.py`** picks the skills that apply to the target, requires them, and
tracks progress.

**`swarm.py`** deals persona cards from a profile (same seed, same cards), writes
one brief per agent, checks the answers and tallies them. It never answers for an
agent.

Every tool has `--help`. When an external tool such as Playwright, Lighthouse, axe,
Semgrep, Trivy, k6, Maestro, promptfoo or garak is installed, the matching skill
uses it or imports its output; when it is not, the skill falls back to a built-in
path. `/proof-doctor` shows which are present.

```bash
python3 -m unittest discover -s tests -v
```

## Fine print

**Evidence, not assurance.** Proof makes claims checkable; it does not make them
true. A screenshot shows one moment, a repro shows one machine, and a measured
number is only as good as the tool that measured it. Ship means the checks you
required passed on the build you tested, not that no bug exists.

**Only test what you are allowed to test.** The security and load skills act only
against a target marked as authorised in `target.json`, and refuse a live
third-party target. Checks are non-destructive.

**The swarm is simulated, not real users.** It is good at surfacing confusing
moments and odd inputs. It is not a measure of how real people behave. Nothing it
reports counts until it is reproduced against the real app.

**Not a substitute for a professional audit.** The security, privacy and
infrastructure skills are structured reviews. Where the stakes call for it, have a
professional confirm the critical findings.

**Not legal or financial advice.** The privacy skill reports technical facts, not
what the law in your jurisdiction requires. The finance skill projects from your
inputs; confirm costs with real quotes and the structure with an accountant.

## Files

```
action.yml                      the GitHub Action
examples/                       the planted-bug demo shop, run_demo.py, a board meeting, a workflow to copy
skills/proof-board/             SKILL.md, board.py, seats.md
skills/proof-full-check/        SKILL.md, fullcheck.py
skills/proof-full-check-no-seo/ SKILL.md
skills/proof-plan/              SKILL.md, target.py
skills/proof-report/            SKILL.md, verdict.py, htmlreport.py, rundiff.py
skills/proof-core/              SKILL.md, ledger.py, num.py, validate.py, evidence-schema.json
skills/proof-doctor/            SKILL.md, doctor.py
skills/proof-swarm/             SKILL.md, swarm.py, profile.example.json, brief.example.md
skills/proof-watch/             SKILL.md, watch.py
skills/proof-web-e2e/           SKILL.md, flows.py, serve.py, flows.example.json
skills/proof-web-perf/          SKILL.md, budget.py, budget.example.json
skills/proof-a11y/              SKILL.md, contrast.py, axe_import.py, checklist.md
skills/proof-compat/            SKILL.md, matrix.py, matrix.example.json
skills/proof-visual/            SKILL.md, visual.py
skills/proof-api/               SKILL.md, schema_check.py, openapi.example.json, cases.example.json
skills/proof-sec-web/           SKILL.md, secweb.py, semgrep_import.py, attack-classes.md
skills/proof-sec-secrets/       SKILL.md, entropy.py
skills/proof-sec-deps/          SKILL.md, advisories.py
skills/proof-sec-diff/          SKILL.md, diffscope.py
skills/proof-infra/             SKILL.md, infra.py
skills/proof-privacy/           SKILL.md, privacy.py
skills/proof-mobile-e2e/        SKILL.md, flowgen.py
skills/proof-mobile-audit/      SKILL.md, apkscan.py
skills/proof-i18n/              SKILL.md, i18n.py
skills/proof-email/             SKILL.md, mailcheck.py
skills/proof-mutation/          SKILL.md, mutate.py
skills/proof-load/              SKILL.md, loadplan.py
skills/proof-explore/           SKILL.md, explore.py
skills/proof-restraint/         SKILL.md, bloat.py
skills/proof-seo/               SKILL.md, seo_score.py, page.example.html
skills/proof-geo/               SKILL.md, geo_score.py, scoring.md, llms.example.txt
skills/proof-ai/                SKILL.md, redteam.py, eval_import.py
skills/proof-finance/           SKILL.md, unit_economics.py
skills/proof-release/           SKILL.md, release.py, checklist.md
skills/proof-audit-trail/       SKILL.md, pack.py
tests/                          the tests for every tool, and the end-to-end demo test
```

Your own files live in `proof/` in your project. The skills read each other's.

## Credit

Proof is original work. It draws on ideas from several open-source skill packs and
testing tools, and on the OWASP web, mobile and LLM testing guides, but copies no
code, text or assets from any of them.

## License

MIT. Take it, change it, ship it.
