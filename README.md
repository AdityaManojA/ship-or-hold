# Proof

Fifty-one Claude skills that test a product before you ship it, then present it to a board of directors. Free, MIT, no
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
and votes on the launch, in a meeting that runs by itself.

## Install

Paste this repo link into Claude and say `install skill`:

```
https://github.com/AdityaManojA/ship-or-hold

install skill
```

Or as a plugin, inside a Claude Code session:

```
/plugin marketplace add AdityaManojA/ship-or-hold
/plugin install proof-skill@proof-skill
```

The same from a terminal (PowerShell, bash or zsh):

```bash
claude plugin marketplace add AdityaManojA/ship-or-hold
claude plugin install proof-skill@proof-skill
```

### Keep it up to date

To get new releases automatically, turn on auto-update for the marketplace: in a
Claude Code session open `/plugin`, go to Marketplaces, select `proof-skill` and
enable auto-update. Or add `autoUpdate` to the entry in `~/.claude/settings.json`:

```json
"extraKnownMarketplaces": {
  "proof-skill": {
    "source": { "source": "github", "repo": "AdityaManojA/ship-or-hold" },
    "autoUpdate": true
  }
}
```

Claude Code then checks for a new version when it starts; restart to load it. To
update by hand instead:

```bash
claude plugin marketplace update proof-skill
claude plugin update proof-skill@proof-skill
```

