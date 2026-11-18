# backend/src/x_insight/cases/reasoning_paths.py

Source SHA-256: 6f7220fb1885531828013e0aba81499dfc7201301e794c17d8dacda89e1503e1

## Purpose
Build deterministic active graphs for frozen proposal comparisons.

## Behavior and constraints
`build_active_graph` derives source paths from effective factors, structural
CPT dependencies from row pairs, and rule/filter paths from actual
evaluations. It unions patient and scenario membership, preserving departed
paths as superseded. Callers pass complete evaluator and candidate result
mappings without recomputing decision branches in graph presentation.
