---
name: proof-seo
description: >-
  A technical and on-page SEO audit of saved web pages where the score is computed
  from the elements found, never an opinion. Parses the HTML you saved (title, meta
  description, h1 and heading order, image alt text, canonical, lang, viewport, Open
  Graph, structured data, internal links, word count), checks robots.txt and the
  sitemap, folds in Lighthouse's SEO score when a report is available, and writes a
  score, coverage and verified findings to the ledger. Use when the user says "SEO
  audit", "check my SEO", "is this page indexable", "check the meta tags", "why am I
  not ranking", "technical SEO", "on-page SEO", "check robots and sitemap", or
  "structured data check". It audits pages; it does not write content.
argument-hint: "[path to a saved .html page, or blank for proof/evidence/proof-seo/]"
---

# proof-seo

Score a page for search the way a crawler would read it: parse the saved HTML, look
for each element that matters, give points for what is there and name what is not.
The score is a sum of checked elements with fixed weights, so two people running it
on the same file get the same number and can trace every point to a tag. This skill
audits. It does not write titles, descriptions or copy; if the user wants that, hand
them the failing checks and let a writing skill take it from there.

What the user typed after `/proof-seo`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/seo_score.py" checks                       # the checks and their weights
python3 "${CLAUDE_SKILL_DIR}/seo_score.py" page <file.html> [--url U]   # one page, every check, a score
python3 "${CLAUDE_SKILL_DIR}/seo_score.py" site [--pages dir]           # every saved page + robots + sitemap
python3 "${CLAUDE_SKILL_DIR}/seo_score.py" record <file.html>           # write a page to the ledger
```

It makes no network calls. It reads files the user saved. Try it on the example page
in this folder: `seo_score.py page "${CLAUDE_SKILL_DIR}/page.example.html"` scores
84 and fails exactly two checks (an image without alt, a heading that skips a level).

## Step 1: know the target

If `proof/target.json` exists, read it; its first URL is used as `--url` when none
is given (the tool says so when it does). Saving a public page is a plain read, not
active testing, so this skill runs with or without a target. If there is no target
and the user wants a verdict later, run `/proof-plan` at some point; it is not needed
to audit.

## Step 2: save the pages

Save the rendered HTML of the pages that matter (home, the main landing pages, one
article, one product or pricing page) into `proof/evidence/proof-seo/`, one file per
page, named after the page (`index.html`, `pricing.html`). Two ways:

- With the built-in browser tools: navigate to the page, let it finish loading, then
  read `document.documentElement.outerHTML` with the javascript tool and write it to
  the file. This is the rendered DOM, which is what matters for an app that builds
  its page in JavaScript.
- With a command the user runs: `curl -sL <url> -o proof/evidence/proof-seo/index.html`.
  This is the server's HTML before any script runs, which is what a crawler sees
  first. When the two differ a lot, save both and score both; the gap is itself a
  finding worth noting.

Also save `robots.txt` and `sitemap.xml` into the same folder when the site has them
(`curl -sL <site>/robots.txt -o proof/evidence/proof-seo/robots.txt`, same for the
sitemap). The `site` command picks them up by name.

## Step 3: score each page

```bash
python3 "${CLAUDE_SKILL_DIR}/seo_score.py" page proof/evidence/proof-seo/index.html --url https://example.com/
```

Each line is one check: the mark, the id, the points earned out of the points
possible, and the value found (or the problem). The weights:

| check | points | passes when |
| --- | ---: | --- |
| title | 12 | a `<title>` of 10-60 characters |
| meta-description | 10 | `<meta name="description">` of 50-160 characters |
| h1 | 10 | exactly one h1 |
| heading-order | 6 | no heading skips a level (h2 then h4 fails) |
| img-alt | 10 | every img has an alt attribute (empty alt counts) |
| canonical | 8 | one `<link rel="canonical">` with an href |
| lang | 6 | `<html lang="...">` |
| viewport | 8 | a meta viewport that sets a width |
| open-graph | 8 | og:title, og:description and og:image |
| structured-data | 8 | an `application/ld+json` script that parses as JSON |
| internal-links | 6 | at least 3 links to the same site |
| word-count | 8 | at least 300 words of visible text |

A check that does not apply (no images, no headings) is left out and the rest is
scaled to 100, and the output says so. `--url` tells internal links from external
ones; without it the page's canonical host is used, and without that absolute links
count as external. `--json` prints the full result for a script to read.

## Step 4: score the site

```bash
python3 "${CLAUDE_SKILL_DIR}/seo_score.py" site --pages proof/evidence/proof-seo --url https://example.com/
```

Runs the page checks on every `.html` in the folder, then the technical checks:
robots.txt present and not blocking all crawling (a `Disallow: /` for `*`, Googlebot
or Bingbot fails it), a sitemap present, well-formed XML with at most 50000 urls and
no entry without a `<loc>`, and every saved page listed in the sitemap (matched by
canonical URL, else by file name). Site score = 0.75 x mean page score + 0.25 x
technical score. A robots.txt or sitemap that was not saved counts as not shown to
exist; save it or pass `--robots` / `--sitemap`.

## Step 5: Lighthouse, when it is there

Run `/proof-doctor --for proof-seo`. When Lighthouse is installed, have the user
(or the browser tooling) save its JSON report for the same page:

```bash
lighthouse https://example.com/ --only-categories=seo --output json --output-path proof/evidence/proof-seo/lighthouse-index.json
```

Then pass it: `page ... --lighthouse proof/evidence/proof-seo/lighthouse-index.json`
prints its SEO score and failing audits next to ours, and `record ... --lighthouse`
writes it as its own metric, `lighthouse_seo` (budget 90), so the report shows both
numbers and each traces to its source. Without Lighthouse the skill runs on the
built-in checks alone and says nothing about what it could not measure.

## Step 6: record

```bash
python3 "${CLAUDE_SKILL_DIR}/seo_score.py" record proof/evidence/proof-seo/index.html --url https://example.com/ \
  --robots proof/evidence/proof-seo/robots.txt --sitemap proof/evidence/proof-seo/sitemap.xml
