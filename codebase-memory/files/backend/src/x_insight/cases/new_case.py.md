# backend/src/x_insight/cases/new_case.py

Source SHA-256: e86c01440eb09c78c4dcb6c969acb9fe8f0c985a69f2033c30990b1c67a1b90c

## Purpose
Validate typed new-case state and derive fixed-model evidence.

## Behavior and constraints
The normal encounter wrapper calls `evaluate_frozen_inputs`, which also
accepts frozen demographics, author ID, labs and assessment definitions for
exploration. `validate_pins` checks model/content and medication release
identities. `prepare_autosave` owns recorded override attribution.
`new_case_plans` uses the evaluated evidence to generate immutable proposals
and v4 signatures.
