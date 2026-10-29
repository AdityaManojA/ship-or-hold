# Proof

An evidence-first testing skill pack for Claude. MIT, open source.

**The idea:** most testing and audit tools let the model *say* something passed.
Proof doesn't. Every skill produces a **reproducible artifact** — a failing test, a
working proof-of-concept, a measured number, a screenshot — and the final verdict
(**Ship / Not yet / Incomplete**) is **computed by a Python tool from those
artifacts**, never narrated by the model. A finding counts only once it has been
verified; a missing input makes the verdict `Incomplete`, never a silent pass.

It covers web-app testing, mobile-app testing, security and dependency audits,
performance and accessibility, SEO and GEO/AEO, mutation and load testing, LLM
red-teaming, and the finance behind the product — 23 `/proof-*` skills that hand off
through files, with stdlib-only tools (no network, no paid API, no cloud) that detect
and use external tools like Playwright, Maestro, Lighthouse, semgrep and k6 when
present and fall back gracefully when they aren't.

**Status:** planning. See [PLAN.md](PLAN.md) for the full design, the 20 skills, the
four build phases, and what it borrows (and improves) from MiroFish, founder-skill,
Anthropic's `webapp-testing`, Trail of Bits' skills, Cloudflare's security-audit
skill, Strix, Lighthouse, axe-core, Maestro, promptfoo, ponytail, the claude-seo /
geo-seo-claude / seomachine SEO packs, and the OWASP guides.

## Credits

Proof is **original work**. It takes *inspiration* — ideas and patterns — from a
number of excellent open-source projects, but copies no code, text, schemas, names
or assets from any of them. With thanks to MiroFish, founder-skill, Anthropic's
`webapp-testing`, Trail of Bits' skills, Cloudflare's security-audit skill, Strix,
ponytail, the claude-seo / geo-seo-claude / seomachine SEO packs, Lighthouse,
axe-core, Maestro, promptfoo, and the OWASP WSTG / MASTG / ASVS guides.

## License

MIT.

