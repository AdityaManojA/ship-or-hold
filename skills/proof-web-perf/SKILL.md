---
name: proof-web-perf
description: >-
  Measure a web page's load performance against a written budget and record every
  number as a pass or fail score in the evidence ledger. Covers Largest Contentful
  Paint, Cumulative Layout Shift, Total Blocking Time, First Contentful Paint, time
  to first byte, JavaScript weight, total transfer and request count. Reads a
  Lighthouse JSON result when Lighthouse is installed, or numbers you capture by
  hand from the browser into a small file. Use when the user says "is the site
  fast", "page speed", "performance budget", "core web vitals", "LCP", "CLS",
  "bundle size", "how heavy is the page", "run Lighthouse", or "perf check".
argument-hint: "[a URL to measure, or a path to a Lighthouse JSON result, or blank]"
---

# proof-web-perf

Write the budget down first, then measure, then let the numbers say pass or fail.
A metric passes only when a measured value sits inside its budget. A metric nobody
measured is not a pass; it is an error, or a gap the report shows.

What the user typed after `/proof-web-perf`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/budget.py" <command>   # template, record, from-lighthouse, skip, status
```

Every command takes `--dir` (default `proof`) before the command name, and `--help`.

## Step 1: know the target

If `proof/target.json` exists, read it for the URL and whether the target is marked
authorised. Measuring a local build (localhost / 127.0.0.1) is always fine. Only
point Lighthouse or a browser at a live, non-local URL the target marks authorised:
Lighthouse loads the page several times under throttling, which is traffic the owner
should expect. No target file is needed; this skill works on its own.

## Step 2: write the budget

```bash
python3 "${CLAUDE_SKILL_DIR}/budget.py" template --out budget.json
```

The shape is `${CLAUDE_SKILL_DIR}/budget.example.json`: one entry per metric with
`id`, `label`, `unit`, `budget` and `op`. The defaults are a common starting line:

| id | unit | budget | what it is |
| --- | --- | ---: | --- |
| lcp | s | <= 2.5 | Largest Contentful Paint |
| cls | score | <= 0.1 | Cumulative Layout Shift |
| tbt | ms | <= 200 | Total Blocking Time |
| fcp | s | <= 1.8 | First Contentful Paint |
| ttfb | ms | <= 800 | time to first byte of the document |
| total_js | kb | <= 300 | JavaScript transferred (kb = 1024 bytes) |
| total_transfer | kb | <= 1600 | everything transferred |
| requests | count | <= 80 | network requests during load |

Set the numbers with the user. Remove a metric nobody will act on rather than
loosening it until it cannot fail. A budget is a decision, not a law.

## Step 3: measure

Check what is installed with `/proof-doctor --for proof-web-perf` (or
`python3 skills/proof-doctor/doctor.py --for proof-web-perf`).

### With Lighthouse

```bash
lighthouse "http://localhost:3000/" --output json --output-path lhr.json --only-categories performance --chrome-flags="--headless=new"
python3 "${CLAUDE_SKILL_DIR}/budget.py" from-lighthouse --json lhr.json --budget budget.json
```

Use `--output-path`, not a shell redirect (a PowerShell redirect writes UTF-16; the
tool copes, but the file doubles in size). The tool maps audits to budget ids:
`largest-contentful-paint` and `first-contentful-paint` (ms to s), `cumulative-layout-shift`,
`total-blocking-time`, `server-response-time` (ttfb), `total-byte-weight` (bytes to kb),
and from the `network-requests` table the row count (requests) and the Script rows'
transfer size (total_js). An audit that is absent, errored or in an unexpected unit
leaves its metric "not measured" with the reason; nothing is guessed. The result file
is copied to `proof/evidence/proof-web-perf/` and every score points at it, and the
source line records the Lighthouse version, profile, URL and time. For a metric the
result does not carry, pass a hand-measured value with `--measured extra.json`, or
skip it with a reason (below).

### By hand (no Lighthouse)

Open the page in the built-in browser tools (or any browser's devtools), let it load
fully, scroll once, then run this in the console (the `javascript_tool` works):

```js
(() => {
  const nav = performance.getEntriesByType("navigation")[0];
  const res = performance.getEntriesByType("resource");
  const fcp = performance.getEntriesByType("paint").find(e => e.name === "first-contentful-paint");
  const kb = b => Math.round(b / 1024);
  const sum = rows => rows.reduce((s, r) => s + (r.transferSize || 0), 0);
  const out = {
    lcp: null, cls: 0, tbt: 0,
    fcp: fcp ? +(fcp.startTime / 1000).toFixed(2) : null,
    ttfb: Math.round(nav.responseStart - nav.requestStart),
    requests: res.length + 1,
    total_transfer: kb((nav.transferSize || 0) + sum(res)),
    total_js: kb(sum(res.filter(r => r.initiatorType === "script" || /\.m?js(\?|$)/.test(r.name)))),
  };
  new PerformanceObserver(l => { const e = l.getEntries().pop(); if (e) out.lcp = +(e.startTime / 1000).toFixed(2); })
    .observe({ type: "largest-contentful-paint", buffered: true });
  new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) out.cls = +(out.cls + e.value).toFixed(3); })
    .observe({ type: "layout-shift", buffered: true });
  new PerformanceObserver(l => { for (const e of l.getEntries()) if (!fcp || e.startTime > fcp.startTime) out.tbt += Math.round(Math.max(0, e.duration - 50)); })
    .observe({ type: "longtask", buffered: true });
  setTimeout(() => console.log(JSON.stringify(out, null, 2)), 1000);
})();
```

Save the printed object as `measured.json` (values may be numbers or strings; "2,5"
reads as two and a half), then:

```bash
python3 "${CLAUDE_SKILL_DIR}/budget.py" record --budget budget.json --measured measured.json \
  --source "devtools, 3rd of 3 reloads, cache disabled" --evidence evidence/proof-web-perf/run3.txt
