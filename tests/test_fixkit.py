import contextlib
import copy
import csv
import io
import json
import os
import re
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

fk = load_tool("proof-fixkit", "fixkit.py")
ct = load_tool("proof-a11y", "contrast.py")
ledger = load_tool("proof-core", "ledger.py")
SKILL = "proof-fixkit"
EXAMPLE = os.path.join(SKILLS_DIR, "proof-fixkit", "fixkit.example.json")


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = fk.main(list(argv))
    return code, out.getvalue()


def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def add_finding(d, skill, title, severity, status="verified"):
    f = ledger.new_finding(skill, title, severity)
    ledger.write_finding(d, f)
    if status == "verified":
        ledger.verify(d, skill, f["id"], by="manual", note="test")
    elif status == "false_positive":
        ledger.reject(d, skill, f["id"], "test")
    return f["id"]


class FixKit(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write(self, spec):
        path = os.path.join(self.tp.base, "fixkit.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        return path

    def record(self, spec):
        code, out = run("--dir", self.tp.dir, "record", self.write(spec))
        self.assertEqual(code, 0, out)
        return out

    def read(self, rel):
        with open(os.path.join(self.tp.dir, rel), encoding="utf-8") as fh:
            return fh.read()

    def coverage(self):
        return {c["id"]: c["status"] for c in ledger.load_coverage(self.tp.dir, SKILL)["checks"]}

    def task_rows(self, text):
        rows = []
        for line in text.split("## The kit")[0].splitlines():
            m = re.match(r"^\| (\d+) \| [\d-]+ \| (.*?) \| (.*?) \| (\w+) \| ([SML]) \| (.*?) \|$", line)
            if m:
                rows.append({"day": int(m.group(1)), "task": m.group(2), "kit": m.group(3),
                             "severity": m.group(4), "effort": m.group(5), "who": m.group(6)})
        return rows

    def test_example_writes_every_file_and_coverage(self):
        self.record(example())
        text = self.read("business/fix-kit.md")
        self.assertEqual(text.splitlines()[0], "# 14-Day Fix Kit")
        for name in ("titles.md", "titles.csv", "colours.md", "whatsapp.md", "size-chart.md", "size-chart.csv",
                     "exchange-policy.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.tp.dir, "fix-kit", name)), name)
        self.assertEqual(self.read("fix-kit/exchange-policy.md").splitlines()[0],
                         "# Draft: have a lawyer or CA check this before publishing")
        cov = self.coverage()
        self.assertEqual(set(cov), {"titles-written", "colours-fixed", "whatsapp-scripts", "size-chart",
                                    "exchange-policy", "schedule"})
        self.assertTrue(all(v == "covered" for v in cov.values()), cov)
        m = ledger.load_all_scores(self.tp.dir)[0]["metrics"][0]
        self.assertEqual((m["id"], m["op"], m["budget"]), ("tasks_scheduled", ">=", 0))

    def test_new_colour_reaches_the_target_and_is_the_closest(self):
        for fg, bg, size, bold in (("#9a9a9a", "#ffffff", "normal", False), ("#e06c5a", "#ffffff", "normal", False),
                                   ("#4a6fa5", "#1b2a41", "normal", False), ("#f4978a", "#ffffff", "24px", True),
                                   ("#444444", "#000000", "normal", False)):
            [c] = fk.fix_colours([{"id": "p", "fg": fg, "bg": bg, "where": "x", "size": size, "bold": bold}])
            self.assertFalse(c["passes"], fg)
            target = 3.0 if c["text"] == "large" else 4.5
            self.assertEqual(c["target"], target)
            self.assertGreaterEqual(c["new_ratio"], target)
            self.assertGreaterEqual(ct.contrast_ratio(ct.parse_color(c["new"]), ct.parse_color(bg)), target)
            # Nothing of the same hue nearer in lightness passes, in either direction.
            h, s, l = fk.rgb_to_hsl(ct.parse_color(fg))
            step = int(round(c["lightness_change"] * 1000))
            for k in range(1, step):
                for sign in (-1, 1):
                    nl = l + sign * k / 1000.0
                    if 0 <= nl <= 1:
                        rgb = fk.hsl_to_rgb(h, s, nl)
                        self.assertLess(ct.contrast_ratio(rgb, ct.parse_color(bg)), target, (fg, k, sign))
            nh, ns, _ = fk.rgb_to_hsl(ct.parse_color(c["new"]))
            if s > 0.05:
                self.assertAlmostEqual(nh, h, delta=0.03)

    def test_passing_pair_is_never_altered_and_none_failing_is_not_applicable(self):
        [c] = fk.fix_colours([{"id": "ok", "fg": "#333333", "bg": "#ffffff", "where": "body", "size": "16px"}])
        self.assertTrue(c["passes"])
        self.assertNotIn("new", c)
        spec = example()
        spec["colours"] = [{"id": "ok", "fg": "#333333", "bg": "#ffffff", "where": "body", "size": "16px"}]
        self.record(spec)
        self.assertEqual(self.coverage()["colours-fixed"], "not_applicable")
        self.assertIn("Nothing to change", self.read("fix-kit/colours.md"))
        self.assertNotIn("#333333 |", self.read("fix-kit/colours.md"))

    def test_colours_from_the_accessibility_ledger(self):
        pairs = ct.load_pairs(self._pairs_file())
        ct.record_pairs(self.tp.dir, pairs, "pairs.json")
        spec = example()
        del spec["colours"]
        spec["colours_from"] = "proof"
        self.record(spec)
        text = self.read("fix-kit/colours.md")
        self.assertIn("#aaaaaa", text)
        self.assertIn("Already passing, unchanged", text)
        spec["colours_from"] = "proof"
        empty = TempProof().__enter__()
        try:
            code, out = run("--dir", empty.dir, "check", self.write(spec))
            self.assertEqual(code, 2, out)
        finally:
            empty.__exit__(None, None, None)

    def _pairs_file(self):
        path = os.path.join(self.tp.base, "pairs.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([{"id": "muted", "fg": "#aaaaaa", "bg": "#ffffff", "size": "normal", "where": "footer"},
                       {"id": "body", "fg": "#222222", "bg": "#ffffff", "size": "normal", "where": "body"}], fh)
        return path

    def test_title_and_description_lengths_and_whole_words(self):
        long_name = "Hand block printed sage green pure linen straight kurta with side pockets and wooden buttons"
        pages = [
            {"url": "u1", "kind": "product", "name": "Kurta", "category": "Women's kurta", "price": 899,
             "features": ["Cotton", "Side pockets", "Machine wash"]},
            {"url": "u2", "kind": "product", "name": long_name, "category": "Kurtas", "price": 1499.5,
             "features": ["Pure linen from a small mill", "Straight cut", "Calf length", "Wooden buttons",
                          "Hand block printed by artisans in a family workshop"]},
            {"url": "u3", "kind": "collection", "name": "Sale", "category": "Kurta sets",
             "features": ["Last season's prints", "Sizes XS to XXL"]},
            {"url": "u4", "kind": "home", "name": "Home", "category": "Linen and cotton clothing",
             "features": ["Made in small batches", "Ships across India"]},
            {"url": "u5", "kind": "policy", "name": "Returns", "features": ["Who can return", "How refunds work"]},
        ]
        for p in pages:
            t = fk.make_title(dict(p), "Loom and Lane", "Rs ")
            d = fk.make_description(dict(p), "Loom and Lane", "Rs ")
            self.assertTrue(30 <= len(t) <= 60, (len(t), t))
            self.assertTrue(120 <= len(d) <= 155, (len(d), d))
            source = set(re.findall(r"[\w']+", " ".join([p["name"], p.get("category", ""), "Loom and Lane Rs",
                                                          str(p.get("price", ""))] + p["features"]).lower()))
            filler = set("shop buy online official store at from the of in plain words a and read it before you "
                         "order see every size detail on page browse full range what is new questions about "
                         "contact applies to orders pieces are added this as they arrive 1 499 50 899 for "
                         "range".split())
            for w in re.findall(r"[\w']+", (t + " " + d).lower()):
                self.assertIn(w, source | filler, "%r is not a whole word from the inputs: %s / %s" % (w, t, d))
        self.record(dict(example(), pages=pages))
        rows = list(csv.DictReader(io.StringIO(self.read("fix-kit/titles.csv"))))
        self.assertEqual(len(rows), 5)
        for r in rows:
            self.assertEqual(int(r["new_title_chars"]), len(r["new_title"]))
            self.assertEqual(int(r["new_description_chars"]), len(r["new_description"]))
        self.assertIn("Rs 1,499.50", self.read("fix-kit/titles.md"))

    def test_schedule_orders_by_severity_then_effort_and_caps_three_a_day(self):
        d = self.tp.dir
        for i in range(8):
            add_finding(d, "proof-sec-web", "Injection in form %d" % i, "critical")   # effort L
        for i in range(8):
            add_finding(d, "proof-seo", "Missing alt text on page %d" % i, "critical")  # effort M
        for i in range(30):
            add_finding(d, "proof-market", "Weak positioning point %d" % i, "low")
        spec = example()
        spec["stage"] = "team"
        self.record(spec)
        text = self.read("business/fix-kit.md")
        rows = self.task_rows(text)
        self.assertEqual(len(rows), 42)
        days = [r["day"] for r in rows]
        self.assertEqual(days, sorted(days))
        for day in range(1, 15):
            self.assertLessEqual(days.count(day), 3)
        rank = [(fk.SEVERITY_ORDER.index(r["severity"]), fk.EFFORTS.index(r["effort"])) for r in rows]
        self.assertEqual(rank, sorted(rank))
        self.assertEqual(rows[0]["severity"], "critical")
        self.assertEqual(rows[0]["effort"], "M")
        self.assertIn("## After day 14", text)

    def test_solo_buckets(self):
        d = self.tp.dir
        add_finding(d, "proof-privacy", "No privacy policy linked from checkout", "high")
        add_finding(d, "proof-web-perf", "Home page takes 6 seconds to load", "high")
        add_finding(d, "proof-offer", "Urgency is not true", "high")
        spec = example()
        spec["stage"] = "solo"
        self.record(spec)
        rows = {r["task"]: r["who"] for r in self.task_rows(self.read("business/fix-kit.md"))}
        self.assertEqual(rows["Fix: No privacy policy linked from checkout"], "Ask a CA or lawyer")
        self.assertEqual(rows["Fix: Home page takes 6 seconds to load"], "Hire a freelancer")
        self.assertEqual(rows["Fix: Urgency is not true"], "Do today")
        self.assertEqual(rows["Register for GST before sales pass the threshold"], "Ask a CA or lawyer")
        self.assertTrue(any(v == "Ask a CA or lawyer" for k, v in rows.items() if "exchange policy" in k))

    def test_team_owner_seats_and_stage_from_target(self):
        d = self.tp.dir
        add_finding(d, "proof-finance", "Each order loses money", "high")
        add_finding(d, "proof-seo", "Duplicate titles", "high")
        with open(os.path.join(d, "target.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "x", "kind": "web", "authorised": True, "required_skills": [], "stage": "company"}, fh)
        spec = example()
        del spec["stage"]
        self.record(spec)
        text = self.read("business/fix-kit.md")
        self.assertIn("| Owner |", text)
        rows = {r["task"]: r["who"] for r in self.task_rows(text)}
        self.assertEqual(rows["Fix: Each order loses money"], "investor")
        self.assertEqual(rows["Fix: Duplicate titles"], "growth")
        self.assertTrue(set(rows.values()) <= set(fk.SEATS))
        os.remove(os.path.join(d, "target.json"))
        r = fk.analyse(spec, d)
        self.assertEqual(r["stage"], "team")

    def test_no_ledger_code_in_task_lines_and_sources_listed(self):
        d = self.tp.dir
        ids = [add_finding(d, "proof-a11y", "Low contrast (2.8:1) at footer-links", "medium"),
               add_finding(d, "proof-sec-web", "Session cookie missing secure flag", "high"),
               add_finding(d, "proof-seo", "Missing meta description", "medium")]
        skipped = [add_finding(d, "proof-seo", "Unverified guess about headings", "high", status="unverified"),
                   add_finding(d, "proof-seo", "Rejected thing about canonical tags", "high", status="false_positive")]
        self.record(example())
        text = self.read("business/fix-kit.md")
        plan, sources = text.split("## Sources")
        for line in plan.splitlines():
            if line.startswith("| "):
                for fid in ids + skipped:
                    self.assertNotIn(fid, line)
                self.assertNotIn("proof-", line)
        for fid in ids:
            self.assertIn(fid, sources)
        for fid in skipped:
            self.assertNotIn(fid, text)
        self.assertIn("Fix: Session cookie missing secure flag", plan)

    def test_whatsapp_scripts_are_plain_and_offer_nothing(self):
        self.record(example())
        text = self.read("fix-kit/whatsapp.md")
        self.assertIn("## Day 3 after delivery", text)
        self.assertIn("## Day 10 after delivery", text)
        self.assertIn("## Size help", text)
        for ph in ("{name}", "{order}"):
            self.assertIn(ph, text)
        scripts = "\n".join(re.findall(r"```text\n(.*?)```", text, re.S)).lower()
        for bad in ("discount", "coupon", "% off", "free gift", "cashback", "5 star", "five star", "reward"):
            self.assertNotIn(bad, scripts)
        self.assertIn("The Loom and Lane team", scripts.replace("the loom and lane team", "The Loom and Lane team"))

    def test_size_chart_rows_per_size_blank_cells(self):
        self.record(example())
        rows = list(csv.reader(io.StringIO(self.read("fix-kit/size-chart.csv"))))
        self.assertEqual(rows[0], ["product_type", "size", "Bust (cm)", "Waist (cm)", "Hip (cm)", "Length (cm)",
                                   "Width (cm)"])
        kurta = [r for r in rows[1:] if r[0] == "Kurta"]
        self.assertEqual([r[1] for r in kurta], ["XS", "S", "M", "L", "XL", "XXL"])
        self.assertTrue(all(c == "" for c in kurta[0][2:6]))
        md = self.read("fix-kit/size-chart.md")
        self.assertIn("## How to measure", md)
        self.assertIn("| Free size |", md)

    def test_missing_inputs_exit_two_and_dir_after_command(self):
        for key in ("start", "currency", "exchange", "pages"):
            spec = example()
            del spec[key]
            code, out = run("--dir", self.tp.dir, "check", self.write(spec))
            self.assertEqual(code, 2, (key, out))
        spec = example()
        spec["start"] = "next monday"
        self.assertEqual(run("check", self.write(spec))[0], 2)
        spec = copy.deepcopy(example())
        del spec["colours"]
        self.assertEqual(run("check", self.write(spec))[0], 2)
        out_file = os.path.join(self.tp.base, "t.json")
        self.assertEqual(run("template", "--out", out_file)[0], 0)
        code, out = run("record", out_file, "--dir", self.tp.dir)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.tp.dir, "business", "fix-kit.md")))

    def test_same_inputs_same_plan(self):
        r1 = fk.render_plan(fk.analyse(example(), self.tp.dir))
        r2 = fk.render_plan(fk.analyse(example(), self.tp.dir))
        self.assertEqual(r1, r2)
        self.assertIn("2026-12-01", r1)
        self.assertIn("2026-12-03", r1)


class RealRunLessons(unittest.TestCase):
    """What a run on a real store showed the kit getting wrong."""

    def test_white_text_on_a_brand_colour_moves_the_brand_colour(self):
        out = fk.fix_colours([{"id": "wish", "fg": "#ffffff", "bg": "#a27046", "where": "button", "size": "15px"}])[0]
        self.assertEqual(out["change"], "background")
        self.assertEqual(out["fg"], "#ffffff")
        self.assertGreaterEqual(out["new_ratio"], 4.5)
        coloured = fk.fix_colours([{"id": "p", "fg": "#a27046", "bg": "#ffffff", "where": "price", "size": "16px"}])[0]
        self.assertEqual(coloured["change"], "text")

    def test_findings_become_plain_jobs_with_pages_named(self):
        cases = {
            "13 of 20 pass because of trust": 'Answer the "trust" objection that 13 of 20 simulated buyers raised',
            "The owner is a single point of failure": "Line up a backup for the owner",
            "meta description missing on index.html": "Paste the meta description from the kit on the home page",
            "no h1 on collection-short-nighty.html":
                "Make the page's name its main heading (h1) on the collection short nighty page",
            "Nobody knows where 'GST registration' stands": "Find out where this stands: GST registration",
        }
        for title, job in cases.items():
            self.assertEqual(fk._plain(title, []), job)
        self.assertEqual(fk._plain("Something new went wrong", []), "Fix: Something new went wrong")

    def test_the_same_job_found_twice_is_one_task_and_meta_findings_are_skipped(self):
        def f(i, title, sev="medium"):
            return {"skill": "proof-geo", "id": i, "title": title, "severity": sev, "category": ""}
        tasks = fk.build_tasks([f("a", "Weak citability signal: facts"), f("b", "Weak citability signal: facts", "high"),
                                f("c", "Promotion is planned while a blocker is open: x/y")], [], [])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["severity"], "high")
        self.assertIn("(found in 2 places)", tasks[0]["task"])

    def test_one_job_on_several_pages_is_one_task_naming_them_all(self):
        def f(i, title, sev="medium", skill="proof-seo"):
            return {"skill": skill, "id": i, "title": title, "severity": sev, "category": ""}
        tasks = fk.build_tasks([f("a", "meta description missing on index.html"),
                                f("b", "meta description missing on shipping.html", "high"),
                                f("c", "meta description missing on collection.html"),
                                f("d", "Shopify is a single point of failure", skill="proof-ops"),
                                f("e", "The owner is a single point of failure", skill="proof-ops"),
                                f("g", "no h1 on product.html")], [], [])
        texts = [t["task"] for t in tasks]
        self.assertIn("Paste the meta description from the kit on the home, shipping and collection pages", texts)
        self.assertIn("Line up a backup for each of these: Shopify and the owner", texts)
        self.assertIn("Make the page's name its main heading (h1) on the product page", texts)
        self.assertEqual(len(tasks), 3)
        meta = next(t for t in tasks if "meta" in t["task"])
        self.assertEqual(meta["severity"], "high")
        self.assertEqual(meta["source"].count("proof-seo/"), 3)


if __name__ == "__main__":
    unittest.main()
