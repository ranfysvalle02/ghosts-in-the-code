# TL;DR: Ghosts in the Code (Blackbox AI Gateway)

## 🚨 The Problem (Why)
When an AI agent writes 15,000 lines of code overnight, it leaves no explanation. If a bizarre edge-case bug occurs 3 weeks later, your engineers are flying blind because the AI's reasoning evaporated the moment the API stream closed.

Furthermore, if that historical context lives exclusively inside OpenAI or Anthropic's walled gardens, you are locked in. You need an **AI Flight Recorder** to capture the *intent* and *chain-of-thought* behind the code, ensuring digital sovereignty.

## 💡 The Solution (What)
A **native pass-through LLM gateway** built on FastAPI and MongoDB. It sits between your developers/agents and the frontier models (OpenAI, Anthropic, Gemini, Ollama, Azure), streams responses back with **zero added latency**, and quietly records *why* the model did what it did.

You point your existing SDK at it. That's the entire integration:

```python
from openai import OpenAI

# Same SDK, same code. Just change where it points.
client = OpenAI(base_url="http://localhost:8000/openai/v1", api_key="gateway-token")
```

## 🔁 The Payoff (How): Capture → Debug → Replay

This is the whole point. Today, when an LLM does something weird, your logs say `200 OK` and nothing else. This gateway turns that black box into a glass box with a three-step loop.

### Step 1 — Capture the *why*, not just the *what*
Every call becomes an **Intent Document**. The key move is that it grabs the model's hidden **`chain_of_thought`** and the **tools it called** — the actual decision-making — not just the final text.

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
**After:** one question, and the database walks you to the moment the agent *reasoned* its way into the bug — in its own words.

### Step 3 — Own & port: every interaction is a portable artifact
Because the `raw_payload` is stored **verbatim** (and decrypted on read), any captured interaction can be exported as a self-contained, re-runnable artifact — the exact provider, path, and request body:

```bash
blackbox-ai export <request_id> --as curl   # → a ready-to-run request you own
```

There are two honest tiers of "replay" here, and it's worth keeping them straight:

- **Deterministic replay (the cache):** an exact-repeat request replays the *stored response bytes* — byte-for-byte identical, for free. Great for cost/latency, narrow in scope.
- **Migration replay (the artifact):** feed the verbatim payload to a **cheaper, newer, or open-source model**. You reproduce the *inputs* exactly; the *output* is a fresh generation. This is the lock-in escape hatch — you own every question ever asked, so you can re-prime any model, eval suite, or regression test.

> The gateway **never auto-replays** — it hands you the artifact, you decide. A captured agent request can contain destructive tool calls (`edit_file`, `delete`), so re-issuing one is always a deliberate human action, never a side effect. (A witness touches nothing.)

> **How it stays invisible:** the gateway is split into a **Data Plane** (a dumb, fail-open proxy that streams bytes straight back — if the DB dies, your request *still* succeeds) and a **Telemetry Plane** (async workers that parse, embed, and store off the hot path). The recorder can never crash the plane.

## 🍃 Why MongoDB Makes This Possible

Doing this the "obvious" way needs 5 systems (a document store, a vector DB, a cache, a sync pipeline, an encryption layer). MongoDB collapses them into **one**:

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
