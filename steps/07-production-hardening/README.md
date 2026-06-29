# Step 07 — Secure-by-default, and the honest gap to production

**Goal:** add the boring, correct defaults that separate a demo from something you
*could* deploy — and then be honest about everything that still stands between
this reference and real production.

## The pattern: make the secure choice the default, and the insecure choice loud

The last 10% is what you'd intend to add and never quite get to. The canonical
gateway bakes it in:

- **A deployment profile.** `GATEWAY_ENV=production` enforces client auth and runs
  a startup self-check that *fails fast* on an insecure config (refusing to boot
  as an open relay). `dev` stays permissive but logs a loud warning.
- **Per-client rate limiting.** A sliding-window limiter (by token, else IP)
  returns a clean `429` + `Retry-After` instead of melting your provider quota.
- **Backpressure.** A hard in-flight cap bounds worst-case memory (concurrency x
  buffered size); excess requests get a fast `503` rather than dragging everyone
  down.
- **Constant-time token comparison**, `SecretStr`-wrapped secrets masked in logs,
  and security headers on every response.
- **Prometheus metrics** at `/metrics`: request counts, latency and TTFT
  histograms, the in-flight gauge, rate-limit rejections, and live telemetry-queue
  depth — so you can *see* both planes working.

## Run it

```bash
GATEWAY_ENV=production GATEWAY_TOKENS=secret-token make up
curl -s http://localhost:8000/readyz      # readiness incl. Mongo + workers
curl -s http://localhost:8000/metrics     # Prometheus exposition
```

## Reference architecture, not production

Here's the honest part this whole project is built around. Even with the above,
**this is teaching code, not a product.** To run something like it on a real
critical path you would still need, at minimum:

- **P99 at scale.** The relay is inline on *every* call, so you own an explicit
  *added-overhead* SLO (measure `gateway_p99 - upstream_p99`, not absolute
  latency), connection-concurrency and backpressure tuning, and horizontal scale.
  The discipline that protects the tail: keep all CPU-bound work (parsing,
  embedding, encryption) off the event loop, in the telemetry plane.
- **Identity & access.** SSO/SAML, SCIM, RBAC — and audit logs *of access to the
  logs*, because decrypted prompts contain secrets and source. Who can read a
  capture is itself a security control.
- **Key management.** BYOK/CMEK via a managed KMS/HSM, key rotation — not a local
  master key.
- **Data governance.** PII/secret redaction on capture, residency controls, and
  configurable retention / right-to-erasure.
- **Multi-tenancy & HA.** Orgs/teams/quotas, hard tenant isolation, provider
  failover, zero-downtime deploys.
- **Honest telemetry.** The decision-record / intent-bias work from
  [../../docs/intent-is-biased.md](../../docs/intent-is-biased.md): provenance
  labels on reasoning, multi-view embeddings, and joining captures to outcomes
  (commit/PR/incident).

These gaps are intentional. They're the difference between a thing you learn from
and a thing you bet a company on — and naming them precisely is part of the
lesson.

## Canonical modules

- [`src/blackbox_ai/security/rate_limit.py`](../../src/blackbox_ai/security/rate_limit.py) — the `RateLimiter` protocol + sliding window (swap in Redis for cross-replica).
- [`src/blackbox_ai/api/_auth.py`](../../src/blackbox_ai/api/_auth.py) — constant-time gateway-token auth.
- [`src/blackbox_ai/metrics.py`](../../src/blackbox_ai/metrics.py) — Prometheus instruments + telemetry-plane collector.
- [`src/blackbox_ai/config.py`](../../src/blackbox_ai/config.py) — the production self-check.

## The destination

You've now walked every layer. The complete, tested implementation that all of
this builds toward is [`../../src/blackbox_ai`](../../src/blackbox_ai). Read it
end to end — you understand every piece now.
