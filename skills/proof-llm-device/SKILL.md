---
name: proof-llm-device
description: >-
  Measures an app's on-device language model against a written budget and records
  every number as a pass or fail score: time to first token, prefill and decode
  speed in tokens per second, peak memory, slowdown over a sustained run (thermal
  throttling), battery drain per 100 prompts, and model file size. Reads files you
  captured (llama.cpp llama-bench JSON, Android dumpsys meminfo and dumpsys
  battery, or a measurements JSON or CSV), never touches the device itself, and a
  metric nobody measured is never a pass. Use when the user says "test the
  on-device model", "how fast is the LLM on the phone", "tokens per second",
  "time to first token", "does the model drain the battery", "on-device AI
  performance", "will it run on older phones", or "benchmark the local model".
argument-hint: "[captured files, or blank to set up the budget]"
---

# proof-llm-device

An app that runs its model on the phone lives or dies on numbers a demo hides: how
long the first word takes, how fast the rest arrive, how much memory it takes from
everything else, whether the phone slows it down once it gets warm, and how much
battery a real session costs. This skill turns those into scores against a budget,
each backed by the file it came from.

It reads captures; it does not run anything on the device. Capture on a real
device, ideally the lowest one you intend to support, plugged out of the charger.

What the user typed after `/proof-llm-device`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/llmdevice.py" budget --out proof/llm-device/budget.json --class mid
python3 "${CLAUDE_SKILL_DIR}/llmdevice.py" measure bench.json meminfo.txt runs.csv
python3 "${CLAUDE_SKILL_DIR}/llmdevice.py" record bench.json meminfo.txt runs.csv --budget proof/llm-device/budget.json --device "Pixel 8" --model "gemma-2b-q4"
python3 "${CLAUDE_SKILL_DIR}/llmdevice.py" record before.txt after.txt --prompts 100
python3 "${CLAUDE_SKILL_DIR}/llmdevice.py" status
```

## Step 1: the budget

`budget` writes defaults for a device class (`--class high`, `mid` or `low`). They
are starting points, not standards; edit them to the line your team decided on and
delete what you do not care about.

| metric | unit | high | mid | low |
| --- | --- | --- | --- | --- |
| `ttft_ms` time to first token | ms, at most | 800 | 1500 | 3000 |
| `prefill_tps` prompt processing | tokens/s, at least | 100 | 50 | 20 |
| `decode_tps` generation | tokens/s, at least | 15 | 8 | 4 |
| `peak_mem_mb` app memory (PSS) | MB, at most | 3000 | 2000 | 1200 |
| `throttle_pct` slowdown over a sustained run | %, at most | 25 | 30 | 40 |
| `battery_pct_per_100` drain per 100 prompts | %, at most | 5 | 8 | 12 |
| `model_mb` model file | MB, at most | 2500 | 1500 | 800 |

It will not replace a file it did not write unless you pass `--force`.

## Step 2: capture

Only on a device you own or are allowed to test.

- **Speed and model size, with llama.cpp:** push `llama-bench` and the model to the
  device and run `adb shell /data/local/tmp/llama-bench -m /data/local/tmp/model.gguf -p 512 -n 128 -o json > bench.json`.
  Prompt runs give `prefill_tps`, generation runs give `decode_tps`, and the model
  size gives `model_mb`.
- **Memory:** while the app is generating, `adb shell dumpsys meminfo <package> > meminfo.txt`.
  Take several captures during a long answer; the highest total PSS is used.
- **Battery:** unplug, `adb shell dumpsys battery > before.txt`, run 100 prompts in
  the app (or as many as you like), then `adb shell dumpsys battery > after.txt`.
  Pass both with `--prompts` set to the number you ran.
- **Anything else, including your app's own runtime (MediaPipe, LiteRT, MLC,
  Core ML):** log each run into a measurements file.

### The measurements file

JSON (a list of runs, or `{"runs": [...]}`) or CSV with a header row. Each run may
carry any of `ttft_ms`, `prefill_tps`, `decode_tps`, `peak_mem_mb`,
`throttle_pct`, `battery_pct_per_100` and `model_mb`; leave out what you did not
measure. A run may instead carry `battery_start_pct`, `battery_end_pct` and
`prompts`. Numbers are read locale-safely ("12,5" is twelve and a half).

```csv
run,ttft_ms,decode_tps,peak_mem_mb
1,820,11.2,1710
2,790,11.6,1695
```

Several runs give a median (what is scored) and a worst-10% figure (shown in the
note). A sustained run of 20 or more `decode_tps` values in order also gives
`throttle_pct` when it is not stated: the slowdown of the last five runs against
the first five.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/llmdevice.py" record bench.json meminfo.txt runs.csv --device "Pixel 8" --model "gemma-2b-q4"
```

Each budget metric becomes a coverage check. A measured one is covered and scored
against its budget, with the captured files copied into the evidence folder; one
with no input is marked not applicable with the reason. Each metric over budget
becomes a medium finding verified by its evidence. A metric that passes on a later
run drops its old finding. `--budget` points at your budget file (the mid-class
defaults are used without it).

## Honest limits

Numbers from one device, one model build and one prompt length describe that case
only; measure on the lowest device you support and with prompts the size your users
send. Time to first token includes whatever your app does before the model starts,
so measure it from the app, not only from a benchmark. Total PSS is the closest
single number to the memory the app costs, not the exact peak. Battery drain moves
with screen brightness, radio use and temperature, so keep those steady between
runs. This judges performance, not answer quality: pair it with `/proof-ai` for
safety and correctness.

## Output / next

`proof/coverage/proof-llm-device.json`, `proof/scores/proof-llm-device.json`,
findings under `proof/findings/proof-llm-device/`, the captured files under
`proof/evidence/proof-llm-device/`. Then `/proof-report`.
