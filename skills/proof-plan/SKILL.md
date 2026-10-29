---
name: proof-plan
description: >-
  The starting point of a Proof run. Writes proof/target.json: what is under test
  (web, mobile, API or repo), the URLs or repo path, whether the user is authorised
  to test it, and required_skills, the list of skills the verdict waits for. It
  also suggests an order of skills for the target, but the user chooses what to
  require and every other skill stays optional. Use when the user says "start
  proof", "set up testing", "what should I test", "plan the testing", "describe the
  target", "test my app", or at the beginning of any Proof run before the other
  skills.
argument-hint: "[the app name, or leave blank to describe it in chat]"
---

# proof-plan

Before any testing, write down what is under test and what "done" means. Everything
else in Proof reads this file. It is also where the user confirms they are allowed
to test the target, which the security and load skills check before they act.

What the user typed after `/proof-plan`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/target.py" <command>
```

Below, `TARGET` means exactly that. State lives in `proof/target.json` and a
readable `proof/target.md`.

## Step 1: learn the target

Gather, in at most three questions in one message if the user has not already said:

- a name;
- the kind: `web`, `mobile`, `api`, `repo`, or `mixed`;
- where it lives: one or more URLs, and/or a repo path;
- for mobile, the platforms (`ios`, `android`).

## Step 2: authorisation (ask, do not assume)

Ask plainly whether the user owns this target or has written permission to test it.
Proof only tests what the user is allowed to test; it never probes third-party
systems. Record the answer:

```bash
TARGET init --name "Acme Shop" --kind web --authorised yes \
  --url http://localhost:3000 --url https://staging.acme.test --done "checkout works end to end and no high security findings"
```

`--authorised no` is recorded, and the security and load skills will refuse to act
against a live target until it is `yes`. Read-only skills still run.

## Step 3: suggest an order

```bash
TARGET recommend --kind web --public --has-llm
```

This prints a suggested, clearly optional order, filtered to the skills actually
installed. `--public` includes the SEO and GEO skills (skip it for something not
meant to be found, like an internal tool or a build still in stealth). `--has-llm`
includes the AI skill. The user decides; nothing is added for them.

## Step 4: choose what the verdict waits for

Let the user pick the required skills and record them:

```bash
TARGET require add proof-web-e2e proof-sec-web proof-api
TARGET require remove proof-geo
```

Explain the rule: `required_skills` is what `/proof-report` waits for before it
will say Ship. Every skill not on the list is optional. It can still be run on its
own at any time, its results still appear in the report, and not running it never
counts against the verdict. So a team building something they are not ready to make
public simply leaves SEO and GEO off the list; a team with no mobile app leaves the
mobile skills off. Nothing breaks.

## Step 5: hand off

Tell the user the next command, usually the first required skill, and that they can
run `/proof-doctor` to see which optional external tools are installed.

## Honest limits

A plan is not evidence. Nothing counts as tested until a skill records it in the
ledger and the finding is verified. Choosing not to require a skill is a decision
about scope, not a pass: the report shows exactly which skills ran and which did
not.

## Output / next

`proof/target.json`, `proof/target.md`. Then the required skills in turn, and
`/proof-report` to compile the verdict. `/proof-doctor` lists optional tools.
