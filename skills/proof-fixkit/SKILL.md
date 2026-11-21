---
name: proof-fixkit
description: >-
  Writes the 14-Day Fix Kit: exact page titles and meta descriptions, passing colours, WhatsApp review scripts, a size chart, a draft exchange policy and a day-by-day plan of every verified finding. Use for "fix kit", "what do I fix first", "14-day plan" or "give me paste-ready fixes".
argument-hint: "[path to a fixkit.json, or nothing to start one]"
---

# proof-fixkit

A list of findings tells a founder what is wrong. The Fix Kit hands them what to
paste in and when. Run it after the checks: it reads every verified finding in the
ledger and writes paste-ready files (page titles and meta descriptions, replacement
colours, WhatsApp scripts, a size chart, a draft exchange policy) plus a plan that
puts every finding and every kit item on a day from 1 to 14. Use it for an online
store that wants to act on the audit without hiring anyone first. Proof writes the
files under `proof/`. It never edits the store itself.

What the user typed after `/proof-fixkit`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/fixkit.py" template --out fixkit.json   # start from an example
python3 "${CLAUDE_SKILL_DIR}/fixkit.py" check fixkit.json            # print the plan, write nothing
python3 "${CLAUDE_SKILL_DIR}/fixkit.py" record fixkit.json           # write proof/fix-kit/ and the ledger
```

`--dir` (the proof folder, default `proof`) and `--force` go before or after the
command. A filled-in example sits beside the tool: `${CLAUDE_SKILL_DIR}/fixkit.example.json`.

## Step 1: gather the inputs

- `business`, `brand` and `currency` (the prefix money is printed with, such as
  `"Rs "`; there is no default).
- `start`: the first day of the plan, YYYY-MM-DD. Ask the user. The tool never reads
  the clock, so the same inputs always give the same plan.
- `stage`: `solo`, `team` or `company`. Leave it out to use the stage in
  `proof/target.json`; with neither, the plan is written for a team.
- `pages`: the pages the SEO check looked at, each `{url, kind, name, category,
  price, current_title, current_description, features}`. `kind` is home,
  collection, product or policy. `features` are short true phrases about the page,
  taken from the page itself. Never invent one.
- `colours`: the colour pairs `{id, fg, bg, where, size, bold}`, or
  `"colours_from": "proof"` to read the pairs the accessibility check recorded.
- `whatsapp`: `{sign_off, number_display}`; `number_display` is optional.
- `size_chart`: `{product_type, sizes, measurements}` for each product type.
- `exchange`: `{days, conditions, contact}`.
- `extra_tasks` (optional): `{task, effort, bucket, owner, severity}`. Effort is S,
  M or L. Severity defaults to medium; bucket and owner are worked out when left out.

A missing required field stops the tool with exit code 2.

## Step 2: check, then record

```bash
python3 "${CLAUDE_SKILL_DIR}/fixkit.py" check fixkit.json
python3 "${CLAUDE_SKILL_DIR}/fixkit.py" record fixkit.json
```

## How the files are made

- Titles are 30 to 60 characters and descriptions 120 to 155, built only from the
  page's name, category, price, features and the brand. Nothing is cut mid-word.
- A failing colour keeps its hue and saturation and moves lighter or darker by the
  smallest step that reaches 4.5:1 (3:1 for large text) on the same background. A
  pair that already passes is never changed.
- WhatsApp scripts ask for an honest review on day 3 and once more on day 10, with
  `{name}`, `{order}` and `{review_link}` to fill in. Nothing is offered in return
  for a review.
- The size chart has a row for each size and a blank centimetre cell for each
  measurement, with a note on how to measure.
- The exchange policy is headed "Draft: have a lawyer or CA check this before
  publishing" and lists the questions to take to them.
- The plan orders every task by severity (critical, high, medium, low), then effort
  (S, M, L), at most three a day. For a solo founder each task gets a bucket: Do
  today, Hire a freelancer (design, code or copy work that needs a specialist) or
  Ask a CA or lawyer (legal, tax and policy). For a team or company each task gets an
  owner seat: investor, growth, builder or operator. Tasks are plain sentences; the
  finding each came from is listed under Sources at the end.

## What it writes

- `proof/business/fix-kit.md`, starting with `# 14-Day Fix Kit`.
- Under `proof/fix-kit/`: `titles.md`, `titles.csv`, `colours.md`, `whatsapp.md`,
  `size-chart.md`, `size-chart.csv` and `exchange-policy.md`.
- A copy of the inputs under `proof/evidence/proof-fixkit/`.
- Coverage: `titles-written`, `colours-fixed` (not applicable when nothing fails),
  `whatsapp-scripts`, `size-chart`, `exchange-policy`, `schedule`.
- Score: `tasks_scheduled`, the number of tasks placed on the 14 days (for
  information, no bar). The kit writes no findings of its own.

## Honest limits

The kit is only as good as the findings behind it: an unverified or rejected finding
is left out, and a check that was never run adds nothing. Effort, bucket and owner
come from fixed rules by the skill a finding came from, so a quick fix can land in
the wrong bucket. A colour that passes the ratio can still clash with the brand. The
exchange policy is a draft, not legal advice. Titles and descriptions follow length
rules; they do not promise a ranking.

## Output / next

`proof/business/fix-kit.md` and the files under `proof/fix-kit/`, plus coverage and
the score under `proof/`. `/proof-business-plan` includes the section as written,
`/proof-board` reads the plan, and re-running the checks after day 14 shows what the
fixes changed.
