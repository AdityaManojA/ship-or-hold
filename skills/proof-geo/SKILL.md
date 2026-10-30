---
name: proof-geo
description: >-
  Audits how readable and citable a site is to AI search engines and assistants
  (GEO / AEO), with a computed citability score. It reads saved HTML pages for a
  clear topic, quotable factual passages, structured data, author and date signals,
  and whether the content is in the HTML at all; and it reads robots.txt and
  llms.txt to report which AI crawlers may fetch the site. Use when the user says
  "GEO", "AEO", "AI search", "will AI cite my site", "citability", "llms.txt",
  "generative engine optimisation", or "optimise for ChatGPT and Perplexity".
argument-hint: "[a saved page, or blank to set it up]"
---

# proof-geo

Search is no longer only ten blue links. Assistants and AI search engines read pages
and quote them, and they can only quote what they can read. This skill measures how
easy a page makes that, and reports whether the crawlers that produce those citations
are even allowed in. The citability score is computed from the page, not guessed; the
method is written down in `scoring.md`.

What the user typed after `/proof-geo`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/geo_score.py" page saved/pricing.html
python3 "${CLAUDE_SKILL_DIR}/geo_score.py" access --robots saved/robots.txt --llms saved/llms.txt
python3 "${CLAUDE_SKILL_DIR}/geo_score.py" record saved/pricing.html --robots saved/robots.txt --llms saved/llms.txt
python3 "${CLAUDE_SKILL_DIR}/geo_score.py" status
```

## Step 1: save the server HTML

Save the HTML a crawler would get, not only what the browser renders. Many AI
crawlers do not run JavaScript, so capture the server response (for example
`curl -s <url> -o saved/pricing.html`, or "view source", rather than the rendered
DOM). Save the key pages under `proof/evidence/proof-geo/`, and grab `robots.txt` and
`llms.txt` from the site root if they exist.

## Step 2: score a page

```bash
python3 "${CLAUDE_SKILL_DIR}/geo_score.py" page saved/pricing.html
```

Each signal is shown with the points it earned and why: a clear topic, self-contained
factual passages, structured data, author and date signals, question-shaped
subheadings, factual density, real text in the HTML, and content that survives with
scripts off. `scoring.md` explains every weight.

## Step 3: check access

```bash
python3 "${CLAUDE_SKILL_DIR}/geo_score.py" access --robots saved/robots.txt --llms saved/llms.txt
```

This reports, per AI crawler, whether robots.txt lets it fetch the site, separating
the crawlers that answer questions (the ones that can cite you) from the ones that
collect training data. Blocking a crawler is a business choice: it protects the
content but gives up citation from that company. The tool reports the state and does
not decide it. It also checks whether an `llms.txt` is present and well-formed
(`llms.example.txt` shows the shape).

## Step 4: record

```bash
python3 "${CLAUDE_SKILL_DIR}/geo_score.py" record saved/pricing.html --robots saved/robots.txt --llms saved/llms.txt
```

Writes the citability score and coverage to the ledger, copies the page in as
evidence, and records a finding for each weak signal with the computed reason, so you
know exactly what to improve.

## Honest limits

AI search is new and the companies behind it do not publish how they pick citations.
These signals are reasoned proxies for readability and citability, measured on a
snapshot of the HTML; a high score makes a page a good candidate to be cited, it does
not promise a citation, and the method will need updating as AI search changes.
Allowing a crawler is necessary for citation but not sufficient. This reads saved
files, not the live site.

## Output / next

`proof/coverage/proof-geo.json`, a `geo_citability` score under
`proof/scores/proof-geo.json`, findings under `proof/findings/proof-geo/`, and the
saved pages under `proof/evidence/proof-geo/`. Then `/proof-seo` for classic SEO and
`/proof-report` for the verdict.
