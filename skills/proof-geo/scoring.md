# How the GEO citability score is computed

The score is out of 100. Every signal is measured on the saved HTML of one page, and
every weight is listed here, so the number can be checked by hand. A signal that does
not apply to a page (for example, no subheadings on a short page) is dropped and the
rest keep their weights; the printed result says when that happens.

The idea behind all of it: an AI search engine or assistant cites a page when it can
read the page's text without running scripts, find a clear topic, and lift a
self-contained, factual passage that answers a question. These signals measure how
easy that is.

| signal | weight | what is measured | why it helps citation |
| --- | ---: | --- | --- |
| topic | 12 | A `<title>`, exactly one `<h1>`, and real overlap between them | A page about one clear thing is easy to place and quote. |
| passages | 18 | Blocks of 40+ words that state a fact (a number, a named thing, or a definition) and are not a sales pitch | Assistants quote self-contained passages; marketing lines are not quotable. |
| structured | 12 | An `application/ld+json` block that parses and has an `@type` | Structured data tells a machine what the page is and who wrote it. |
| eeat | 14 | Author, organisation and date signals (meta tags, a byline, a `<time>`, or ld+json keys) | Assistants prefer sources they can attribute and date. |
| headings | 10 | `h2`-`h4` subheadings that read as questions or clear topics | Question-shaped headings match the questions people ask. |
| density | 12 | Numbers and named entities per 100 words | Specific, checkable facts are what gets lifted into an answer. |
| text | 12 | The count of visible words in the HTML | A page with almost no server-side text has nothing to cite. |
| stable | 10 | A 20+ word block present in the HTML, no empty client-render root, no "JavaScript required" notice | Many AI crawlers do not run scripts, so content must be in the HTML. |

## The weights

Passages (18) and E-E-A-T (14) carry the most because they are what an assistant
actually needs to produce an attributed citation: something quotable, and someone to
attribute it to. Topic, structured data, density and raw text (12 each) are the
foundations. Headings and script-free stability (10 each) matter but are easier to
fix.

A page scores well by being a clear, factual, well-attributed document whose content
is in the HTML. It scores badly by being an empty shell that paints itself with
JavaScript, or a page of slogans with nothing a machine can quote.

## Access is separate

Whether AI crawlers are *allowed* to fetch the site is reported by the `access`
command from robots.txt, not folded into the page score. Blocking a crawler is a
business choice, not a mistake: it protects content but removes the chance of being
cited by that company's assistant. The tool reports the state and leaves the choice
to you.

## Honest limits

AI search is new and the companies behind it do not publish how they choose
citations. These signals are reasoned proxies for readability and citability,
measured on a snapshot of the HTML; a high score makes a page a good candidate, it
does not promise a citation, and the methods here will need updating as AI search
changes.
