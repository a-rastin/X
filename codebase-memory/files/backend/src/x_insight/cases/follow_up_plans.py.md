# backend/src/x_insight/cases/follow_up_plans.py

Source SHA-256: da47d8db87edf88fb8f766e41e4091a11a5d3e42c6ae4d3bd1c194b7fd0a4845

## Purpose
Generate immutable follow-up proposals and sign v5 snapshots.

## Behavior and constraints
`resolve_pinned_content` verifies installed assets, model bytes, exact
assessment bytes, DDI release and catalog IDs with reads. `verified_inputs`
uses those assets to evaluate and fingerprint saved state. `generate_proposal`
deduplicates by input and content hashes. Command-level `context` locks
account, patient and encounter before generation or signing. `sign` checks
completeness and freshness, persists one replay-safe v5 signature and advances
lifecycle.
