# backend/src/x_insight/cases/reasoning_exploration.py

Source SHA-256: d81f899901a232f5c454392c48ffe657f87b18820e48a3d2a2db6206627e4953

## Behavior
explore_proposal requires the caller to resolve and authorize the proposal.
It verifies pinned inputs, checks patient replay against frozen values and
compares a normalized scenario through the same workflow and candidate policy.
The result includes exact attribution and an active graph. Lookups are reads.
source_controls exposes the shared read-only workflow allowlist. Failures
during frozen resolution use the baseline conflict envelope, while invalid
submitted overrides keep their validation status.
