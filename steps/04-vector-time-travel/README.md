# Step 04 — Time-travel debugging with vector search

> From here on, the steps are **guided lessons on the canonical package**
> ([`../../src/blackbox_ai`](../../src/blackbox_ai)). Vector search needs real
> infrastructure (MongoDB Atlas Local + Voyage AI), so instead of a fragile
> partial copy, we walk the real modules and run them.

**Goal:** find the interaction you care about by *meaning*, six months later, when
all you remember is the vibe of what went wrong.

## The idea

You can't `grep` for a vibe. The agent didn't write "I am nervous about connection
pooling" — it wrote three paragraphs that circled the idea. Keyword search finds
matches; you need *meaning*.

So as each Intent Document is captured, a telemetry worker embeds its most
meaningful text into a vector and stores it **on the same document**. Then "find
moments like this" is one `$vectorSearch` stage — no second database, no sync job,
no drift. And hybrid `$rankFusion` fuses that semantic leg with a keyword leg
(exact model/project names) in a single query.

## The honesty thread (read this one carefully)

Look at [`embedding_text()`](../../src/blackbox_ai/telemetry/embeddings.py): it
embeds `chain_of_thought` first, then `content`, then the prompt. And
[`search.py`](../../src/blackbox_ai/search.py) weights the vector leg `0.7` to the
keyword leg `0.3`. Put together, **most of your relevance signal is similarity to
a narrative the model wrote about itself** — which Step 03 established is
self-reported and uneven.

That's fine for *exploratory* debugging (it's a great lead generator) but it is
the convenient signal, not the trustworthy one. The hardened version embeds the
*objective* artifacts too — tool calls, the diff, params — as separate views, so a
query can land on what the model **did**. See
[../../docs/intent-is-biased.md](../../docs/intent-is-biased.md) for the full
treatment and concrete exercises.

## Run it (against the canonical gateway)

```bash
make gen-key                      # encryption is on by default
echo 'GATEWAY_EMBEDDINGS_PROVIDER=voyage' >> .env
echo 'VOYAGE_API_KEY=...'         >> .env   # Voyage AI key
make up && make init              # collections + vector & text indexes
make demo                         # generate some interactions
make search q="why did the agent change the connection pool limits?"
```

With embeddings off, everything else still works — documents are simply written
without a vector (fail-open).

## Canonical modules

- [`src/blackbox_ai/telemetry/embeddings.py`](../../src/blackbox_ai/telemetry/embeddings.py) — the `Embedder` protocol, Voyage backend, circuit breaker, and `embedding_text()`.
- [`src/blackbox_ai/search.py`](../../src/blackbox_ai/search.py) — vector + hybrid `$rankFusion`, with graceful fallback.
- [`src/blackbox_ai/db/search_indexes.py`](../../src/blackbox_ai/db/search_indexes.py) — the vector + full-text index definitions.

## Next

[Step 05](../05-queryable-encryption) locks the crown jewels — without giving up
the search you just built.
