# TL;DR: Ghosts in the Code

**An educational reference project** — build your own AI Flight Recorder: a
pass-through LLM gateway on FastAPI + MongoDB that records what your AI did and
the reasoning it gave. It is teaching code, not a product (see *Reference
architecture, not production* at the end).

## 🚨 The problem it tackles
When an AI agent writes 15,000 lines of code overnight, it leaves no explanation. If a bizarre edge-case bug occurs 3 weeks later, your engineers are flying blind because the AI's reasoning evaporated the moment the API stream closed. Today your logs say `200 OK` and nothing else.

This repo is a worked example of turning that black box into a glass box — and of doing it on a *single* database instead of five bolted-together systems. Along the way it is honest about a thing most "capture the why" tools gloss over: the model's reasoning is *self-reported*, so what you capture is a **decision record**, not a confession.

## 💡 The shape of the solution
A **native pass-through LLM gateway**. It sits between your developers/agents and the frontier models (OpenAI, Anthropic, Gemini, Ollama, Azure), streams responses back with **negligible added overhead**, and quietly records each interaction off the hot path.

You point your existing SDK at it. That's the entire integration:

```python
from openai import OpenAI

# Same SDK, same code. Just change where it points.
client = OpenAI(base_url="http://localhost:8000/openai/v1", api_key="gateway-token")
```

## 🔁 The loop you'll build: Capture → Debug → Replay

### Step 1 — Capture a *decision record* (not just the final text)
Every call becomes an **Intent Document**. The interesting move is grabbing the model's hidden **`chain_of_thought`** and the **tools it called** — the actions and the narrated decision-making — alongside the final text.

```json
{
  "provider": "anthropic",
  "model_responded": "claude-sonnet-4",
  "project_id": "billing-service",
  "intent_telemetry": {
    "content": "I'll switch the connection pool to lazy init...",
    "chain_of_thought": "The pool is exhausting under load. Eager init blocks startup, so I'll trade a cold-start penalty for...",
    "tools_called": [{ "name": "edit_file", "arguments": { "path": "db/pool.py" } }]
  },
  "raw_payload": { "...": "the exact request, stored verbatim" }
}
```

> **Be honest about what this is.** `chain_of_thought` is the model's *stated* rationale — self-reported, sometimes unfaithful to what actually drove the output, and uneven across providers. The trustworthy anchors are the *objective* fields (`tools_called`, the diff, the params, the inputs). Treat the narrative as a lead, not the truth. (See [docs/intent-is-biased.md](docs/intent-is-biased.md).)

### Step 2 — Debug: ask *"why did the AI do X?"* in plain English
You don't know the request ID or the timestamp. You only know the *vibe* of what went wrong — and you can't `grep` for a vibe.

So instead of keyword-matching, you search by **meaning**. MongoDB's `$rankFusion` runs a semantic vector search (over the embedded reasoning) **and** a keyword search in one query, and fuses the results:

```javascript
// "Find when the agent worried about connection pooling" —
// matches the meaning even if those exact words were never used.
{
  "$rankFusion": {
    "input": { "pipelines": {
      "vector": [ { "$vectorSearch": { "path": "embedding", /* semantic */ } } ],
      "text":   [ { "$search": { /* exact model / project / keyword */ } } ]
    }},
    "combination": { "weights": { "vector": 0.7, "text": 0.3 } }
  }
}
```

```bash
$ make search q="why did the agent change the connection pool limits?"
# → returns the exact interaction + the decrypted chain_of_thought above.
```

**Before:** `200 OK`, shrug, spend a day reverse-engineering AI code.
**After:** one question, and the database walks you to the moment the agent *narrated* its way into the bug — in its own words.

### Step 3 — Own & port: every interaction is a portable artifact
Because the `raw_payload` is stored **verbatim** (and decrypted on read), any captured interaction can be exported as a self-contained, re-runnable artifact — the exact provider, path, and request body:

```bash
blackbox-ai export <request_id> --as curl   # → a ready-to-run request you own
```

There are two honest tiers of "replay" here, and it's worth keeping them straight:

- **Deterministic replay (the cache):** an exact-repeat request replays the *stored response bytes* — byte-for-byte identical, for free. Great for cost/latency, narrow in scope.
- **Migration replay (the artifact):** feed the verbatim payload to a **cheaper, newer, or open-source model**. You reproduce the *inputs* exactly; the *output* is a fresh generation. You own every question ever asked, so you can re-prime any model, eval suite, or regression test.

> The gateway **never auto-replays** — it hands you the artifact, you decide. A captured agent request can contain destructive tool calls (`edit_file`, `delete`), so re-issuing one is always a deliberate human action, never a side effect. (A witness touches nothing.)

> **How it stays invisible:** the gateway is split into a **Data Plane** (a dumb, fail-open proxy that streams bytes straight back — if the DB dies, your request *still* succeeds) and a **Telemetry Plane** (async workers that parse, embed, and store off the hot path). The recorder can never crash the plane. This two-plane split is a reusable pattern, not a one-off ([ADR 0001](docs/adr/0001-data-plane-telemetry-plane-split.md)).

## 🍃 Why MongoDB Makes This Possible

Doing this the "obvious" way needs 5 systems (a document store, a vector DB, a cache, a sync pipeline, an encryption layer). MongoDB collapses them into **one** — which is the central lesson of the curriculum:

| Need | The bolt-on way | With MongoDB |
| --- | --- | --- |
| Store every provider's different shape | Wide nullable tables + migrations | One polymorphic document |
| Find *meaning* in past reasoning | Separate vector DB + sync job | `$vectorSearch` on the same doc |
| Meaning **and** keywords at once | App-side result merging | `$rankFusion` in one query |
| Protect prompts/source code | App-layer crypto + key plumbing | Queryable Encryption in the driver |
| Expire cached answers | A cron sweeper | A TTL index |

### The killer feature: search *encrypted* data
Prompts contain proprietary code and secrets, so the crown jewels (`raw_payload`, `content`, `chain_of_thought`) are encrypted **client-side, before they ever reach the database** via Queryable Encryption. MongoDB stores ciphertext and literally cannot read it.

And yet **Step 2 still works** — because the embedding vector is computed *before* encryption and stored in plaintext. So you get semantic, time-travel debugging over data the database itself can't read. A safe with a search slot.

## 📚 How to work through it
Follow [`steps/`](steps/) in order (relay → two-plane capture → parsers/intent → vector search → encryption → cache/replay → hardening). Each step is a runnable snapshot building toward the canonical implementation in [`src/blackbox_ai`](src/blackbox_ai). The full map is in the [README](README.md).

## ⚠️ Reference architecture, not production
This is teaching code — complete, tested, runnable, and deliberately scoped. It is **not** a hardened product and has no SLA. Real production would still need P99-at-scale discipline, SSO/RBAC and audit-of-access, managed KMS/BYOK, PII redaction, and multi-tenancy/HA. Those gaps are intentional and are covered as content in Step 07.
