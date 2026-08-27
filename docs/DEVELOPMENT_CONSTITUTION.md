# AI_BRAIN — Development Constitution

## Article 1 — Research Before Implementation

No major technology is selected because it is popular or familiar.

Research must compare alternatives using evidence.

## Article 2 — Design Before Coding

Every significant subsystem requires:
- purpose,
- responsibilities,
- interfaces,
- dependencies,
- failure modes,
- security considerations,
- test strategy.

## Article 3 — Review Before Delivery

Before code is considered ready, review:
- syntax,
- imports,
- types,
- control flow,
- edge cases,
- error handling,
- security,
- resource usage,
- concurrency,
- maintainability.

## Article 4 — Tests Are Part of the Feature

A feature is incomplete until its intended behavior is covered by appropriate tests.

## Article 5 — Documentation Is a Deliverable

Research, architecture decisions, workflows, and operational behavior must be documented.

## Article 6 — No Chat-Only Knowledge

Important decisions must be persisted in repository artifacts.

## Article 7 — Small Modules

Prefer focused modules with clear contracts.

## Article 8 — Stable Interfaces

Core business logic must not depend unnecessarily on MCP transport or a single AI vendor.

## Article 9 — Security First

Treat external web content, retrieved notes, AI output, and MCP input as potentially untrusted.

## Article 10 — Reproducibility

Development should be reproducible on Kali Linux using documented setup steps.

## Article 11 — Git Discipline

Changes must be reviewable and recoverable.

## Article 12 — Teach the Why

The project is also a learning system. Major implementation choices must be explained well enough that the maintainer understands them.

## Article 13 — Measure Before Optimizing

Performance claims require measurement.

## Article 14 — No Silent Architecture Changes

If a decision needs to change, document why and create/update an ADR.

## Article 15 — Session Continuity

Every meaningful session must leave the repository in a recoverable state.