```

Every id in the budget needs a value and every value needs an id in the budget; the
tool refuses anything else and writes nothing. `--evidence` is optional and must
name a file under `proof/` (a saved console output or a Network panel export). Two
honest notes on the snippet: `transferSize` is 0 for cross-origin resources that do
not send `Timing-Allow-Origin`, so total_transfer and total_js undercount third-party
weight (read the Network panel's transferred column for those); and `tbt` here is an
approximation from long tasks after first paint, where Lighthouse's number is the
reference. A `null` in the output is a metric the browser did not report; measure it
another way or skip it:

```bash
python3 "${CLAUDE_SKILL_DIR}/budget.py" skip tbt --note "no long-task timing in this browser" --budget budget.json
```

A skipped metric is recorded as not applicable with the reason, and `record` then
accepts a measured file without it. Skipping a metric that already has a score
removes that score; a skipped metric must not keep counting.

## Step 4: measure more than once

Run the measurement three times and record the middle run (or the worst, if the
user wants to be strict), and say which in `--source`. If two runs land on opposite
sides of a budget line, say so in the report rather than picking the one that passes.

## Step 5: read it back

```bash
python3 "${CLAUDE_SKILL_DIR}/budget.py" status
```

## What it writes

- `proof/scores/proof-web-perf.json`: one metric per budget entry with value, unit,
  budget, op, source and evidence. `/proof-report` lists every metric over budget as
  a blocker; this skill writes no findings, the score itself is the fact.
- `proof/coverage/proof-web-perf.json`: one check per budget metric, covered when
  measured, not_run (with the reason) when the run could not supply it, or
  not_applicable (with the reason) when skipped. A not_run check keeps the verdict
  Incomplete until it is measured or skipped. The ledger is rebuilt from the budget
  on every run, so an earlier run against other inputs never lingers.
- `proof/evidence/proof-web-perf/`: the Lighthouse result files.

## Honest limits

Lab numbers depend on the machine, the network and whatever else was running; the
same page can move twenty percent between two runs on the same laptop. One run is a
sample, not a distribution: three runs and the middle one is the least to do before
calling a number real. Lighthouse applies simulated throttling, so its numbers are
not what any one user saw; hand measurements from a fast machine on a fast link are
optimistic in the other direction. A budget is a decision, not a law: a number over
budget means the team's own line was crossed, nothing more and nothing less. Field
data from real users, where the site collects it, outranks everything here.

## Output / next

`proof/scores/proof-web-perf.json`, `proof/coverage/proof-web-perf.json`, evidence
under `proof/evidence/proof-web-perf/`. Then `/proof-report`, which turns a score
over budget into a Not yet; `/proof-web-e2e` if the slow page is also a flow worth
walking.
