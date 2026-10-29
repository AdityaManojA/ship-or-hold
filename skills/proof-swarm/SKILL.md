---
name: proof-swarm
description: >-
  The scenario-agent swarm. Deals a crowd of personas from a profile (device,
  patience, skill, intent, or any dimensions you choose), sends the same brief to
  one sub-agent per persona in waves, checks every answer against a schema, and
  tallies the results by segment with the exact words agents used. The tool never
  answers for an agent and the same seed always deals the same crowd. Use when the
  user says "run a swarm", "simulate users", "scenario agents", "panel of agents",
  "try it as 100 different people", "exploratory test with personas", or when
  another proof skill (exploratory testing, AI red-team) asks for a swarm run.
argument-hint: "[--quick | --n 100] [--seed S]"
---

# proof-swarm

A crowd of simulated people, each a sub-agent with its own card, all reading the
same brief and answering on their own. It finds the cases a single tester misses:
the impatient phone user, the power user hammering the back button, the shopper who
pastes an address in one line. You are the moderator. You never answer for an agent.

What the user typed after `/proof-swarm`: `$ARGUMENTS`

## The tool

All bookkeeping goes through `swarm.py` in this skill's folder:

```bash
python3 "${CLAUDE_SKILL_DIR}/swarm.py" <command>
```

Below, `SWARM` means exactly that. Global options go before the command:
`--dir proof` (default) and `--run <name>` when more than one run exists. State
lives in `proof/swarm/<run-name>/`.

## Step 1: the profile

Write a profile JSON with the user. Shape: `profile.example.json` in this folder.

- `name`: a slug for the run.
- `purpose`: one line every agent reads.
- `dimensions`: 2 to 5 ways the crowd differs. Each option has a `value`, a
  `share` (the shares in a dimension sum to 1) and an optional `detail` line that
  makes the persona concrete.
- `answer`: the fields each agent returns. `outcome` names one boolean field; the
  tally reports its rate overall and per dimension value. Other fields are
  `string`, `number`, or `enum` with `values`.

Ground the dimensions in something real: who uses the product, what the analytics
or support tickets say. Show the user the profile in a few lines before dealing. A
wrong crowd makes every number wrong.

## Step 2: the brief

Write a brief in Markdown. Shape: `brief.example.md`. It can use `{name}`,
`{purpose}`, `{<dimension>}` and `{<dimension>.detail}` (also written
`{card.<dimension>}`). The tool always appends a fixed closing block that tells the
agent to answer honestly, not to be agreeable, not to invent facts, and exactly
where and in what JSON shape to write the answer. Do not write that part yourself.

Describe only what the agent could really see. Every agent reads the same brief.

## Step 3: deal and run

```bash
SWARM init --profile profile.json --brief brief.md --n 100
SWARM prompts
```

`init` deals the cards and records the seed. Running `init` again clears the
previous answers and briefs, so a changed brief can never inherit old answers.
`--keep-answers` keeps them only when the brief is byte-for-byte the same.

`prompts` writes one brief per agent and lists them in waves of 10. For each wave,
send ONE message with one Agent tool call per brief (called Task in older Claude
Code versions):

- `subagent_type`: `general-purpose`
- `description`: `swarm <id>`
- `prompt`: `Read <brief path> and follow it exactly. It is your whole brief.`

Wait for the whole wave before starting the next. After the last wave:

```bash
SWARM check      # lists missing or invalid answers
SWARM prompts    # re-briefs only those; run them once more
SWARM tally
```

If an agent printed its JSON in its reply instead of writing the file, file it with
`SWARM save <id>` and the reply on stdin; `save` refuses anything invalid.
`prompts` stops re-briefing an agent after `--max-attempts` tries (default 2) and
lists it as excluded; the tally counts it as excluded. Never write an answer for an
agent yourself.

Before a 100-agent run, tell the user it is 100 sub-agent calls and offer `--quick`
(20 agents) for a first look. Each agent writes one file into
`proof/swarm/<run>/answers/`; in the default permission mode that is one approval
per file, so suggest accept-edits mode for the run.

## Step 4: read it to the user

`tally` writes `results.json` and `results.md`:

1. The headline: how many answered, the outcome rate.
2. The segments that differ most: the dimension rows with the biggest gap.
3. The top answers in the agents' own words, as written, never paraphrased.
4. What to do next, and which skill turns it into evidence.

## Using it from another skill

A caller writes its own profile and brief, runs the same loop, then reads
`results.json`. The swarm does not write ledger findings itself: a simulated
answer is a lead, not evidence. The calling skill reproduces a reported problem
for real, saves the evidence, and only then records a finding through
`proof-core`.

## Honest limits

Every agent is the same model wearing a different card, so the crowd shares that
model's leanings. Agents lean agreeable, so read a positive outcome rate as an
upper bound. A swarm is good at surfacing cases, objections and wording you had
not thought of; it is not a measurement of how real people behave. Confirm what
matters with real users or a real test run.

## Output / next

`proof/swarm/<run>/`: `swarm.json`, `cards.json`, `briefs/`, `answers/`,
`attempts.json`, `results.json`, `results.md`. Exploratory testing and AI red-team
skills read `results.json`; problems that reproduce for real become findings, and
`/proof-report` reads those.
