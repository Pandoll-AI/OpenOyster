# OpenOyster Threat Model

## 1. Assets

- Raw documents and extracted text.
- API and model credentials.
- Hypothesis/evidence provenance.
- Policy and mission state.
- Human feedback and decision history.
- Database availability and integrity.
- External action authority added by future tools.

## 2. Trust boundaries

```text
Untrusted files / URLs
        ↓
Connector and parser boundary
        ↓
Database and event graph
        ↓
Model/provider boundary
        ↓
Internal tool execution
        ↓
API/operator boundary
        ↓
Future external action boundary
```

All source content is untrusted, including instructions embedded in documents. Extracted text must be treated as data, not privileged commands.

## 3. Principal threats and current mitigations

### Prompt injection in documents

Threat: source text attempts to alter system behaviour or request secrets/actions.

Mitigations:

- extraction output is schema-validated;
- execution can only invoke code-registered tools;
- no arbitrary code or shell tool is exposed;
- external writes are absent by default;
- source text remains linked as evidence rather than system instruction.

Residual risk: a model can still produce biased or malicious structured interpretations. Domain validation and adversarial tests are required.

### SSRF and network pivoting

Threat: URL ingestion reaches metadata services or private hosts.

Mitigations: public scheme/host validation, credential rejection, DNS/IP classification on every redirect, redirect cap, timeout, and size cap.

Residual risk: DNS rebinding and network-specific address tricks require egress firewall/proxy enforcement.

### Stored XSS

Threat: a document title or hypothesis claim contains HTML/JavaScript.

Mitigation: dashboard values are HTML-escaped and the dashboard has no write forms.

Residual risk: API clients must escape content in their own UI.

### Unauthorised mutation

Threat: attacker triggers runs, ingests URLs, submits labels, or promotes policy.

Mitigation: mutation endpoints require a configured shared API key; otherwise they are disabled.

Residual risk: one shared key has no user identity, scope, expiry, or audit attribution. Use an identity-aware proxy and rotate secrets.

### Data exfiltration through model provider

Threat: private document chunks are sent to a remote model.

Mitigation: local provider is default; remote provider requires explicit configuration and records provider identity.

Residual risk: remote calls send chunk text to the configured endpoint. Operators must obtain appropriate consent, contracts, regional controls, and redaction.

### Poisoned feedback / optimiser manipulation

Threat: malicious labels drive thresholds toward noisy or suppressed behaviour.

Mitigations: label minimum, bounded mutation, replay improvement, separate shadow labels, policy versioning, experiment records, and manual promotion command.

Residual risk: shared-key feedback has no individual trust weighting; small samples and collusion can still poison optimisation.

### Duplicate or lost work

Threat: crashes or concurrency produce duplicated actions or skipped events.

Mitigations: durable checkpoints, nested-transaction idempotent events, unique keys, per-loop leases, per-loop transactions, retry records.

Residual risk: exactly-once is not globally guaranteed. Future external tools need their own idempotency keys and reconciliation.

### Resource exhaustion

Threat: oversized files, event storms, model retries, expensive tools, or unbounded scans.

Mitigations: size limits, event limits, bounded scan multiplier, chunk/task/tool bounds, retry caps, timeouts, cost fields, and registered tools.

Residual risk: no global API rate limiter and no OS/container quotas in the application.

### Supply-chain compromise

Threat: malicious dependency or container image.

Mitigations: bounded dependency ranges, CI lint/type/test/build, minimal non-root image.

Residual risk: dependencies are not hash-locked or signed in this repository. Deployments should generate a lock/SBOM, scan images, and verify provenance.

## 4. Security non-goals in 0.2.0

- Multi-tenant isolation.
- Formal regulatory validation.
- Fine-grained RBAC/ABAC.
- Field-level encryption.
- Built-in secret manager.
- Tamper-evident cryptographic audit log.
- Sandboxed arbitrary code execution.
- Automated destructive action.

## 5. Secure deployment recommendations

- Use PostgreSQL on a private network.
- Put API behind TLS, authentication, rate limiting, and IP/network policy.
- Store secrets in a platform secret manager.
- Limit outbound network destinations.
- Keep inbox read-only to the service where possible.
- Run containers as non-root with CPU/memory limits.
- Disable remote LLM fallback when silent quality degradation is unacceptable.
- Monitor policy promotion and premise-review events.
- Test restore and incident procedures.
