import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

voice = load_tool("proof-voice", "voice.py")
ledger = load_tool("proof-core", "ledger.py")

TURNS_CSV = "turn,speech_end_ms,first_audio_ms\n" + "\n".join(
    "%d,%d,%d" % (i, 1000 * i, 1000 * i + ms) for i, ms in enumerate(
        [600, 700, 650, 800, 900, 750, 700, 680, 720, 2600], 1)) + "\n"

DUMP = {"PeerConnections": {"1-1": {"stats": {
    "IT01A-statsType": {"statsType": "inbound-rtp", "values": "[\"inbound-rtp\"]"},
    "IT01A-kind": {"statsType": "inbound-rtp", "values": "[\"audio\",\"audio\"]"},
    "IT01A-packetsLost": {"statsType": "inbound-rtp", "values": "[0,10]"},
    "IT01A-packetsReceived": {"statsType": "inbound-rtp", "values": "[100,190]"},
    "IT01A-jitter": {"statsType": "inbound-rtp", "values": "[0.01,0.045]"},
    "CPa-state": {"statsType": "candidate-pair", "values": "[\"succeeded\"]"},
    "CPa-currentRoundTripTime": {"statsType": "candidate-pair", "values": "[0.1,0.12]"},
}}}}

GETSTATS = [
    {"type": "inbound-rtp", "id": "a", "kind": "audio", "packetsLost": 1, "packetsReceived": 999, "jitter": 0.008},
    {"type": "candidate-pair", "id": "p", "state": "succeeded", "currentRoundTripTime": 0.05},
]


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = voice.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Voice(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, text):
        p = os.path.join(self.tp.base, name)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text if isinstance(text, str) else json.dumps(text))
        return p

    def cov(self):
        return dict((c["id"], c) for c in ledger.load_coverage(self.d, "proof-voice")["checks"])

    def metrics(self):
        s = [x for x in ledger.load_all_scores(self.d) if x["skill"] == "proof-voice"]
        return dict((m["id"], m) for m in s[0]["metrics"]) if s else {}

    def test_latency_percentiles(self):
        values = voice.parse_latency(self.file("t.csv", TURNS_CSV))
        s = voice.latency_summary(values)
        self.assertEqual(s["turns"], 10)
        self.assertEqual(s["max"], 2600)
        self.assertEqual(s["p95"], 2600)
        self.assertEqual(s["p50"], 700)
        rows = [{"latency_ms": "1,25"}, {"latency_ms": 900}]
        self.assertEqual(voice.parse_latency(self.file("l.json", rows)), [1.25, 900.0])

    def test_latency_refuses_bad_rows(self):
        rc, out = run("latency", self.file("x.json", [{"turn": 1}]))
        self.assertNotEqual(rc, 0)
        self.assertIn("no turns", out)
        rc, out = run("latency", self.file("y.json", [{"speech_end_ms": 900, "first_audio_ms": 100}]))
        self.assertIn("before speech_end_ms", out)

    def test_wer(self):
        r = voice.wer_pairs([("Hello, world! It's me.", "hello word it's me")])
        self.assertEqual(r["words"], 4)
        self.assertEqual(r["edits"], 1)
        self.assertAlmostEqual(r["wer"], 0.25)
        self.assertEqual(voice.wer_pairs([("a b c", "a b c")])["wer"], 0.0)
        rc, out = run("wer", "--ref", self.file("r.txt", "turn on the lights"),
                      "--hyp", self.file("h.txt", "turn on the light"))
        self.assertEqual(rc, 0)
        self.assertIn("0.250", out)

    def test_webrtc_dump_and_getstats(self):
        stats = voice.parse_webrtc(self.file("dump.json", DUMP))
        w = voice.webrtc_worst(stats)
        self.assertAlmostEqual(w["loss_pct"], 5.0)
        self.assertAlmostEqual(w["jitter_ms"], 45.0)
        self.assertAlmostEqual(w["rtt_ms"], 110.0)
        self.assertEqual(stats["streams"][0]["kind"], "audio")
        w2 = voice.webrtc_worst(voice.parse_webrtc(self.file("gs.json", GETSTATS)))
        self.assertAlmostEqual(w2["loss_pct"], 0.1)
        self.assertAlmostEqual(w2["rtt_ms"], 50.0)
        rc, out = run("webrtc", self.file("empty.json", {"a": 1}))
        self.assertNotEqual(rc, 0)

    def test_record_measurements(self):
        rc, out = run("--dir", self.d, "record", "--latency", self.file("t.csv", TURNS_CSV),
                      "--webrtc", self.file("dump.json", DUMP))
        self.assertEqual(rc, 0, out)
        m = self.metrics()
        self.assertFalse(ledger.metric_passes(m["latency_p95_ms"]))
        self.assertTrue(ledger.metric_passes(m["latency_p50_ms"]))
        self.assertFalse(ledger.metric_passes(m["webrtc_loss_pct"]))
        self.assertTrue(ledger.metric_passes(m["webrtc_rtt_ms"]))
        cov = self.cov()
        self.assertEqual(cov["response-latency"]["status"], "covered")
        self.assertEqual(cov["poor-network"]["status"], "covered")
        self.assertEqual(cov["transcript-accuracy"]["status"], "not_applicable")
        self.assertEqual(cov["barge-in"]["status"], "not_run")
        ids = dict((f["id"], f) for f in ledger.load_findings(self.d, "proof-voice"))
        self.assertEqual(ids["voice-latency-over-budget"]["status"], "verified")
        self.assertIn("packet loss", ids["voice-network-over-budget"]["title"])
        # A later run within budget drops the latency finding and keeps the network score.
        good = self.file("good.csv", "latency_ms\n500\n600\n700\n")
        rc, out = run("--dir", self.d, "record", "--latency", good)
        self.assertEqual(rc, 0, out)
        ids = set(f["id"] for f in ledger.load_findings(self.d, "proof-voice"))
        self.assertNotIn("voice-latency-over-budget", ids)
        self.assertIn("webrtc_loss_pct", self.metrics())

    def test_record_wer_pairs_and_budget_flag(self):
        pairs = self.file("p.csv", "ref,hyp\nturn on the lights,turn on the lights\nwhat time is it,what time is\n")
        rc, out = run("--dir", self.d, "record", "--pairs", pairs, "--max-wer", "0.05")
        self.assertEqual(rc, 0, out)
        m = self.metrics()["wer"]
        self.assertAlmostEqual(m["value"], 0.125)
        self.assertEqual(self.cov()["transcript-accuracy"]["status"], "covered")
        self.assertTrue(any(f["id"] == "voice-wer-over-budget" for f in ledger.load_findings(self.d, "proof-voice")))

    def test_record_needs_input(self):
        rc, out = run("--dir", self.d, "record")
        self.assertNotEqual(rc, 0)
        self.assertIn("nothing to record", out)
        rc, out = run("--dir", self.d, "record", "--wer-ref", self.file("r.txt", "a"))
        self.assertIn("go together", out)

    def test_checklist_results(self):
        rc, out = run("--dir", self.d, "plan")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.cov()), len(voice.CHECKS))
        rc, out = run("--dir", self.d, "result", "barge-in", "pass", "--evidence", "evidence/proof-voice/missing.mp4")
        self.assertNotEqual(rc, 0)
        self.tp.write("evidence/proof-voice/barge.txt", "screen recording notes")
        rc, out = run("--dir", self.d, "result", "barge-in", "pass", "--evidence", "evidence/proof-voice/barge.txt")
        self.assertEqual(rc, 0, out)
        self.tp.write("evidence/proof-voice/teardown.txt", "mic indicator still on")
        rc, out = run("--dir", self.d, "result", "teardown", "fail", "--evidence", "evidence/proof-voice/teardown.txt",
                      "--title", "Mic stays on after the session ends", "--verify")
        self.assertEqual(rc, 0, out)
        f = ledger.load_findings(self.d, "proof-voice")[0]
        self.assertEqual(f["severity"], "high")
        self.assertEqual(f["status"], "verified")
        rc, out = run("--dir", self.d, "na", "audio-route", "--note", "web only")
        self.assertEqual(rc, 0)
        self.assertEqual(self.cov()["audio-route"]["status"], "not_applicable")
        rc, out = run("--dir", self.d, "result", "nope", "pass", "--evidence", "x")
        self.assertIn("no check", out)
        rc, out = run("--dir", self.d, "result", "echo", "fail", "--evidence", "evidence/proof-voice/teardown.txt")
        self.assertIn("--title", out)
        rc, out = run("--dir", self.d, "status")
        self.assertIn("barge-in", out)


if __name__ == "__main__":
    unittest.main()
