# ADR 0001: Event-driven multi-loop architecture

## Status

Accepted.

## Context

A fixed plan/act agent loop is insufficient for a system that must discover its own triggers, tune its own thresholds, and review its own scope. A single task tree also makes it difficult to run document extraction, utilisation, evaluation, hyperparameter tuning, and meta-premise review in parallel.

## Decision

OpenOyster uses an immutable event stream with per-loop cursors. Loops communicate by emitting events and writing durable memory objects. Each loop is independently deployable.

## Consequences

Positive:

- Multiple loops can consume the same event.
- Behaviour is auditable.
- Local supervisor and distributed workers can share the same contract.
- Policy tuning and premise review can observe system behaviour.

Negative:

- More database tables and event discipline are required.
- Developers must document new event types.
- Exactly-once side effects require careful loop design.
