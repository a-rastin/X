# backend/src/x_insight/cases/follow_up.py

Source SHA-256: 9d86e36c195efd02e9fed7beb8c632b647a16b8be14e4924312785e172b4bd04

## Purpose
Validate follow-up drafts and evaluate current history, effects and branch questions.

## Behavior and constraints
`evaluate_state` combines lab suggestions, inherited answers and manual
overrides, then delegates to `follow_up_rules`. Proposal verification and
exploration can pass pin-resolved assets and definitions to both structural
validation and evaluation. `content_pins` includes exact assessment file
hashes. The calculation never persists proposals.
