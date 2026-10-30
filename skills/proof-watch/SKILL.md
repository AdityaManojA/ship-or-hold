---
name: proof-watch
description: >-
  Re-runs checks when the code changes and tracks how the computed verdict moves
  over time. It polls a folder for changed files, runs the commands you give it
  (Proof tools or your own test commands) with a timeout and captured logs, then
  snapshots the verdict, the verified findings by severity, the gaps and the
  failing scores into a local history, and prints a timeline that marks every
  verdict change and every finding that appeared or went away. Use when the user
  says "watch mode", "re-run on save", "keep checking while I fix", "track the
  verdict over time", "did my fix change the verdict", "verdict history", "run
  the checks in CI", or "show me the trend".
argument-hint: "[snapshot | history | run --cmd \"...\" | watch --path src --cmd \"...\"]"
---

# proof-watch

Fixing findings is a loop: change the code, re-run the checks, see whether the
verdict moved. This skill makes that loop visible. Each time it runs, it computes
the verdict from the evidence ledger exactly as `/proof-report` does and appends one
row to a local history, so you can see when a finding appeared, when a fix made it
go away, and when the verdict flipped. Use it in a terminal while you work, or call
`run` once in CI so every build adds a row.

What the user typed after `/proof-watch`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/watch.py" snapshot --label "before fix"
python3 "${CLAUDE_SKILL_DIR}/watch.py" history --last 10
python3 "${CLAUDE_SKILL_DIR}/watch.py" history --json
python3 "${CLAUDE_SKILL_DIR}/watch.py" run --cmd "python -m pytest -q" --label "after fix" --timeout 600
python3 "${CLAUDE_SKILL_DIR}/watch.py" watch --path src --cmd "python -m pytest -q" --interval 2 --debounce 1
```

Every command takes `--dir` (default `proof`) and `--fail-on` (override the
target's fail_on). `run` and `watch` also take `--cwd` (where the commands run)
and `--fail-on-cmd` (exit 1 instead of 0 when the verdict is Ship but a command
failed). `watch` takes `--max-runs N` to stop after N runs and `--ignore <folder>`
(repeatable) to skip more folders. `.git`, `node_modules`, `__pycache__` and the
proof folder are always skipped.

## Workflow

1. **Have a ledger.** The verdict needs `proof/target.json`. Without it every
   snapshot reads Incomplete, which is honest but not useful. Run `/proof-plan`
   first if it is missing.
2. **Take a baseline.** `snapshot --label baseline` records where things stand
   before you change anything.
3. **Pick the commands.** Each `--cmd` is run in order through the shell. Use
   the Proof tools that write the ledger for what you are fixing, and your own
   test commands. They must be non-interactive: stdin is closed, so a command that
   waits for input fails or times out instead of hanging the loop.
4. **Choose how to run them.**
   - In a terminal while you fix: `watch --path src --cmd "..."`. It scans the
     folder every `--interval` seconds, comparing each file's modification time
     and size. When something changes it waits until the tree has been quiet for
     `--debounce` seconds (so a save of many files counts once), then does a run.
     Ctrl-C stops it cleanly; a command in flight is killed with its child
     processes.
   - In CI or by hand: `run --cmd "..." --label "commit abc123"`. The exit code is
     the verdict's: 0 Ship, 1 Not yet, 2 Incomplete, so a pipeline can gate on it.
5. **Read the history.** `history` prints one row per snapshot:

   | column  | meaning |
   | ---     | --- |
   | time    | when the snapshot was taken (UTC) |
   | label   | your label, `run N`, or the files that changed |
   | verdict | Ship, Not yet or Incomplete |
   | c/h/m/l | verified findings: critical / high / medium / low |
   | gaps    | reasons the verdict is Incomplete |
   | failing | measured scores over budget |
   | changes | what moved since the row before |

   `changes` says `verdict Ship -> Not yet` when the verdict flips, `findings +2`
   or `findings -1` when verified findings appear or go away (compared by skill
   and id, so swapping one finding for another shows as +1 and -1), and notes
   moves in gaps and failing scores. A row that says `-` changed nothing.
6. **Act on it.** When a row turns worse, open the run log it points to and the
   report from `/proof-report`. When a fix lands, the row shows the finding gone;
   the finding itself still has to be reopened, re-verified or rejected in the
   ledger by the skill that owns it.

A failed command is recorded in the history (exit code, timeout, seconds) but
does not change the verdict by itself. The verdict only moves when something
writes to the ledger. Add `--fail-on-cmd` when a failing test command should fail
the build even though no finding recorded it.

## What it writes

- `proof/watch/history.jsonl`: one JSON object per snapshot with time, label,
  verdict, verified findings by severity, gaps, failing scores and their ids, the
  list of verified finding ids, and for runs the run number, log path and each
  command's exit code.
- `proof/watch/history.md`: the same timeline as a table, rewritten by `history`.
- `proof/evidence/proof-watch/run-<n>.log`: everything each run's commands printed,
  with the command line and exit code before and after each one.

It does not write findings, coverage or scores of its own and is never a required
skill. It only reads the ledger the other skills write.

## Honest limits

- Polling compares modification time and size at each scan. Changes that happen
  and revert faster than the interval can be missed, and on a very large tree each
  scan costs real CPU and disk time. Point `--path` at the source folder, not the
  whole disk, and use `--ignore` for build output.
- A re-run proves the checks it runs, not the ones it does not. If `--cmd` runs
  only the unit tests, a security finding stays exactly as it was until the skill
  that owns it runs again. A green row means the commands you chose passed.
- The verdict comes from the ledger. A command that fails without writing to the
  ledger leaves the verdict where it was.
- The history is local to this machine and folder. Nothing is uploaded or merged;
  a CI job keeps its own history unless you save `proof/watch/` as an artifact.
- Commands run through the shell with your permissions. Only give it commands you
  would run yourself.

## Output / next

Writes `proof/watch/history.jsonl`, `proof/watch/history.md` and
`proof/evidence/proof-watch/run-<n>.log`. `/proof-report` gives the full report
behind any row, and `/proof-audit-trail` can pack the history and run logs with
the rest of the evidence.
