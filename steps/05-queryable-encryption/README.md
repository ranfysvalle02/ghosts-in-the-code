# Step 05 — The safe with a search slot (Queryable Encryption)

**Goal:** encrypt the crown jewels — prompts, source code, the model's reasoning —
without losing the search from Step 04.

## The idea

What you're recording is the literal blueprint of your business in plain language:
the single most valuable, most dangerous pile of text you own. Encrypting it is
non-negotiable. But the usual paradox bites: encrypt your data and you can't
search it — a locked diary, perfectly safe and perfectly useless.

MongoDB's **Queryable Encryption** refuses the tradeoff. The crown-jewel fields
(`raw_payload`, `intent_telemetry.content`, `intent_telemetry.chain_of_thought`)
are encrypted **client-side, by the driver, before they ever leave your process**.
The database stores ciphertext and never holds the keys.

And the search still works — because the **embedding vector is computed *before*
encryption** and stored in plaintext. So `$vectorSearch` operates over data the
database itself cannot read, and results come back decrypted client-side. A safe
with a search slot.

## The one alarm allowed to ground the plane

Everywhere else the gateway is fail-*open*: telemetry can fail quietly and the
request still completes. Encryption is the deliberate exception — it is
**fail-closed**. If encryption is enabled but the key or the `crypt_shared`
library is missing, the gateway *refuses to start* rather than risk writing one
line of plaintext. Some mistakes you want to find at the gate, not at 2:47am. See
[ADR 0002](../../docs/adr/0002-fail-closed-queryable-encryption.md).

## Run it

```bash
make gen-key                 # writes a local 96-byte master key to .env
# GATEWAY_ENCRYPTION_ENABLED defaults to true
make up && make init
make demo
```

Then see the split with your own eyes — the database view is ciphertext, the
gateway view is decrypted — exactly as walked through in
[`../../OLLAMA_DEMO.md`](../../OLLAMA_DEMO.md) (sections 4).

## Production note

The local key is a dev convenience. Real deployments point the encryption manager
at a managed KMS (AWS/Azure/GCP/KMIP) by changing only `kms_providers` and the
per-DEK `master_key` — the rest is identical. This is the first of several
"reference vs production" gaps; Step 07 collects them.

## Canonical modules

- [`src/blackbox_ai/security/encryption.py`](../../src/blackbox_ai/security/encryption.py) — the QE manager, encrypted-field map, and KMS seam.
- [`src/blackbox_ai/db/indexes.py`](../../src/blackbox_ai/db/indexes.py) — encrypted-collection bootstrap.

## Next

[Step 06](../06-cache-and-replay) adds a self-cleaning cache and turns every
capture into a portable, replayable artifact.