If you installed before the repo was renamed and adding the marketplace fails with
"its source doesn't match its extraKnownMarketplaces entry", change `repo` in that
entry from `AdityaManojA/the-startup-skill` to `AdityaManojA/ship-or-hold`.

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/proof-skill:proof-plan` and so on. Copy the folders instead if you want plain
`/proof-plan`:

```bash
git clone https://github.com/AdityaManojA/ship-or-hold.git
cp -r ship-or-hold/skills/proof-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`.

The tools need Python 3.8 or newer. Nothing to pip install.

## Start

Open Claude in your own project's folder and say what you want:

```
run a full check and present it to the board
```

Don't want SEO (an internal tool, a build in stealth, a private beta):

```
run a full check without SEO and present it to the board
```

Every check that fits the app, the optional ones too:

```
run the ultracheck and present it to the board
```

Each one reviews the app first (web, PWA, API, mobile, LLM, database, sign-in and so on) and shows you what it
found before planning.

Or start with `/proof-plan`, which records what is under test and asks you to
confirm you own it or have permission to test it, then pick the skills you need.
Everything Proof writes goes into a `proof/` folder in your project.

## The fifty-one

| command | job | what it does |
| --- | --- | --- |
| `/proof-board` | Board of directors | Nine directors, each applying the researched thinking of one person (Jobs, Vogels, Katz, Porat, Ogilvy, Patterson, Cook, Fadell, Smith), hear the evidence and vote. The meeting runs by itself in the chat and as a page; Go, Go with conditions or Hold is computed. |
| `/proof-full-check` | Full check | Every skill that applies to the target, including SEO and AI search, run in order to a verdict. |
| `/proof-ultracheck` | Ultra check | Reviews the app, then runs the full check plus every optional skill the review finds a reason for, all required. |
| `/proof-full-check-no-seo` | Full check, no SEO | The same, minus SEO and AI search, which then never hold the launch. For internal tools, stealth builds, private betas. |
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
| `/proof-database` | Database | Row-level security off or too open, public storage buckets, a service-role key in client code, risky migrations, schema smells. |
| `/proof-auth` | Auth | Password reset, session expiry, logout, token storage, cookie flags, JWT hygiene, OAuth redirects, rate limits, account enumeration. |
| `/proof-rag` | RAG quality | Retrieval hit rate, citations that support the answer, invented citations, abstaining when nothing fits, leaks between users. |
| `/proof-llm-cost` | LLM cost | Cost per call, conversation and user from usage records and your price file, response times, runaway conversations, uncapped calls. |
| `/proof-resilience` | Resilience | Offline, slow network, server errors, timeouts, cold starts, double submits and retry storms, with a HAR analyser. |
| `/proof-voice` | Voice | Mic permission, response latency, interrupting, background and lock, audio routes, poor networks, word error rate, WebRTC stats. |
| `/proof-store-ready` | Store readiness | App Store and Google Play rejection risks: account deletion, Sign in with Apple, target SDK, privacy manifest, icons, screenshots. |
| `/proof-licenses` | Licences | Dependency licences against your project's licence, and a CycloneDX software bill of materials. |
| `/proof-pwa` | PWA | Checks an installable web app: the manifest, icons, service worker, offline fallback and HTTPS, from saved files. |
| `/proof-react-native` | React Native | Audits a React Native or Expo project: secrets in the bundle, tokens in AsyncStorage, cleartext URLs, permissions without usage text, debug settings, OTA updates, expo-doctor results. |
| `/proof-mobile-explore` | Mobile explore | An agent explores the real app on your own device or emulator toward goals; crashes, ANRs and dead ends become findings with screenshots and logs. |
| `/proof-mobile-a11y` | Mobile accessibility | Unlabelled controls, touch targets under 48 dp / 44 pt, meaningless labels and large-text clipping, from the app's UI trees. |
| `/proof-design-match` | Design match | Compares screens with the design export and the last build: pixel difference, and elements that are new, missing, moved or resized. |
| `/proof-llm-device` | On-device LLM | Time to first token, tokens per second, peak memory, heat throttling, battery per 100 prompts and model size against a budget. |
| `/proof-mobile-audit` | Mobile build | Reads an APK or IPA for permissions, cleartext traffic and embedded URLs. |
| `/proof-i18n` | Translations | Missing keys, placeholder mismatches, a pseudo-locale for overflow, hardcoded strings. |
| `/proof-email` | Email | Checks transactional emails for links, text parts and leaking dev hosts, and SPF and DMARC records. |
| `/proof-mutation` | Test strength | Plants small bugs one at a time and checks whether the tests catch them. |
| `/proof-load` | Load | Writes a load plan and scores the result (p95, p99, errors, throughput) against a budget. |
| `/proof-explore` | Exploratory | Sends a persona swarm through the product and turns what they hit into findings to reproduce. |
| `/proof-restraint` | Restraint | Finds dead code, unused dependencies and oversized files: what to delete. |
| `/proof-slop` | Slop check | Finds copy, styling and code that read as machine-made: hype words, invented-looking numbers, stock gradients, banner comments, swallowed errors. |
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
was looked at), scores, and the evidence files behind them. Every skill also runs on
its own, and a skill you do not require never counts against the verdict.

## It runs by itself

You do not click through anything. Say what you want (for example "run a full check
and present it to the board") and Claude runs the skills, convenes the board, and
shows you the meeting as it happens:

- **In the chat or terminal**, the meeting appears step by step: the opening, each
  audit area with what the team showed, what each director said and the quote they
  based it on, the outcome of that item, then the vote, the decision and the actions.
  `board.py tally` prints the whole meeting; `board.py play` paces it in a terminal.
- **In the browser**, `proof/board/meeting.html` opens by itself at the end of the
  meeting (`board.py tally --open`) and plays the same meeting on its own, item by item, and stops on the actions. The opening introduces each director by
  full name, role and where they made their name; the questions play as an
  exchange; the ballots turn over one by one; and the decision lands as a stamp
  (a shaking HOLD! "Do not proceed.", or a GO with a drawn tick). Each step shows the short version and
  any quote that decided a call; the full points sit behind a click. Opening one holds the meeting on
  that step until it is closed, and the progress bar at the top jumps to any step.
  Scrolling down to read pauses it on that step; press Continue to move on.
  Nothing needs a click. In CI, or on a machine with no screen, it is not opened;
  open the file yourself (double-click it, or `start`, `open` or `xdg-open` it).

The only time Proof stops to ask is at `/proof-plan`, to confirm you own the target or
have permission to test it.

## Full check, or full check without SEO

| | `/proof-full-check` | `/proof-full-check-no-seo` |
| --- | --- | --- |
| Use it for | a product people should be able to find: a public site, an app with a landing page, anything you will market | something not meant to be found yet: an internal tool, a build in stealth, a private beta, an API with no public pages |
| Runs | every skill that fits the target's kind (web, API, mobile or repo) | the same set |
| Leaves out | nothing | `/proof-seo` (search engines) and `/proof-geo` (AI assistants and AI search) |
| Effect on the verdict | SEO and GEO scores count; an SEO score over budget makes the verdict Not yet | SEO and GEO are not required, so they can never hold the launch |
| Later | | run `/proof-seo` and `/proof-geo` on their own when you go public, or re-plan with `/proof-full-check` |

Leaving SEO out is a decision about scope, not a pass: the report and the board say
the area was not tested.

Neither full check includes the optional skills, which need setup, a second run or
a person, or only fit some projects: `/proof-mutation`, `/proof-compat`,
`/proof-visual`, `/proof-release`, `/proof-design-match`, `/proof-mobile-explore`,
`/proof-mobile-a11y`, `/proof-llm-device`, `/proof-pwa`, `/proof-react-native`, `/proof-database`, `/proof-auth`,
`/proof-rag`, `/proof-llm-cost`, `/proof-resilience`, `/proof-voice`, `/proof-store-ready` and
`/proof-licenses`. `/proof-ultracheck` adds every one the app's review calls for. Run any of them on its own; the full
check's plan lists them.

An example run: an online shop, running locally, with a checkout.

1. **`/proof-plan`** asks for the name, kind and URL, and whether you own the app or
   have written permission to test it.
2. **`/proof-full-check`** picks the skills for a web app and requires them all.
3. **`/proof-web-e2e`** drives sign-in, add to cart and checkout. Checkout submits
   twice on a fast double click: that becomes a high finding, verified by its
   screenshot.
4. The remaining skills run in turn.
5. **`/proof-report`** computes the verdict: Not yet, with the double-submit bug
   listed. Fix it, re-run the flow, and it says Ship. `rundiff.py compare` shows what
   changed between the runs.
6. **`/proof-board`** holds the meeting described below.
7. **`/proof-audit-trail`** packs the run with a hash for every file.

## The board meeting

`/proof-board` turns the evidence into a presentation and gives it to nine
directors. Each is a sub-agent that applies the documented thinking of one person,
researched from their own talks, interviews, books and letters:

| seat | lens | judges | veto |
| --- | --- | --- | --- |
| CEO | Steve Jobs | the overall call: focus, quality, saying no | none (chairs) |
| CTO | Werner Vogels | does it work and keep working, and does it read as made by people (reads `/proof-slop`) | core flows, API, load |
| CISO | Steve Katz | security and customer data | security, secrets, dependencies, infra, AI |
| CFO | Ruth Porat | unit economics, cost of fixing versus delaying | unit economics |
| CMO | David Ogilvy | claims, research, how the market meets it | none |
| Sales | John H. Patterson | can it be demonstrated and sold | none |
| COO | Tim Cook | readiness, operations, privacy posture | launch-blocking readiness items |
| CPO | Tony Fadell | the product experience | none |
| Legal | Brad Smith | privacy, trust, regulation | privacy |

How a director speaks, taken from the demo meeting. The CTO quotes because the
words are the call; the CFO, on the same point, quotes nothing:

```
CTO (Werner Vogels lens):
  - HIGH: The signup form posts to a plain HTTP endpoint, so customer personal
    data travels unencrypted.
    evidence: proof-privacy/privacy-insecure-forms-form-to-http-localhost-signup-posts-o
    quoting Werner Vogels: "Make use of it. Dance like nobody is watching. Encrypt
      like everyone is." (SDxCentral, 'Amazon's Werner Vogels: Dance Like Nobody's
      Watching. Encrypt Like Everyone Is', 2019-03-27)
