---
name: proof-voice
description: >-
  Tests voice and real-time audio features (a voice assistant, a WebRTC call,
  speech in and out of a web or mobile app) with evidence. A checklist covers the
  microphone permission and what happens when it is denied, reply latency, barge-in
  (interrupting the reply), background and screen lock, audio route changes, poor
  networks, echo, releasing the microphone at the end, and transcript accuracy.
  Measures reply latency per turn, word error rate, and packet loss, jitter and
  round-trip time from a WebRTC stats dump against budgets. Optional, not part of a
  full check. Use when the user says "test the voice feature", "voice assistant
  testing", "how fast does it reply", "can I interrupt it", "test the call
  quality", "WebRTC stats", "word error rate", "transcript accuracy", or "does the
  mic turn off".
argument-hint: "[plan | latency | wer | webrtc | record | result | status]"
---

# proof-voice

A voice feature fails in ways a unit test never sees: the reply takes three
seconds to start, the user cannot cut it off, locking the phone leaves the
microphone on, a Bluetooth headset drops the session. This skill keeps a checklist
of those behaviours, records each result you run on your own app with its
evidence, and turns what can be measured into scores against a budget.

It is optional and never part of `/proof-full-check`. Run it on its own for any
app with speech or real-time audio.

What the user typed after `/proof-voice`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/voice.py" plan
python3 "${CLAUDE_SKILL_DIR}/voice.py" latency turns.csv --p95-ms 1500
python3 "${CLAUDE_SKILL_DIR}/voice.py" wer --ref ref.txt --hyp hyp.txt --max-wer 0.15
python3 "${CLAUDE_SKILL_DIR}/voice.py" webrtc webrtc_internals_dump.json --max-loss 3 --max-jitter 30 --max-rtt 300
python3 "${CLAUDE_SKILL_DIR}/voice.py" record --latency turns.csv --wer-ref ref.txt --wer-hyp hyp.txt --webrtc dump.json
python3 "${CLAUDE_SKILL_DIR}/voice.py" result barge-in pass --evidence evidence/proof-voice/barge-in.mp4
python3 "${CLAUDE_SKILL_DIR}/voice.py" result teardown fail --evidence evidence/proof-voice/teardown.mp4 \
  --title "Mic indicator stays on after ending the session" --verify
python3 "${CLAUDE_SKILL_DIR}/voice.py" na audio-route --note "web app only, the browser handles routing"
python3 "${CLAUDE_SKILL_DIR}/voice.py" status
```

## Step 1: the checklist

`plan` creates one coverage check per item and prints how to test each one and
what a pass looks like:

| Check | What to look for |
| --- | --- |
| `mic-permission` | Asked when the user starts talking, with a reason; never at launch |
| `permission-denied` | The app explains the mic is off and how to turn it on |
| `response-latency` | End of user speech to first reply audio, within budget |
| `barge-in` | Talking over the reply stops it promptly |
| `background-and-lock` | Background or lock mid-session behaves on purpose; mic indicator matches |
| `audio-route` | Speaker, earpiece, Bluetooth, wired switches keep the session |
| `poor-network` | Throttling or packet loss degrades gracefully and recovers |
| `echo` | On speaker the assistant never hears and answers itself |
| `teardown` | Mic and camera indicators turn off when the session ends |
| `transcript-accuracy` | Optional: word error rate within budget |

Test on your own app and your own device or emulator. Save a screen recording,
screenshot or log for each item under `proof/evidence/proof-voice/`, then record
it with `result <check> pass|fail --evidence <path>` (`--title`, `--severity`,
`--repro`, `--fix` and `--verify` for a failure, `--note` for context). Use `na`
with a `--note` for an item that cannot apply.

## Step 2: measure

**Reply latency.** Log each turn's end of user speech and first reply audio in
milliseconds (from the app's own timestamps, or by reading a screen recording
frame by frame) into a CSV or JSON with `speech_end_ms` and `first_audio_ms`, or a
ready `latency_ms` per turn. Ten turns or more. `latency` prints p50, p95 and max;
budgets are `--p95-ms` (default 1500) and `--p50-ms` (default 800).

**Transcript accuracy.** Read a known script aloud and save the app's transcript.
`wer` compares them word by word after lowercasing and dropping punctuation:
`--ref` and `--hyp` for one pair, or `--pairs` for a JSON or CSV of rows with `ref`
and `hyp`. Budget `--max-wer` (default 0.15).

**Call quality.** During a call in Chrome, open `chrome://webrtc-internals`, then
Create dump and save it. Or save `getStats()` output from the app as JSON.
`webrtc` reports packet loss, jitter and round-trip time per inbound stream, with
budgets `--max-loss` (percent, default 3), `--max-jitter` (ms, default 30) and
`--max-rtt` (ms, default 300).

## Step 3: record

`record` takes any of `--latency`, `--wer-ref` with `--wer-hyp` (or `--pairs`) and
`--webrtc`, with the same budget flags. It copies each input into evidence, writes
the scores, covers `response-latency`, `transcript-accuracy` and `poor-network`,
and writes a verified finding for anything over budget (dropped again once it
passes). A measurement you did not give is never a pass: its check stays not run,
and `transcript-accuracy` is marked not applicable until a transcript is given.

## Honest limits

The tool does not drive the app or listen to audio; you or another tool run each
case and capture the evidence. Latency is only as good as the timestamps you
logged. Word error rate depends on the script you read and the room you read it
in; test with the accents and noise real users have. WebRTC stats are a snapshot
of one call on one network.

## Output / next

`proof/coverage/proof-voice.json`, `proof/scores/proof-voice.json`, findings
under `proof/findings/proof-voice/`, inputs under `proof/evidence/proof-voice/`.
Pairs with `/proof-ai --mobile` for the assistant's answers and `/proof-mobile-a11y`
for the screens around it. Then `/proof-report`.
