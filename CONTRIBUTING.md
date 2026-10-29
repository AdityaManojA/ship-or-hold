# Contributing to Proof

Proof is a pack of Claude skills with small Python tools. These are the rules every
skill and tool in `skills/` follows. The test suite enforces most of them.

## The one rule

**Nothing is asserted that the evidence does not show.** A skill records findings,
coverage and measured scores through `skills/proof-core/ledger.py`; a finding
counts only once it is verified; `skills/proof-report/verdict.py` computes the
verdict. A skill never writes "this passed" in prose where a ledger entry belongs.

## Every skill stands alone

Any `/proof-*` skill can be run by itself, in any order, with nothing else having
run first. It must not require another skill's output. It *may* read
`proof/target.json` when present (to learn the target and whether the user is
authorised to test it), and it must refuse active testing against a live target
when `authorised` is `false`. Which skills the verdict waits for is the user's
choice, written in `required_skills`; a skill not on that list is optional and not
running it never counts against the verdict.

## Layout

```
skills/proof-<name>/SKILL.md       required
skills/proof-<name>/<tool>.py      optional, standard library only
skills/proof-<name>/*.example.*    runnable example inputs for the tool
skills/proof-<name>/*.md           reference files the skill loads on demand
tests/test_<tool>.py               one test module per tool
```

## SKILL.md

- Frontmatter: `name: proof-<name>` (matches the folder), `description: >-` of
  at least 300 characters that says what it does and lists the phrases a user
  would say to trigger it, and `argument-hint`.
- Body of at least 60 lines, in this order: what the skill is and when to use it
  (one paragraph), the tool commands (as `python3 "${CLAUDE_SKILL_DIR}/<tool>.py"`),
  the step-by-step workflow, what it writes under `proof/`, a short **Honest limits**
  section, and **Output / next** naming the files and the skills that commonly read
  them.
- Every `${CLAUDE_SKILL_DIR}/<file>` the text names must exist in the folder.
- External tools (Playwright, Maestro, Lighthouse, axe, semgrep, k6, ...) are
  detected and used when present and the skill says what it does without them.
  Nothing is required beyond Python 3.8.
- Say the limits once, plainly. No marketing.

## Tools

- Python 3.8+, standard library only, no network calls, no third-party imports.
- Run on Windows, macOS and Linux. Use `os.path`; never assume a shell.
- Call `ledger.console()` at the top of `main()` and use `ledger.OK` / `ledger.BAD`
  for console marks. Write files as UTF-8 explicitly.
- Import the core with

  ```python
  import os, sys
  sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "proof-core"))
  import ledger, num
  ```

- Numbers come in through `num.parse_number` / `num.require`. A missing input is
  an error, never a default that changes a result.
- Every flag a SKILL.md mentions exists in the tool. Every command has `--help`.
- Re-running a tool never silently reuses stale state from a previous run against
  different inputs; it either clears it or refuses.
- A tool that runs something (a repro, a server, an external scanner) has a
  timeout and cleans up what it started.

## Tests

- `python -I -m unittest discover -s tests -v` must pass offline, with no
  external tools installed.
- Use `tests/_load.py`: `load_tool(skill, filename)` imports a tool,
  `TempProof()` gives a throwaway `proof/` folder.
- Test the behaviour the SKILL.md promises, including the refusals.

## Originality

Proof is original work. Ideas and patterns from other projects are welcome
inspiration; their code, text, prompts, schemas, names and assets are not copied
or paraphrased into this repository. Credit inspirations in the README only, as
plain prose with a link. No other project's name, command prefix or branding
appears anywhere under `skills/` or `tests/`.

## Writing

Plain English, short sentences, no em dashes, no hype. Say what the thing does.
