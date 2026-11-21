#!/usr/bin/env python3
"""Run the business side of Proof end to end on made-up inputs and compile the plan.

Uses each business skill's own example inputs (a lunch-box delivery business that
does not exist), files sample buyer answers in place of a real panel, and writes
the three files a run ends with: summary.md, business-plan.md and one-pager.md.
Nothing leaves this machine and no sub-agent is started.

    python3 examples/run_business_demo.py                 # writes examples/business-demo/proof/
    python3 examples/run_business_demo.py --out /tmp/proof-business-demo

In a real run Claude gathers the inputs with you, a sub-agent plays each buyer,
the board meets, and Claude writes the summary. Here the buyer answers and the
summary are written by this script so the whole flow can be seen and tested, and
the board has not met, which the plan says under "What is not done yet".
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
DEMO = os.path.join(ROOT, "examples", "business-demo")

BUSINESS = ["proof-intake", "proof-market", "proof-buyers", "proof-pricing", "proof-offer", "proof-ads",
            "proof-finance", "proof-go-to-market", "proof-ops", "proof-fixkit"]

IDEA = """# Tiffin Loop (a made-up example)

- What it is: a weekday lunch-box subscription, cooked each morning and delivered to office floors.
- Who it is for: office workers in one business district who buy lunch out most days.
- What it sells and at what price: one lunch box a working day, sold by the week or the month.
- How it makes money: the subscription. There is no other revenue line.
- Where and how: one kitchen, one delivery run at lunchtime, orders by message.
- What it costs to run: kitchen rent, a cook, a rider, ingredients and packaging.
- What is already decided: start with one district and one menu a day.
- What is not known yet: how many people in the district would subscribe at this price.
"""

NUMBERS = {
    "business": "Tiffin Loop (a made-up example)", "currency": "Rs ", "unit": "lunch box", "price": "120",
    "days_per_month": 22,
    "variable": [{"label": "ingredients", "cost": "48"}, {"label": "box and label", "cost": "6"},
                 {"label": "delivery, per box", "cost": "8"}, {"label": "payment fee", "cost": "2"}],
    "fixed_monthly": [{"label": "kitchen rent", "cost": "12000"}, {"label": "cook", "cost": "14000"},
                      {"label": "rider", "cost": "6000"}, {"label": "gas, power, phone", "cost": "3000"}],
    "startup": [{"label": "kitchen equipment", "cost": "40000"}, {"label": "rent deposit", "cost": "24000"},
                {"label": "boxes and first stock", "cost": "8000"}],
    "plan_per_day": "60", "capacity_per_day": "90",
    "ramp_per_day": [12, 18, 24, 30, 36, 42, 46, 50, 54, 57, 59, 60],
}

# Sample answers, written by this script. Seven of twenty would buy; the rest pass
# for the reasons a real panel tends to give. They are fixtures, not buyers.
PASS = [("habit", "I already have a lunch place downstairs and I like the walk.",
         "I would have to change a routine that works.", "A week free, so I can see if I miss my usual place."),
        ("price", "It is more than I spend on the days I bring food from home.",
         "Paying for days I am out of the office.", "Being able to pause a day without losing it."),
        ("trust", "I do not know this kitchen and nobody I know has tried it.",
         "Food safety and whether it turns up on time.", "A colleague telling me it was good and on time."),
        ("need", "I only come to the office twice a week.",
         "A weekly plan does not fit two days.", "A plan for two days a week.")]
BUY = ("convenience", "Lunch at my desk at a fixed time saves me the queue every day.",
       "Whether the menu repeats too often.", "Nothing; I would try a week.")


def tool(skill, filename):
    path = os.path.join(SKILLS, skill, filename)
    spec = importlib.util.spec_from_file_location("bdemo_%s_%s" % (skill.replace("-", "_"), filename[:-3]), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(skill, filename, argv, show=True):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = tool(skill, filename).main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e.code) if not isinstance(e.code, int) else "")
    if show:
        for line in out.getvalue().strip().splitlines()[-3:]:
            print("    " + line)
    return code, out.getvalue()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def sample_answers(d):
    """File one sample answer per dealt buyer. Returns how many would buy."""
    briefs = os.path.join(d, "business", "panel", "briefs")
    answers = os.path.join(d, "business", "panel", "answers")
    os.makedirs(answers, exist_ok=True)
    ids = sorted(n[:-3] for n in os.listdir(briefs) if n.endswith(".md"))
    buys = 0
    for i, bid in enumerate(ids):
        yes = i % 3 == 0  # every third buyer
        code, reason, objection, change = BUY if yes else PASS[i % len(PASS)]
        buys += yes
        step = (i % 4) * 5
        obj = {"buys": yes, "times_first_month": 12 if yes else 0, "reason_code": code, "reason": reason,
               "objection": objection, "would_change_mind": change, "price_too_cheap": 60 + step,
               "price_bargain": 95 + step, "price_getting_expensive": 135 + step, "price_too_expensive": 175 + step}
        with open(os.path.join(answers, bid + ".json"), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
    return buys, len(ids)


def summary_text(r, buys, total):
    """The half page Claude writes in a real run, built here from the computed values."""
    blockers = [b for b in r["blockers"]]
    lines = [
        "# Summary: Tiffin Loop (a made-up example)", "",
        "**Verdict: %s.** This summary was written by the demo script from the computed values; in a real run "
        "Claude writes it from the sections." % r["verdict"], "",
        "Tiffin Loop is a weekday lunch-box subscription delivered to office floors in one business district. It "
        "is for office workers who buy lunch out most days.", "",
        "**The numbers.** Every measured number and its bar is in the key numbers table of the plan. Unit "
        "economics come from the inputs in the numbers section; the month-by-month ramp is an estimate, because "
        "nothing has been sold yet.", "",
        "**The buyers.** %d of %d sample buyers would subscribe. These are fixtures filed by the demo script, not "
        "a panel and not customers. The reasons to pass were habit, price, trust and need." % (buys, total), "",
        "**What blocks it.** %s" % ("Nothing is at or above the fail-on severity and every number is within its "
                                     "bar." if not blockers else "%d item(s), listed at the top of the plan."
                                     % len(blockers)), "",
        "**The biggest risk** is that the district does not hold enough subscribers at this price. The first "
        "real-world test in the go-to-market section settles it, with real people and a pass mark written first.",
        "",
        "**The board has not met.** Run /proof-board, then compile again.", "",
        "**The one thing to do this week:** run the first real-world test.",
    ]
    return "\n".join(lines) + "\n"


def run(out_dir, quiet=False):
    say = (lambda *a: None) if quiet else print
    show = not quiet
    d = os.path.abspath(out_dir)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    inputs = os.path.join(d, "inputs")
    os.makedirs(inputs)

    say("1. the target and the idea file")
    call("proof-plan", "target.py", ["--dir", d, "init", "--name", "Tiffin Loop (a made-up example)", "--kind", "web",
                                     "--authorised", "yes", "--url", "http://localhost:3000",
                                     "--done", "every business check within its bar"]
         + sum([["--require", s] for s in BUSINESS], []), show=show)
    _write(os.path.join(d, "business", "idea.md"), IDEA)

    say("1b. the four real numbers (made-up values standing in for the owner's)")
    call("proof-intake", "intake.py", ["--dir", d, "set", "--currency", "Rs ", "--unit-cost", "62", "--ad-spend",
                                       "0", "--sessions", "1200", "--orders", "60", "--aov", "120",
                                       "--period", "last 30 days"], show=False)
    call("proof-intake", "intake.py", ["--dir", d, "record"], show=show)

    say("2. market: competitors, complaints, the gap")
    call("proof-market", "market.py", ["--dir", d, "template", "--out", inputs], show=False)
    call("proof-market", "market.py", ["--dir", d, "record", os.path.join(inputs, "competitors.csv"),
                                       os.path.join(inputs, "market.json")], show=show)

    say("3. buyers: deal the panel, file sample answers, tally")
    call("proof-buyers", "buyers.py", ["--dir", d, "template", "--out", inputs], show=False)
    call("proof-buyers", "buyers.py", ["--dir", d, "init", "--customer", os.path.join(inputs, "customer.json"),
                                       "--pitch", os.path.join(inputs, "pitch.md"), "--n", "20", "--seed", "7"],
         show=False)
    call("proof-buyers", "buyers.py", ["--dir", d, "prompts"], show=False)
    buys, total = sample_answers(d)
    call("proof-buyers", "buyers.py", ["--dir", d, "tally"], show=show)

    say("4. pricing")
    call("proof-pricing", "pricing.py", ["--dir", d, "template", "--out", os.path.join(inputs, "pricing.json")],
         show=False)
    call("proof-pricing", "pricing.py", ["--dir", d, "record", os.path.join(inputs, "pricing.json")], show=show)

    say("5. the offer")
    call("proof-offer", "offer.py", ["--dir", d, "template", "--out", os.path.join(inputs, "offer.json")], show=False)
    call("proof-offer", "offer.py", ["--dir", d, "record", os.path.join(inputs, "offer.json")], show=show)

    say("5b. the ads")
    call("proof-ads", "ads.py", ["--dir", d, "template", "--out", os.path.join(inputs, "ads")], show=False)
    call("proof-ads", "ads.py", ["--dir", d, "record", os.path.join(inputs, "ads", "ads.json")], show=show)

    say("6. the numbers")
    with open(os.path.join(inputs, "numbers.json"), "w", encoding="utf-8") as fh:
        json.dump(NUMBERS, fh, indent=2)
    call("proof-finance", "unit_economics.py", ["--dir", d, os.path.join(inputs, "numbers.json"), "--record"],
         show=show)

    say("7. go to market")
    call("proof-go-to-market", "gtm.py", ["--dir", d, "template", "--out", os.path.join(inputs, "gtm.json")],
         show=False)
    call("proof-go-to-market", "gtm.py", ["--dir", d, "record", os.path.join(inputs, "gtm.json")], show=show)

    say("8. operations")
    call("proof-ops", "ops.py", ["--dir", d, "template", "--out", os.path.join(inputs, "ops.json")], show=False)
    call("proof-ops", "ops.py", ["--dir", d, "record", os.path.join(inputs, "ops.json")], show=show)

    say("8b. the fix kit and the 14-day plan")
    call("proof-fixkit", "fixkit.py", ["--dir", d, "template", "--out", os.path.join(inputs, "fixkit.json")],
         show=False)
    call("proof-fixkit", "fixkit.py", ["--dir", d, "record", os.path.join(inputs, "fixkit.json")], show=show)

    say("9. the verdict, the summary and the plan")
    call("proof-report", "verdict.py", ["--dir", d], show=False)
    plan = tool("proof-business-plan", "plan.py")
    r = plan.verdict.compute(d)
    _write(os.path.join(d, "summary.md"), summary_text(r, buys, total))
    code, _ = call("proof-business-plan", "plan.py", ["--dir", d, "compile"], show=show)
    return d, code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(DEMO, "proof"))
    a = ap.parse_args(argv)
    d, code = run(a.out)
    print("\nThe result of the run:")
    for name in ("summary.md", "business-plan.md", "one-pager.md"):
        path = os.path.join(d, name)
        print("  %s %s" % ("ok" if os.path.isfile(path) else "!!", path))
    return 0 if all(os.path.isfile(os.path.join(d, n)) for n in ("summary.md", "business-plan.md", "one-pager.md")) else 1


if __name__ == "__main__":
    sys.exit(main())
