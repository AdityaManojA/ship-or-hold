import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

i18n = load_tool("proof-i18n", "i18n.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = i18n.main(list(argv))
    return rc, out.getvalue()


BASE = {
    "app": {"title": "Welcome to the shop", "brand": "GitHub", "ok": "OK"},
    "cart": {"items": "You have {{count}} items", "greet": "Hello {name}",
             "total": "Total: %s", "save": "Save changes"},
    "footer": "All rights reserved",
}


class I18n(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.loc = os.path.join(self.tp.base, "locales")
        self.src = os.path.join(self.tp.base, "src")
        os.makedirs(self.loc)
        os.makedirs(self.src)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write(self, folder, rel, content):
        full = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return full

    def fr(self, **overrides):
        data = {
            "app": {"title": "Bienvenue dans la boutique", "brand": "GitHub", "ok": "OK"},
            "cart": {"items": "Vous avez {{count}} articles", "greet": "Bonjour {name}",
                     "total": "Total : %s", "save": "Enregistrer"},
            "footer": "Tous droits r\u00e9serv\u00e9s",
        }
        data.update(overrides)
        return data

    # ------------------------------------------------------------ parsing

    def test_nested_json_flattened(self):
        flat = i18n.flatten({"a": {"b": {"c": "x"}, "d": "y"}, "e": ["p", "q"]})
        self.assertEqual(flat, {"a.b.c": "x", "a.d": "y", "e.0": "p", "e.1": "q"})

    def test_locale_detection_from_names(self):
        self.assertEqual(i18n.locale_from_name("en"), "en")
        self.assertEqual(i18n.locale_from_name("messages_fr"), "fr")
        self.assertEqual(i18n.locale_from_name("pt_BR"), "pt_BR")
        self.assertIsNone(i18n.locale_from_name("messages"))

    def test_properties_parsing(self):
        text = ("# comment\n! also a comment\ngreeting = Hello {0}\nfarewell: Goodbye\n"
                "long = first \\\n    second\nescaped = caf\\u00e9\n")
        props = i18n.parse_properties(text)
        self.assertEqual(props["greeting"], "Hello {0}")
        self.assertEqual(props["farewell"], "Goodbye")
        self.assertEqual(props["long"], "first second")
        self.assertEqual(props["escaped"], "caf\u00e9")

    def test_po_parsing(self):
        text = ('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                '#: src/a.py:1\nmsgid "Hello %s"\nmsgstr "Bonjour %s"\n\n'
                'msgid "Two "\n"lines"\nmsgstr ""\n"Deux lignes"\n\n'
                '#, fuzzy\nmsgid "Guess"\nmsgstr "Devine"\n\n'
                'msgid "Untranslated"\nmsgstr ""\n')
        po = i18n.parse_po(text)
        self.assertEqual(po["Hello %s"], "Bonjour %s")
        self.assertEqual(po["Two lines"], "Deux lignes")
        self.assertEqual(po["Guess"], "")          # fuzzy counts as empty
        self.assertEqual(po["Untranslated"], "")
        self.assertNotIn("", po)                   # header skipped
        base = i18n.parse_po('msgid "Save"\nmsgstr ""\n', base=True)
        self.assertEqual(base["Save"], "Save")     # base falls back to msgid

    def test_properties_and_po_locales_compared(self):
        self.write(self.loc, "messages_en.properties", "a = Open the file\nb = Close the file\n")
        self.write(self.loc, "messages_de.properties", "a = Datei \\u00f6ffnen\n")
        res = i18n.analyse(self.loc, "en")
        de = res["locales"][0]
        self.assertEqual(de["locale"], "de")
        self.assertEqual(de["missing"], ["b"])
        po_dir = os.path.join(self.tp.base, "po")
        self.write(po_dir, "en.po", 'msgid "Open"\nmsgstr ""\n')
        self.write(po_dir, "fr.po", 'msgid "Open"\nmsgstr "Ouvrir"\n')
        res = i18n.analyse(po_dir, "en")
        self.assertEqual(res["locales"][0]["completeness"], 100.0)

    # --------------------------------------------------------- comparison

    def test_missing_extra_empty(self):
        fr = self.fr(footer="")
        del fr["cart"]["save"]
        fr["extra"] = "En trop"
        self.write(self.loc, "en.json", BASE)
        self.write(self.loc, "fr.json", fr)
        rep = i18n.analyse(self.loc, "en")["locales"][0]
        self.assertEqual(rep["missing"], ["cart.save"])
        self.assertEqual(rep["extra"], ["extra"])
        self.assertEqual(rep["empty"], ["footer"])
        self.assertEqual(rep["total"], 8)
        self.assertEqual(rep["translated"], 6)
        self.assertEqual(rep["completeness"], 75.0)

    def test_placeholder_mismatch(self):
        base = {"a": "Hello {name}", "b": "Total: %s", "c": "{{count}} items", "d": "Hi %(user)s"}
        loc = {"a": "Bonjour {nom}", "b": "Total : %d", "c": "{count} articles", "d": "Salut %(user)s"}
        rep = i18n.compare(base, loc)
        keys = sorted(m["key"] for m in rep["placeholder_mismatch"])
        self.assertEqual(keys, ["a", "b", "c"])

    def test_placeholders_extracted(self):
        self.assertEqual(i18n.placeholders("{{ count }} of {total} by %s, %(x)s and 100%%"),
                         ["%(x)s", "%s", "{total}", "{{count}}"])
        self.assertEqual(i18n.placeholders("{count, plural, one {# item} other {# items}}"), ["{count}"])
        self.assertEqual(i18n.placeholders("100% sure"), [])

    def test_identical_skips_short_brand_and_numbers(self):
        base = {"ok": "OK", "brand": "GitHub", "num": "2024", "save": "Save changes", "url": "https://x.io"}
        rep = i18n.compare(base, dict(base))
        self.assertEqual(rep["identical"], ["save"])

    # ------------------------------------------------------------- pseudo

    def test_pseudo_keeps_placeholders_and_expands(self):
        src = self.write(self.loc, "en.json", BASE)
        out = os.path.join(self.tp.base, "pseudo.json")
        rc, text = run("pseudo", src, "--out", out, "--expand", "0.5")
        self.assertEqual(rc, 0, text)
        with open(out, encoding="utf-8") as fh:
            pseudo = json.load(fh)
        items = pseudo["cart"]["items"]
        self.assertTrue(items.startswith("[") and items.endswith("]"))
        self.assertIn("{{count}}", items)
        self.assertIn("{name}", pseudo["cart"]["greet"])
        self.assertIn("%s", pseudo["cart"]["total"])
        self.assertGreater(len(pseudo["footer"]), len(BASE["footer"]) * 1.4)
        self.assertNotEqual(pseudo["footer"].strip("[]~"), BASE["footer"])  # accented
        self.assertEqual(i18n.placeholders(items), i18n.placeholders(BASE["cart"]["items"]))

    def test_pseudo_rejects_bad_expand(self):
        src = self.write(self.loc, "en.json", BASE)
        rc, _ = run("pseudo", src, "--out", os.path.join(self.tp.base, "p.json"), "--expand", "9")
        self.assertEqual(rc, 2)

    # ---------------------------------------------------------- hardcoded

    def test_hardcoded_jsx(self):
        self.write(self.src, "App.jsx",
                   "export function App() {\n"
                   "  const n = 1;\n"
                   "  return (\n"
                   "    <div>\n"
                   "      <h1>{t('home.title')}</h1>\n"
                   "      <p>Welcome back to the shop</p>\n"
                   "      <input placeholder=\"Search products\" />\n"
                   "      <img alt={t('logo')} />\n"
                   "    </div>\n"
                   "  );\n"
                   "}\n")
        hits = i18n.hardcoded(self.src)
        texts = {(h["line"], h["text"]) for h in hits}
        self.assertIn((6, "Welcome back to the shop"), texts)
        self.assertIn((7, "Search products"), texts)
        self.assertFalse(any("t(" in h["text"] for h in hits))
        self.assertEqual(len(hits), 2)

    def test_hardcoded_vue_ignores_template_expressions(self):
        self.write(self.src, "C.vue",
                   "<template>\n  <p>{{ $t('greeting') }}</p>\n  <span :title=\"label\">Read the docs</span>\n"
                   "</template>\n<script>\nexport default { data() { return { a: '<b>not text here</b>' } } }\n"
                   "</script>\n")
        hits = i18n.hardcoded(self.src)
        self.assertEqual([h["text"] for h in hits], ["Read the docs"])

    # ------------------------------------------------------------- record

    def test_record_writes_findings_coverage_score(self):
        fr = self.fr()
        fr["cart"]["greet"] = "Bonjour {nom}"           # placeholder mismatch
        del fr["cart"]["save"]                          # 1 of 8 missing = 12.5%
        self.write(self.loc, "en.json", BASE)
        self.write(self.loc, "fr.json", fr)
        self.write(self.loc, "de.json", BASE)           # complete, untranslated
        self.write(self.src, "a.html", "<p>Contact our support team</p>\n")
        rc, out = run("--dir", self.d, "record", self.loc, "--base", "en", "--src", self.src, "--min", "95")
        self.assertEqual(rc, 0, out)

        cov = ledger.load_coverage(self.d, "proof-i18n")
        ids = {c["id"]: c["status"] for c in cov["checks"]}
        self.assertEqual(ids, {"fr": "covered", "de": "covered", "hardcoded": "covered"})

        findings = {f["id"]: f for f in ledger.load_findings(self.d, "proof-i18n")}
        self.assertEqual(findings["i18n-placeholders-fr"]["severity"], "high")
        self.assertEqual(findings["i18n-missing-fr"]["severity"], "medium")
        self.assertIn("i18n-hardcoded", findings)
        self.assertNotIn("i18n-missing-de", findings)
        self.assertTrue(all(f["status"] == "verified" and f["category"] == "i18n" for f in findings.values()))

        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-i18n"][0]
        metrics = {m["id"]: m for m in scores["metrics"]}
        self.assertEqual(metrics["i18n_completeness_fr"]["value"], 87.5)
        self.assertEqual(metrics["i18n_completeness_fr"]["op"], ">=")
        self.assertEqual(metrics["i18n_completeness_fr"]["budget"], 95)
        self.assertFalse(ledger.metric_passes(metrics["i18n_completeness_fr"]))
        self.assertTrue(ledger.metric_passes(metrics["i18n_completeness_de"]))

        rc, out = run("--dir", self.d, "status")
        self.assertEqual(rc, 0)
        self.assertIn("i18n_completeness_fr", out)

    def test_record_small_gap_is_low_and_rerun_clears_stale(self):
        base = {"k%d" % i: "Message number %d here" % i for i in range(40)}
        fr = dict(base)
        del fr["k0"]                                    # 1 of 40 = 2.5%
        self.write(self.loc, "en.json", base)
        self.write(self.loc, "fr.json", fr)
        run("--dir", self.d, "record", self.loc)
        f = ledger.load_finding(self.d, "proof-i18n", "i18n-missing-fr")
        self.assertEqual(f["severity"], "low")
        self.write(self.loc, "fr.json", base)
        run("--dir", self.d, "record", self.loc)
        self.assertEqual(ledger.load_findings(self.d, "proof-i18n"), [])

    def test_missing_base_refused(self):
        self.write(self.loc, "fr.json", {"a": "b"})
        rc, out = run("--dir", self.d, "keys", self.loc, "--base", "en")
        self.assertEqual(rc, 2)
        self.assertIn("base locale", out)


if __name__ == "__main__":
    unittest.main()