CFO (Ruth Porat lens):
  - HIGH: The signup form posts personal data over plain HTTP. Every signup is a
    liability rather than revenue until this is fixed.
    evidence: proof-privacy/privacy-insecure-forms-form-to-http-localhost-signup-posts-o
```

Sixty verified quotes back the nine lenses, gathered from the people's own blogs,
books, talks, earnings calls, testimony and interviews. Quotes the research could
not confirm word for word are kept in the file marked "likely" and are never given
to a director; well-known misattributions (for example "Real artists ship" for Jobs,
or "If it doesn't sell, it isn't creative" for Ogilvy) were rejected.

The rules that keep this honest:

- Directors quote only the verbatim, sourced quotes in
  `skills/proof-board/personas.json`. `check` rejects any quote id that is not in
  that seat's list, and the meeting page links every quote to its source.
- Quotes are rare on purpose. A director may quote only when the person's words are
  the reason for a high-severity call backed by evidence, at most once per answer.
  `check` sends back any answer that quotes on a medium or low point, quotes twice,
  or quotes for colour. Most directors quote nothing.
- A director applies a person's thinking; it never writes as that person or claims
  they would endorse anything.
- Every high-severity point must cite a finding or score from the ledger; invented
  evidence is rejected.
- Vetoes come from the evidence, not from a director's view: a verified high security
  finding holds the launch whatever the votes say.

The meeting walks the audits in board order (user flows, reliability, security,
infrastructure, privacy and legal, reach, AI, code health, finance, launch
readiness). Each item shows the team's slide, the directors who speak to it, and a
computed outcome: Blocker, Concerns, Clear or Not covered. Then questions and
answers, the vote, the decision, and an action list grouped by owner.
`examples/board-meeting.html` is a real meeting held on the demo shop.

## In CI

The repo is also a GitHub Action. Add it to a workflow and every pull request gets
the verdict in the job summary, verified findings as code scanning alerts, and a
failed check unless the verdict is Ship:

```yaml
- uses: AdityaManojA/ship-or-hold@v1
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

