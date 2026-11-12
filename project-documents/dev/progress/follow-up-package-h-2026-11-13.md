# Follow-up Package H partial checkpoint — 2026-11-13

Base revision `eb4663eddc5bdc023d57f7d8249c65c78ad32a12`; changes are
uncommitted. Package H is **incomplete**. The application Dockerfile now copies
`content/follow-up`, alongside its existing New case and Lexicomp dependencies.
The production packaging check requires that copy, and a focused test loads the
hash-checked content through `X_INSIGHT_CONTENT_DIR` outside the repository cwd.
No application database or production deployment was changed.

On the working tree, `make check`, `make test-web` (22 passed), `make prod-check`,
and the focused packaging, source, rule, candidate and plan-input selector
(`tests/operations/test_follow_up_packaging.py tests/operations/test_new_case_packaging.py
tests/models/test_follow_up_sources.py tests/assessments/test_follow_up_rules.py
tests/models/test_follow_up_candidates.py tests/assessments/test_follow_up_plan_inputs.py`)
passed (49 tests). The web build reported its existing large-chunk warning.
`docker build --network host -f deploy/Dockerfile.app -t
x-insight-follow-up-packaging-check .` succeeded, and `docker run --rm -w /tmp`
loaded `follow-up-v1` and all its pinned assets inside that image. The first
default-network build was interrupted after container DNS resolution failed;
it is not counted as a pass.

Normal `python -m x_insight.operations.migrate` completed against a fresh,
disposable PostgreSQL 16 database at schema `0032`, installing one oral model
and the pinned Lexicomp release `ddi-2adbb775258d` and DDInter release. The
model's installed source hash matched the follow-up manifest. The
follow-up table's three role grants and named workflow, signature and uniqueness
constraints were checked; in-process HTTP health and readiness both returned
200. A clone of this empty fresh database was downgraded to `0031`, then upgraded
by the same normal setup command; the schema returned to `0032` and both release
hashes and the model ID matched the original. This synthetic upgrade contained
no historical patient data and is not historical compatibility evidence.

`make test-recovery` passed 58 tests against a separate disposable database at
`0032`. This covers the existing supported recovery paths and the deliberate
follow-up rejection; it does not prove a v5 archive can be restored. The first
recovery and HTTP regression attempts were interrupted and are not counted.
The rerun of `make test-backend TEST='tests/http/test_follow_up_state.py
tests/http/test_followup.py tests/http/test_established_followup.py
tests/http/test_new_case.py tests/http/test_new_case_plans.py
tests/http/test_exports.py'` passed 60 tests against another disposable database.
The full `make test-backend` run was stopped after 375 of 1,481 tests passed;
it is incomplete and must be rerun after the missing integration is finished.
Browser acceptance and the missing proposal-route selector were not run.

Handoff: F lacks public proposal persistence and v5 signing, G lacks the wizard,
and follow-up creation remains disabled. The restore validator deliberately
rejects follow-up records; consequently no public v5 mixed-history, restore or
subsequent-follow-up acceptance is possible yet. Complete B/F/G and their
remaining integration first; then perform the full package H acceptance matrix,
including real public HTTP/browser flows, mixed backup and restore, historical
signature checks and the complete repository gates. Do not report package H or
the follow-up workflow complete based on the packaging checks.
