---
name: proof-core
description: >-
  The shared spine of the Proof pack: the evidence ledger every /proof-* skill
  writes to, the schema it must follow, the rules for when a finding counts, and
  the validator. Load this when another proof skill asks for "the ledger rules",
  when you need to record a finding, mark coverage or write a score by hand, when
  you want to check a proof/ folder is well formed, or when the user asks how
  Proof decides what counts as evidence. Every other proof skill works on its own;
  this one explains the files they have in common.
argument-hint: "[validate | explain]"
---

# proof-core

Proof's one rule: **nothing is asserted that the evidence does not show.** The
model never writes the verdict. It records what it found, how it checked, and what
it measured, in files a tool validates and another tool turns into the verdict.

What the user typed after `/proof-core`: `$ARGUMENTS`. `validate` runs the
validator on `proof/`. `explain` means walk the user through the files below.

## The files, all under `proof/` in the user's project

| file | written by | holds |
| --- | --- | --- |
| `target.json` | `/proof-plan` | what is under test, whether the user is authorised to test it, and `required_skills`: the skills the verdict waits for |
| `findings/<skill>/<id>.json` | any skill | one finding per file |
| `coverage/<skill>.json` | any skill | the checks that skill looked at: `covered`, `not_applicable` (with a reason) or `not_run` |
| `scores/<skill>.json` | measuring skills | numbers against budgets: `value`, `unit`, `budget`, `op` |
| `evidence/<skill>/...` | any skill | screenshots, logs, request captures, failing test output |
| `report.md`, `verdict.json` | `/proof-report` | the compiled report and the computed verdict |

The schema is `evidence-schema.json` in this folder. `ledger.py` enforces it on
every write, so a tool cannot write a malformed record.

## The three rules

1. **A finding is born `unverified`.** It becomes `verified` only through
   `ledger.verify()`, which needs one of: an evidence file that exists and is not
   empty (`evidence-file`), a repro that was run with its output saved as evidence
   (`repro-run`), or a human's note saying who checked and how (`manual`). A finding
   that turns out wrong is `reject()`ed with a reason and stays in the ledger as
   ruled out. **Only verified findings count toward the verdict.**
2. **Coverage is recorded, not assumed.** Each skill starts a coverage ledger with
   every check it knows `not_run`, then marks each `covered` (with evidence) or
   `not_applicable` (with a reason). The report can answer "did we look?" as well as
   "what broke?".
3. **Missing is never zero.** `num.py` turns an absent input into an error and reads
   numbers locale-safely ("12,50" is 12.5; "1,250" is refused as ambiguous unless
   the convention is given). No tool fills a gap with a default that changes the
   verdict.

## Every skill is optional

`required_skills` in `target.json` is the user's list. A skill not on it can still
be run on its own at any time, and its results still appear in the report; not
running it never counts against the verdict. Nothing in Proof depends on another
skill having run, except that `/proof-report` needs `target.json` to know what it
is reporting on.

## Using the ledger from a tool

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "proof-core"))
import ledger, num

f = ledger.new_finding("proof-web-e2e", "Checkout double-submits the order", "high",
                       category="flow:checkout", location="/checkout",
                       repro="python3 flows.py run checkout", evidence=["evidence/proof-web-e2e/checkout-double.png"])
ledger.write_finding("proof", f)
ledger.verify("proof", "proof-web-e2e", f["id"], by="evidence-file")
```

## Validate

```bash
python3 "${CLAUDE_SKILL_DIR}/validate.py" --dir proof
```

Exit 0 when clean. It checks the schema, that every evidence path exists and is
non-empty, that verified findings say how, that false positives say why, and that
file names match ids. Run it before `/proof-report`; the report refuses a folder
that does not validate.

## Honest limits

The ledger makes claims checkable; it does not make them true. A screenshot proves
a screen looked a certain way once, a repro proves a bug reproduced on this
machine, a measured number is only as good as the tool that measured it. Say which
of those a finding rests on, and when the stakes are high, have a person re-run
the repro.
