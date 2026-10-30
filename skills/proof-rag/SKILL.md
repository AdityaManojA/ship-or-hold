---
name: proof-rag
description: >-
  Measures the answer quality of a retrieval-augmented (RAG) feature with
  evidence: whether the right documents are retrieved, whether citations point at
  documents that were retrieved and relevant (and none are invented), whether the
  app abstains when the documents hold no answer, whether answers are faithful
  and mention the required facts, and whether any user ever sees another user's or
  tenant's documents. You run your cases against your own app; the tool scores the
  saved results against budgets and never calls a model. Optional, not part of a
  full check. Use when the user says "test my RAG", "are the citations right",
  "does retrieval work", "evaluate answers against my documents", "is it
  hallucinating citations", "can users see each other's documents", "grounding
  check", or "RAG eval".
argument-hint: "[cases.json results.json, or blank to start from a template]"
---

# proof-rag

A RAG feature can be safe and still wrong: it retrieves the wrong file, cites a
document it never read, answers from the model's memory when the user's documents
say nothing, or, worst, pulls another customer's document into the answer. This
skill turns those into numbers with the cases behind them.

It is optional and never part of a full check. It pairs with `/proof-ai`, which
covers safety (prompt injection, data leaks, misuse), and with
`eval_import.py results` in that skill for any eval suite you already run.

What the user typed after `/proof-rag`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/rag.py" template --out proof/rag/cases.json
python3 "${CLAUDE_SKILL_DIR}/rag.py" score cases.json results.json --k 5
python3 "${CLAUDE_SKILL_DIR}/rag.py" score cases.json results.json --json
python3 "${CLAUDE_SKILL_DIR}/rag.py" record cases.json results.json --owners docs.json --min-hit 0.8
python3 "${CLAUDE_SKILL_DIR}/rag.py" status
```

`template --force` replaces a file Proof did not write.

## Step 1: write the cases

Start from the template and write 30 to 100 cases against a test workspace you own,
seeded with known documents. Mix four kinds:

- **Answerable**: the answer is in one document (`expected_docs` lists it), with
  `must_mention` holding the facts the answer has to contain.
- **Multi-document**: the answer needs two or more documents.
- **Unanswerable**: nothing in the documents answers it (`expected_docs: []`); the
  right behaviour is to say so.
- **Other-tenant bait**: asked by one user about something only another user's
  documents contain (`expected_docs: []`). Seed a second test account with its own
  documents for this.

Each case: `{"id", "question", "user" (or "tenant"), "expected_docs": [...],
"must_mention": [...]}`. Use the document ids your app uses in its citations.

## Step 2: run them against your app

Send each question to your app as its `user`, through the same API the product uses
(a short script with curl or your HTTP client, or the built-in browser tools), and
save one result per case:

```json
{"id": "refund-policy", "answer": "Refunds are accepted within 30 days...",
 "retrieved": [{"doc": "alice/policies.pdf", "user": "alice", "score": 0.82}],
 "cited": ["alice/policies.pdf"], "abstained": false, "faithful": true, "latency_ms": 2100}
```

- `retrieved` in rank order; plain ids are fine, add `user` or `tenant` when your API
  exposes the owner, or pass `--owners docs.json` mapping each document id to its owner.
- `abstained` is optional; without it, phrases such as "I don't know" or "couldn't
  find" count, and `--abstain-phrase` adds your app's own wording.
- `faithful` is optional and must come from a judge: a person, or a separate model
  asked whether every claim in the answer is supported by the cited text. String
  matching cannot tell a faithful paraphrase from an invented one, so this tool
  never guesses it; without the field, faithfulness is not applicable.

## Step 3: score and record

| Metric | Means | Default budget |
| --- | --- | --- |
| retrieval_hit_rate | an expected document is in the top `--k` | at least 85% (`--min-hit`) |
| citation_precision | cited documents are among the expected ones | at least 90% (`--min-precision`) |
| citation_support | every cited document was actually retrieved; otherwise the citation is invented | 100%, fixed |
| abstain_rate | unanswerable cases get "I don't know", not an invented answer | at least 90% (`--min-abstain`) |
| answer_rate | answerable cases get an answer, not a refusal | at least 90% (`--min-answer`) |
| faithfulness | judged faithful | at least 90% (`--min-faithful`) |
| must_mention_rate | answers contain the required facts | at least 90% (`--min-mention`) |
| cross_tenant_leaks | documents shown to someone who does not own them | 0, fixed |

`record` copies the cases, results and owners file into evidence, marks a metric
not applicable (with the reason) when no case could measure it, writes each metric
as a score, and turns each failing metric into one verified finding listing the
failing case ids. Each leaking case is its own critical finding. Invented citations
are high. Findings that pass on a later run are removed; ones you marked false
positive are kept.

## Honest limits

The scores are only as good as the cases: a small or easy set gives flattering
numbers. Retrieval is judged by document id, so a right answer from an unlisted
document counts as a miss; list every document that genuinely answers a case.
Leaks are found only where the owner of a document is known, from the results or
`--owners`. Faithfulness depends on the judge you use. Cases run against a test
workspace say nothing about documents real users upload; repeat the run after
changes to retrieval, chunking or the prompt.

## Output / next

`proof/coverage/proof-rag.json`, `proof/scores/proof-rag.json`, findings under
`proof/findings/proof-rag/`, the inputs under `proof/evidence/proof-rag/`. Then
`/proof-report`.
