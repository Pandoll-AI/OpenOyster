# ADR 0001: Event-driven multi-loop architecture

## Status

Accepted.

## Context

A fixed plan/act agent loop is insufficient for a system that must discover triggers from persisted observations and keep extraction, hypothesis work, planning, execution, utilisation, and evaluation independently retryable. A single task tree makes it difficult to preserve durable provenance and loop-level failure isolation.

## Decision

OpenOyster uses an immutable event stream with per-loop cursors. Loops communicate by emitting events and writing durable memory objects. Each loop is independently deployable.

## Consequences

Positive:

- Multiple loops can consume the same event.
- Behaviour is auditable.
- Local supervisor and distributed workers can share the same contract.
- Evaluation and feedback can observe system behaviour.

Negative:

- More database tables and event discipline are required.
- Developers must document new event types.
- Exactly-once side effects require careful loop design.