**Your project is left as it was.** Proof writes into its own `proof/` folder.
Where a tool writes elsewhere (a starter script, a template, a report copy), it
refuses to replace a file it did not write unless you pass `--force`. Mutation
testing works on a temporary copy of the project, never your files. A load plan is
only written for a local address or a host `target.json` lists as authorised. The
scanners only read, and nothing is sent anywhere.

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
skills/proof-board/             SKILL.md, board.py, seats.md, personas.json
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
skills/proof-ultracheck/        SKILL.md
skills/proof-database/          SKILL.md, dbcheck.py
skills/proof-auth/              SKILL.md, auth.py
skills/proof-rag/               SKILL.md, rag.py
skills/proof-llm-cost/          SKILL.md, llmcost.py, prices.example.json
skills/proof-resilience/        SKILL.md, resilience.py
skills/proof-voice/             SKILL.md, voice.py
skills/proof-store-ready/       SKILL.md, store.py
skills/proof-licenses/          SKILL.md, licenses.py
skills/proof-pwa/               SKILL.md, pwa.py
skills/proof-react-native/      SKILL.md, rncheck.py
skills/proof-mobile-explore/    SKILL.md, mexplore.py
skills/proof-mobile-a11y/       SKILL.md, ma11y.py
skills/proof-design-match/      SKILL.md, design.py
skills/proof-llm-device/        SKILL.md, llmdevice.py
skills/proof-mobile-audit/      SKILL.md, apkscan.py
skills/proof-i18n/              SKILL.md, i18n.py
skills/proof-email/             SKILL.md, mailcheck.py
skills/proof-mutation/          SKILL.md, mutate.py
skills/proof-load/              SKILL.md, loadplan.py
skills/proof-explore/           SKILL.md, explore.py
skills/proof-restraint/         SKILL.md, bloat.py
skills/proof-slop/              SKILL.md, slop.py
skills/proof-seo/               SKILL.md, seo_score.py, page.example.html
skills/proof-geo/               SKILL.md, geo_score.py, scoring.md, llms.example.txt
skills/proof-ai/                SKILL.md, redteam.py, eval_import.py
skills/proof-finance/           SKILL.md, unit_economics.py
skills/proof-release/           SKILL.md, release.py, checklist.md
skills/proof-audit-trail/       SKILL.md, pack.py
tests/                          the tests for every tool, and the end-to-end demo test
```

Your own files live in `proof/` in your project. The skills read each other's.

## Release checklist: the meeting page

`meeting.html` is a single local file that the board writes into `proof/board/`. It
is a report, not a website: it is not deployed or meant to be indexed. The checklist
below says what was checked and what does not apply.

Build and handoff

- [x] DONE - Run commands are documented (`board.py brief`, `check`, `tally`, `play`); the board's tests pass (`tests/test_board.py`).
- [x] DONE - No dependencies added: standard-library Python writes the page; the page is one HTML file with inline CSS and JavaScript, no fonts or libraries fetched. Type uses fonts already on the system (Bahnschrift and Sitka on Windows, DIN and Charter on macOS) with plain fallbacks.
- [x] DONE - Assets: the director avatars are inline SVG drawn for this project; no third-party images. Quotes are attributed with their source and link.
- [x] DONE - Autoplay from the opening through every agenda item to the actions, and the action checklist, were checked in a browser on the demo meeting.
- [x] DONE - The opening roster, question exchange, ballot flips and the Hold and Go decision animations were checked in a browser; all are CSS only, with no animation loop.
- [ ] PENDING - The reduced-motion path (every animation off, ballots shown face up) is implemented but was not tested with the setting turned on.
- [x] DONE - Layout checked at desktop width and a 375px phone width (step labels collapse to the current one).
- [ ] PENDING - Keyboard and screen-reader pass: the progress steps and dropdowns are native buttons and details elements and focus is visible, but no full assistive-technology test has been run.
- [ ] PENDING - No measured performance figures; the page runs no animation loop and only fades text in, but nothing was profiled on low-end devices.

SEO and search readiness

- N/A - Indexing, sitemap, canonical URLs, Open Graph and structured data: the page is a local report and carries `noindex`; it is not published.
- [x] DONE - Semantic structure: one `h1`, a heading per step, `nav` for the agenda and `main` for the meeting.

Google Search Console

- N/A - The page is never deployed to a domain, so there is nothing to verify or submit.

Privacy, cookies, consent and legal

- [x] DONE - The page sends nothing anywhere: no analytics, no trackers, no requests. It stores only the action checklist's ticks in the browser's local storage, on that machine.
- N/A - Cookie banner and consent: no cookies and no third-party services.
- [ ] PENDING - The directors quote real people with attribution. The quotes are short, attributed and linked; whether your own use of a generated meeting is fair is your call, and the page states the directors are simulated and do not speak for anyone.

## Demo (optional)

This tests a small demo shop that ships with the repo, not your project. It is
only for seeing what a run looks like before you point Proof at your own code.
From a clone of this repo:

```bash
python3 examples/run_demo.py
```

It starts the demo shop with nine problems planted on purpose, runs Proof against
it, catches all nine, and writes the verdict, an HTML report and a SARIF file. See
`examples/README.md`.

## Credit

Proof is original work. It draws on ideas from several open-source skill packs and
testing tools, and on the OWASP web, mobile and LLM testing guides, but copies no
code, text or assets from any of them.

## License

MIT. Take it, change it, ship it.
