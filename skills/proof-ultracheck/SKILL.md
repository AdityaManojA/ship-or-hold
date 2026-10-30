---
name: proof-ultracheck
description: >-
  The biggest check Proof runs. It reviews the app first (web, installable PWA,
  API, React Native or native mobile, LLM use, an on-device model, retrieval,
  database, sign-in, voice, tests) and then requires every skill that fits that
  app: everything in the full check plus the optional skills the review calls for,
  such as mutation testing, the browser matrix, visual regression, the launch
  checklist, PWA, React Native, mobile exploration and accessibility, store
  readiness, database security, auth, RAG quality, LLM cost, resilience, voice and
  licences. Ends with the computed verdict and the board meeting. Use when the
  user says "ultracheck", "ultra check", "test absolutely everything", "every
  check there is", "the deepest audit", or "leave nothing out".
argument-hint: "[--no-seo] [--skip proof-x] [--project path]..."
---

# proof-ultracheck

`/proof-full-check` runs the skills every app of its kind needs. The ultra check
goes further: it reviews the app, then adds each optional skill the review finds a
reason for, and makes all of them required, so the verdict waits for every one.
Nothing is added on a guess: a skill joins only when the review found what it
tests (a PWA manifest, an Expo app, a database, retrieval, a voice feature).

What the user typed after `/proof-ultracheck`: `$ARGUMENTS`

## The tool

This skill uses the full-check tool in ultra mode:

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" review --project .
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" plan --ultra --project .
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" status
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" next
```

## Step 1: the target

The ultra check needs `proof/target.json`. If it is missing, run `/proof-plan`
first: it records what is under test and asks whether you own it or have written
permission to test it.

## Step 2: review the app

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" review --project .
```

It reads the manifests and a sample of the source, in the folder and one level of
sub-folders (`frontend/`, `backend/`, `mobile/`, `apps/*`), and lists what it
found with the file behind each signal. Give `--project` once per folder when the
product spans several repositories (a web app and a mobile app, for example).
Show the review to the user before planning.

## Step 3: plan

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" plan --ultra --project .
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" plan --ultra --project ../web --project ../mobile
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" plan --ultra --skip proof-voice
```

The plan is the full check (the AI safety skill and the mobile skills join when the
review finds an LLM or a mobile app) plus, from the review:

| Found | Adds |
| --- | --- |
| a test suite | `/proof-mutation` |
| a web app or PWA | `/proof-compat` |
| a web or mobile UI | `/proof-visual`, `/proof-design-match` |
| an installable PWA | `/proof-pwa` |
| a React Native or Expo app | `/proof-react-native` |
| any mobile app | `/proof-mobile-explore`, `/proof-mobile-a11y`, `/proof-store-ready` |
| a model running on the device | `/proof-llm-device` |
| a database | `/proof-database` |
| sign-in | `/proof-auth` |
| retrieval (RAG) | `/proof-rag` |
| an LLM | `/proof-llm-cost` |
| a web, API or mobile app | `/proof-resilience` |
| voice or real-time audio | `/proof-voice` |
| always | `/proof-release`, `/proof-licenses` |

`--no-seo` is not available in ultra mode; skip SEO and GEO with
`--skip proof-seo --skip proof-geo` if the product is not public.

## Step 4: work through them

Run what `next` names, following that skill's own steps, then ask for the next one.
A check that cannot apply (no design exports for design match, no device for the
mobile skills) is marked not applicable with a reason inside the skill, so it stops
holding the verdict at Incomplete.

## Step 5: finish

Finish in this order, every time, without asking: `/proof-report` (`verdict.py`
writes `report.md` and `report.html`), then `/proof-board` whatever the verdict,
ending with `board.py tally --open`, which opens the meeting page, then
`/proof-audit-trail`.

## Honest limits

The ultra check is long and some skills need things only you can provide: a device
or emulator, design exports, database query results, usage records with prices,
someone to walk through a screen reader. The review reads files, not running
behaviour, so it can miss a feature or see one that is not live; check its list
before planning. Ship means every required check passed on the build you tested.

## Output / next

Writes `proof/review.json` (what the review found), `proof/fullcheck.json` (the
plan) and `required_skills` in `proof/target.json`. Finish with `/proof-report`,
`/proof-board` and `/proof-audit-trail`.
