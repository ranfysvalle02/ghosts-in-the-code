# Step 03 — The polymorphic Intent Document (and why "intent" is biased)

**Goal:** turn raw captured bytes into a structured record — and be honest about
what that record actually is.

## The idea

You support five providers and every one speaks a different dialect. OpenAI hands
you one shape, Anthropic another, Gemini buries the model name in the URL, Ollama
does its own thing — and tomorrow one of them ships a new field. Model that in a
rigid table and you drown in nullable columns and migrations.

So the telemetry worker dispatches a tiny **per-provider parser**
([`app.py`](app.py)) and lifts the results into one **Intent Document**: a single
record whose shape follows the data. Normalized fields (`content`,
`chain_of_thought`, `tools_called`, `finish_reason`) sit alongside the verbatim
`raw_payload`. New provider, new field? You just store it.

## The honesty thread: it's a *decision record*, not a confession

The parser pulls out `chain_of_thought` where the provider exposes it (Anthropic
`thinking_delta`, OpenAI-style `reasoning_content`). It is tempting to call this
"the why." Resist that.

- The chain-of-thought is **generated text** — the model's *stated* rationale, not
  a trace of the computation that produced the output. Models routinely give
  plausible reasons they didn't use, and omit reasons they did.
- Fidelity is **uneven**: real thinking from one provider, a summary from another,
  nothing from a third. So even within one collection, the field means different
  things.

Read each Intent Document as a **decision record**: the inputs, the *actions* the
model took (`tools_called` — objective), and the *story* it told (`chain_of_thought`
— a lead). Trust the actions; use the story to point you at them. The full
argument, and how you'd harden it, is in
[../../docs/intent-is-biased.md](../../docs/intent-is-biased.md).

## Run it

```bash
cd steps/03-parsers-and-intent
uv run --with fastapi --with "uvicorn[standard]" --with httpx uvicorn app:app --port 8000
```

```bash
curl -N http://localhost:8000/ollama/api/chat \
  -H 'content-type: application/json' \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"Explain a mutex in one line"}],"stream":true}'

curl -s http://localhost:8000/_captures | python -m json.tool
```

You'll now see a parsed `intent_telemetry` block (content, any reasoning, tool
calls, finish reason) plus the verbatim `raw_payload`.

## Canonical counterparts

- [`src/blackbox_ai/telemetry/parsers/`](../../src/blackbox_ai/telemetry/parsers/) — the complete parsers (`openai_sse`, `anthropic_sse`, `gemini_sse`, `ollama_ndjson`).
- [`src/blackbox_ai/telemetry/models.py`](../../src/blackbox_ai/telemetry/models.py) — the real `IntentDocument` / `IntentTelemetry` (note the docstrings carry this same honesty caveat).

## Next

[Step 04](../04-vector-time-travel) makes the history *searchable by meaning* —
and shows why embedding the (biased) narrative is the convenient choice, not the
trustworthy one.
