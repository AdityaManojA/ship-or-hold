# The Proof skill — an evidence-first testing pack

## Context

The user compared two repos and wanted the best of each combined into one new
**open-source** Claude skill pack that does 10× more — "the best tester for all
scenarios" covering **audits, finance, SEO, and all sorts of web-app and mobile-app
testing** — folding in the best ideas from other GitHub projects. The user then
asked to **add an SEO skill** and fold in five more repos
([claude-seo](https://github.com/AgricIDaniel/claude-seo),
[seomachine](https://github.com/TheCraigHewitt/seomachine),
[geo-seo-claude](https://github.com/zubair-trabzada/geo-seo-claude),
[cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill),
[ponytail](https://github.com/DietrichGebert/ponytail)), and to commit the plan. The
working directory `D:\Projects\the-startup-skill` is an empty git repo (branch
`main`, no commits yet), so committing the plan creates the repo's first commit.

The two sources:
- **MiroFish** (82k⭐): a complete multi-step pipeline, agent swarm, report/chat
  layer — but no evidence its predictions predict. Keep the *orchestrated pipeline +
  swarm + report* idea.
- **founder-skill** (2.2k⭐, MIT): 11 skills + stdlib Python tools whose best trait
  is that **code computes the verdict, the model never just asserts it**; skills
  hand off through files; honest about limits; tiny install. Keep that discipline.

**The unifying idea:** apply founder-skill's "nothing is asserted that code hasn't
verified" to **testing/auditing software**. Every skill produces a **reproducible
artifact** (failing test, working PoC, measured number, screenshot) and the
**Ship / Not yet / Incomplete** verdict is **computed by a Python tool from those
artifacts**, never narrated. That is the defensible 10×: not more skills, but a
tester whose every verdict is re-runnable evidence.

Pack name: **`proof`**; commands `/proof-*`; plugin `proof-skill`; MIT.

## Decisions (already made, proactive)

1. **Name** `proof`, commands `/proof-*`.
2. **External tools** (Playwright, Maestro, Lighthouse, axe, semgrep, trufflehog,
   k6, MobSF, promptfoo…): **detect-and-fall-back**. Our own Python tools are
   stdlib-only, no network, no paid API, no cloud. `/proof-doctor` reports what's
   installed and what each missing tool unlocks.
3. **Sequencing**: one plan, built in **4 phases**, each phase a usable commit.
4. **License** MIT. OWASP material (WSTG/MASTG/ASVS, CC-BY-SA) is cited and linked,
   never copied in.
5. **Originality / clean-room — nothing traces back to any source repo.** This is a
   hard requirement: we borrow *ideas and patterns*, and write **100% original
   code, text, schemas and names** from scratch. No file, function, comment,
   docstring, prompt, SKILL.md paragraph, test, config or asset is copied or
   paraphrased from MiroFish, founder-skill, cloudflare/security-audit-skill,
   ponytail, the SEO packs, Trail of Bits, Strix, Anthropic's `webapp-testing`, or
   any other repo. "Model on X" / "borrow Y's idea" in this plan means *reimplement
   the concept cleanly*, never port the source. No source repo's name, command
   prefix (e.g. `founder-`, `geo-`), brand, logo or wording appears in our code or
   UI — only in the README credits, as plain prose links acknowledging inspiration
   (which every source's MIT/Apache/CC-BY-SA license permits). Rationale: the user
   wants zero licensing/attribution/IP exposure once this is public.

## What we borrow and improve

| From | Keep | Improve |
| --- | --- | --- |
| founder-skill | code-computed verdict; stdlib tools; file handoff; honest limits; `test_repo.py` layout; MIT | fix its 4 known bugs (issue #1): missing input → `Incomplete` not pass; clear stale state on re-init; locale-safe number parsing; every advertised flag must exist in its tool |
| MiroFish | orchestrated pipeline; agent swarm; report over results | swarm is stdlib + seeded + deterministic (no Zep/cloud); tools are synchronous so no "stuck running forever" |
| anthropics `webapp-testing` | `with_server.py` dev-server + Playwright recon→act; run scripts as black boxes | built-in browser (`mcp__Claude_Browser__*`) as the no-Playwright fallback |
| trailofbits/skills | `fp-check` verify-every-finding gate; `differential-review`; SARIF | verify gate is structural (a finding counts only when `status==verified`) |
| usestrix/strix | vuln taxonomy; "working PoC not false positives"; headless non-zero exit for CI | shapes our finding schema + CI mode |
| Lighthouse / axe-core | perf + WCAG as measured scores | parsed to a computed pass/fail |
| Maestro / mobile-mcp | mobile E2E | Maestro YAML when installed; else manual/mobile-mcp |
| promptfoo / garak / deepeval | LLM red-team + eval | `/proof-ai` for LLM-embedding apps |
| OWASP WSTG/MASTG/ASVS | test checklists (cited) | each item → an evidence artifact |
| **cloudflare/security-audit-skill** (52.7k⭐) | multi-phase audit; **machine-readable `report-schema.json`**; **coverage ledger** (every attack class covered or marked N/A); independent `validate-findings` + `validate-coverage-ledger` validators; attack-class reference docs (progressive disclosure) | this becomes the backbone of our security spine: adopt its report schema + coverage-ledger idea for the whole pack, so "did we even look?" is answerable, not just "what did we find?" |
| **ponytail** (180k⭐) | "the best code is the code you never wrote" — the lazy-senior-dev restraint lens; multi-harness packaging | a `proof-restraint` audit lens: flag code/features/deps that shouldn't exist (cheapest fix = deletion) |
| **claude-seo** (24k⭐) | technical SEO + E-E-A-T + schema + **GEO/AEO** + agent-readiness (llms.txt, WebMCP, Lighthouse agentic browsing) breadth | our SEO verdict is a **computed score** per checked element, not model opinion |
| **geo-seo-claude** (11k⭐) | GEO-first **citability scoring**, AI-crawler access analysis, documented scoring methodology | `geo_score.py` computes citability from crawled evidence |
| **seomachine** (7.5k⭐) | long-form content research → SERP-gap → optimize workflow; commands + specialist agents | informs `proof-seo`'s content-quality checks; we keep scope to auditing, not ghostwriting |

## Architecture

Mirror founder-skill's layout (so its `tests/test_repo.py` + `_load.py` port over):

```
.claude-plugin/plugin.json          # proof-skill, MIT, keywords
.claude-plugin/marketplace.json     # one plugin, source ./
README.md  LICENSE  .gitignore
skills/proof-<name>/SKILL.md        # one folder per skill
skills/proof-<name>/*.py            # stdlib tools where the skill owns numbers
skills/proof-<name>/*.example.*     # runnable example inputs
tests/                              # unittest, stdlib, one per tool + repo test
```

Shared spine:
- **Evidence ledger + coverage ledger** (`_ledger.py`, schema `report-schema.json`
  modelled on cloudflare's): every finding is
  `{id, skill, severity, title, evidence_path, repro, status}`,
  `status ∈ {verified, unverified, false_positive}`. **Counts toward the verdict
  only when `verified`** — the fp-check gate, made structural. Alongside it a
  **coverage ledger** records, per skill, which checks/attack-classes were *looked
  at* and which were marked N/A (cloudflare's idea): the report can then answer
  "did we even test this?", not just "what broke?". A stdlib `validate.py` checks
  both ledgers against the schema (cloudflare ships `validate-findings.cjs` /
  `validate-coverage-ledger.cjs`; ours is Python so the pack stays single-runtime).
- **Computed verdict** (`proof-report/verdict.py`): reads ledger + measured scores,
  prints **Ship / Not yet / Incomplete** with every failing check numbered;
  `Incomplete` when a required input is missing; `--check` exits non-zero for CI.
- **Swarm** (`proof-swarm/swarm.py`, stdlib + seeded): deal N scenario/persona/fuzz
  cards, one brief per agent, orchestrator fans out sub-agents in waves of ~10,
  tally. Used by exploratory testing, AI red-team, and consumer-style skills.
- **Honest limits** stated once per skill; **locale-safe missing-is-error** number
  parsing via shared `_num.py` (fixes founder bugs #1, #3; a test proves
  `"12,50"` ≠ 1250).

## The 23 skills (tool each owns)

**Core:** `proof-plan` (orchestrator, writes `proof/target.md`) · `proof-swarm`
(`swarm.py`) · `proof-report` (`verdict.py`) · `proof-doctor` (`doctor.py`, detects
optional tools).

**Web:** `proof-web-e2e` (Playwright recon→act / browser fallback, `flows.py`) ·
`proof-web-perf` (Lighthouse→`budget.py`) · `proof-a11y` (axe + `contrast.py`,
manual-review boundary) · `proof-api` (`schema_check.py`, schemathesis).

**Security & audit:** `proof-sec-web` (Strix taxonomy + per-finding `verify.py`,
SARIF/CVSS, semgrep) · `proof-sec-secrets` (trufflehog / `entropy.py`) ·
`proof-sec-deps` (`advisories.py`, pip/npm audit, trivy) · `proof-sec-diff`
(`diffscope.py` scopes sec skills to a git diff — fast CI gate).

**Mobile:** `proof-mobile-e2e` (`flowgen.py` → Maestro YAML / mobile-mcp) ·
`proof-mobile-audit` (`apkscan.py`: permissions, exported components, cleartext,
Firebase URLs; MobSF; cites MASTG).

**SEO & GEO:** `proof-seo` (technical + on-page SEO audit; `seo_score.py` computes a
score from crawled evidence — title/meta/headings/canonical/robots/sitemap/structured
data/internal links; Lighthouse SEO category when present; every deduction traces to a
checked element) · `proof-geo` (GEO/AEO — AI-search **citability** score, `llms.txt`
and agent-readiness, schema-for-AI, AI-crawler access checks; `geo_score.py`; cites a
documented scoring methodology like geo-seo-claude's).

**Depth:** `proof-mutation` (stryker/mutmut / stdlib AST `mutate.py`) · `proof-load`
(k6 / `loadplan.py` budgets) · `proof-explore` (swarm archetypes break flows) ·
`proof-restraint` (ponytail's lens: flags dead code, needless features, risky/unused
deps; `bloat.py` reports what to delete — the cheapest fix is the code you remove).

**AI/LLM:** `proof-ai` (`redteam.py` deals attack cards via the swarm:
jailbreak/injection/PII/hallucination; promptfoo/garak; cites OWASP LLM Top 10).

**Finance:** `proof-finance` (founder-skill CFO hardened: `unit_economics.py` with
missing-field-is-error + locale-safe parsing).

**Packaging:** `proof-audit-trail` (`pack.py`: bundles `proof/` into a timestamped,
SHA-256-manifested report folder so a reviewer can trust the run).

## Build phases (each a commit, each usable)

- **Phase 1 — spine + web + one security skill (MVP, proves the thesis):** scaffold
  (manifests, README, LICENSE, tests), `proof-plan`, `proof-swarm`+`swarm.py`,
  `proof-report`+`verdict.py`, `proof-doctor`, `proof-web-e2e`,
  `proof-sec-web`+`verify.py`, shared `_ledger.py`/`_num.py`, the
  `report-schema.json` + `validate.py` + coverage ledger, tests for every tool.
- **Phase 2 — security & audit depth + SEO:** `proof-sec-secrets`, `proof-sec-deps`,
  `proof-sec-diff`, `proof-web-perf`, `proof-a11y`, `proof-api`, **`proof-seo`**,
  **`proof-geo`**. (Security skills adopt cloudflare's attack-class coverage docs as
  progressive-disclosure reference files.)
- **Phase 3 — mobile + quality depth:** `proof-mobile-e2e`, `proof-mobile-audit`,
  `proof-mutation`, `proof-load`, `proof-explore`, **`proof-restraint`**.
- **Phase 4 — AI, finance, packaging + polish:** `proof-ai`, `proof-finance`,
  `proof-audit-trail`; README honest-limits + credits to every borrowed project; a
  worked end-to-end sample app; CI; final pass confirming the 4 founder-skill bug
  fixes and that every advertised flag exists.

## Patterns to reimplement cleanly (NOT copy — see decision 5)

The scratchpad clones are **read-only references for understanding the idea**; no
line is copied out of them. Each item below is a concept to rebuild from scratch:

- Repo layout + a frontmatter/flag-existence/no-copied-string test suite — the
  *shape* is a common convention; we write our own `tests/` and helpers.
- `verdict.py` — the concept of a tuple `(verdict, checks, numbers)` + a `--check`
  exit code, reading our ledger, missing-input → `Incomplete`. Written fresh.
- `swarm.py` — the concept of deal/seed/brief/check/tally in waves, never answering
  for an agent, per-caller brief templates. Written fresh.
- Dev-server + browser recon→act loop — the *technique* (start server, wait for
  idle, inspect DOM, act); our own code, with the built-in browser as fallback.
- `report-schema.json` + coverage ledger — the *concept* of machine-readable,
  independently-validated findings and a "did we look?" ledger. Our own schema,
  field names and Python validator.
- `unit_economics.py` — the *concept* of input-traceable unit economics with
  missing-is-error + locale-safe parsing. Written fresh.

## The commit (this step)

The user asked to **commit the plan**. Copy this plan into the repo as `PLAN.md`
(and a short `README.md` stub pointing to it), then make the repo's first commit on
`main` (no prior commits exist, so there is nothing to branch from):

```
PLAN.md        # this plan, verbatim
README.md      # one-paragraph stub: what Proof is + "see PLAN.md"
.gitignore     # Python + proof/ working dir
```

Commit message: `docs: plan the Proof evidence-first testing skill pack`, with the
`Co-Authored-By: Claude` trailer. No push unless asked.

## Verification (end to end, once built)

1. `python -m unittest discover -s tests -v` — all tools green, offline, no network.
2. On the bundled sample app: `/proof-plan` → `/proof-web-e2e` → `/proof-sec-web` →
   `/proof-report`; flip one evidence file's `status` from `verified` to
   `unverified` and watch the computed verdict change (the whole thesis — key test).
3. `verdict.py --check` exits non-zero when a check fails (CI contract).
4. `/proof-doctor` with none of the external tools installed: every skill still runs
   in fallback; doctor lists what each missing tool would add.
5. Reproduce the 4 founder-skill bugs against our code and confirm each is fixed.
6. `/plugin marketplace add <repo>` → `/plugin install proof-skill@proof-skill`;
   skills load as `/proof-*`.
7. **Originality gate (decision 5):** a `tests/test_originality.py` scans our tree
   for any source-repo name/command-prefix/brand outside the README credits, and a
   manual diff spot-check confirms no file mirrors a source file's structure or
   wording. The README credits are plain-prose acknowledgements with links only.

## Out of scope (v1)

No paid APIs, no cloud, no Zep, no web UI. No destructive security testing against
targets the user doesn't own; skills refuse unless `proof/target.md` declares the
target owned/authorised.
