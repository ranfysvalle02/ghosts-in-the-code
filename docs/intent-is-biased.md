# Why your captured "intent" is biased — and what to anchor on instead

This project's pitch is seductive: capture the AI's `chain_of_thought` and you've
captured *why* it wrote the code. It's worth slowing down on that claim, because
it's the part most "capture the why" tools quietly overstate — and getting it
right makes the recorder more useful, not less.

The short version: **a captured chain-of-thought is the model's *stated*
rationale, not a log of the computation that produced the output.** So a capture
is best understood as a **decision record** — the inputs, the actions the model
took, and the story it told about them — where only some of those parts are
trustworthy.

## 1. Chain-of-thought is not a faithful trace

The reasoning a model emits is itself *generated text*. It is produced by the same
next-token process as the answer, and there is a substantial research line (e.g.
Turpin et al., *"Language Models Don't Always Say What They Think"*, and
Anthropic's 2025 *"Reasoning Models Don't Always Say What They Think"*) showing
that models:

- reach conclusions for reasons they do **not** verbalize, and
- verbalize reasons they did **not** actually use (plausible post-hoc
  rationalization).

So "the model said it switched to lazy init because the pool was exhausting under
load" is a *hypothesis the model authored*, not a measurement of its internal
state. It might be exactly right. It might be a confident story. You usually
can't tell from the text alone.

## 2. The fidelity is wildly uneven across providers

This repo captures reasoning from whatever the wire exposes, and the wire is not
consistent:

- **Anthropic** streams genuine extended-thinking deltas — see the
  `thinking_delta` handling in
  [src/blackbox_ai/telemetry/parsers/anthropic_sse.py](../src/blackbox_ai/telemetry/parsers/anthropic_sse.py).
- **OpenAI** o-series returns *summarized* reasoning, not the raw trace.
- **Most models** expose nothing, so there is no chain-of-thought at all.

That matters because of how the embedded text is chosen.
[`embedding_text()`](../src/blackbox_ai/telemetry/embeddings.py) prefers
`chain_of_thought`, then `content`, then the user prompt. So one document's vector
represents *real thinking*, another's represents *polished output*, and another's
represents *the user's question* — three different kinds of thing. Their
similarity scores are not strictly comparable, and that asymmetry is invisible in
the results. It's a bias baked into the index itself.

## 3. The search is anchored on the least reliable signal

[`search.py`](../src/blackbox_ai/search.py) weights the vector leg at `0.7` and the
keyword leg at `0.3`. Combined with the `chain_of_thought`-first embedding choice,
that means the majority of your relevance signal is *similarity to a narrative the
model authored about itself*. For exploratory debugging that's genuinely useful —
but it's a lead, not proof.

And it's at its weakest precisely when you need it most: when a decision emerged
from a very long context, the actual driver is often some buried message the model
never cites ("lost in the middle"), so the narration is least trustworthy for the
big, messy interactions you most want to debug.

## 4. What to anchor on instead: the objective artifacts

The fix isn't to throw away the narrative — it's to stop treating it as the
source of truth and anchor on what's *verifiable*:

- **Actions, not words.** `tools_called` (e.g. `edit_file` with a path) is an
  objective record of what the model tried to do. The resulting **diff** /
  file:line is even better. Index and search *these*, so a query for "why did the
  agent touch `db/pool.py`?" lands on the action even if the story lied.
- **What it knew.** The model, parameters (temperature, etc.), system-prompt
  version, and the context actually present at decision time. Bugs are frequently
  "it was given stale/partial context" — which is objective and reproducible.
- **Outcomes.** The commit / PR / file that the call produced, ideally joined to
  the later failing test or incident. Intent without outcome linkage is just
  vibes; the debugging payoff is the join from artifact → decision record.

A useful framing: a flight recorder doesn't store the pilot's feelings — it stores
instrument readings and control inputs, plus a cockpit *voice* track that everyone
knows is commentary, not flight data. `chain_of_thought` is the voice track.

## 5. How you'd harden this build (exercises for the reader)

None of these are implemented here — they're the honest "next mile," and they make
good extensions to the curriculum:

1. **Label provenance.** Add a `reasoning_source` of `raw_thinking` /
   `provider_summary` / `prose_inference` / `absent` and an `is_self_reported`
   flag to `IntentTelemetry`, so search can normalize or filter per provider.
2. **Embed multiple views.** Instead of one `embedding_text()` that picks
   reasoning-first, embed an *action view* (tool names + args + paths) and an
   *input view* separately from the *reasoning view*, so queries can hit the
   objective track.
3. **Join on artifacts.** Capture the resulting commit SHA / file:line and make it
   a first-class indexed field; debug from the code you're already staring at.
4. **A faithfulness flag.** Cheaply check whether the `tools_called` / diff
   actually match the stated rationale, and flag mismatches as `low_faithfulness`.

## TL;DR

- A capture is a **decision record**, not a confession.
- `chain_of_thought` is *self-reported* and unevenly available; it's a lead.
- Trust the **objective** fields (tool calls, diffs, params, outcomes); use the
  narrative to point you at them.
- This repo intentionally ships the "convenient" version (reasoning-first
  embedding) so you can *see* the tradeoff — and improve it.
