# backend/src/x_insight/assessments/engine.py

Source SHA-256: e551082bc3b0322d6480f257429869823f9f4c30199632634d7e2343023e1229
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Pure, declarative assessment evaluator shared by diagnosis, PANSS and C-SSRS: loads a
definition file and turns answers into a status, scores and findings. The server is the only
authority — the browser never computes a verdict.

## Important Symbols
- `load_definition()` — validates an allowlisted schema; **`_FORBIDDEN_KEYS`**
  (`expression`, `eval`, `exec`, `script`, `code`, `lambda`, `function`, `python`,
  `javascript`, `__import__`) anywhere in the file raise `DefinitionError`.
- `evaluate(definition, answers)` → `status`, `missing_item_ids`, `item_errors`, `scores`,
  `findings`, `definition_version`.
- Statuses: `unanswered`, `partial`, `complete`, `not_assessed` (`__skipped`), `bypassed`
  (diagnosis-only `__bypass`).
- Three-valued Kleene logic for diagnosis (`_kleene_and/or/not`), overall
  `criteria_satisfied` / `below_threshold` / `indeterminate`; always emits
  `symptom_count_alone_insufficient: True`.
- `normalize_cssrs_answers()` — drops ~35 retired item ids and maps absent → `unknown`.

## Behavior and Constraints
- **No implicit minimum scores**: a missing answer suppresses aggregates to `null`, never
  zero. PANSS partials report finished subscales but force `total=None` and drop the band;
  diagnosis/C-SSRS report nothing until complete.
- PANSS total bands are `informational_only` + `not_a_treatment_gate`.
- A higher-level C-SSRS "yes" never fills lower levels; there is no composite score.

## Related Files
- `backend/src/x_insight/assessments/diagnosis.py`, `panss.py`, `cssrs.py` — per-instrument
  state and server previews.
- `backend/src/x_insight/assessments/released.py` — loads `content/assessments/*.json`.