```

For the page it writes: coverage with one check per item (covered, or not applicable
with the reason), a `seo_score` metric (points, budget 80, op `>=`), and a verified
finding (category `seo`, severity low or medium) for each failed check worth 10 points
or more: missing or bad title, missing or bad meta description, h1 count, images
without alt. Verification is `manual` with a note stating the computed fact, because
the parse is the proof and the saved page is the evidence. A page outside `proof/` is
copied into `proof/evidence/proof-seo/` first. Record each key page in turn: findings
are kept per page, `seo_score:<page>` is written per page and `seo_score` is the
lowest of them, so the weakest key page gates the verdict. Re-recording a page
replaces its own entries and removes a finding whose check now passes; a finding the
user rejected as a false positive is left alone. With `--robots` / `--sitemap` the
technical checks are recorded too (a blanket robots block is a medium finding, an
invalid sitemap is low); without them they are marked not applicable with a note.

## Honest limits

On-page and technical signals are a fraction of what decides a ranking. A high score
means the page is well-formed for search, not that it will rank; links, content
quality, speed and the competition are not in this number. The thresholds are
common guidance, not rules a search engine publishes, and engines change. The tool
reads a saved snapshot, not the live crawl: a page that renders differently for a
bot, is blocked by a header, or returns a different status code will not show that
here. Word and link counts come from a parser, not a browser; text injected after
load is counted only if you saved the rendered DOM.

## Output / next

`proof/scores/proof-seo.json`, `proof/coverage/proof-seo.json`, findings under
`proof/findings/proof-seo/`, pages and reports under `proof/evidence/proof-seo/`.
Then `/proof-report` for the verdict, `/proof-web-perf` for speed, `/proof-a11y`
for the accessibility side of alt text and headings.
