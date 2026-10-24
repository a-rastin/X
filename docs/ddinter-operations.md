# DDInter build, review, publication, and rollback

Use this runbook for the eight supplied DDInter CSV exports and their
`docs/explanation.txt`. It supplements the [deployment runbook](../deploy/README.md)
and preserves the existing monograph build/publish path. Commands below run from
`backend/` unless a repository-root working directory is stated.

The full candidate is archived in
[`content/ddi/ddinter/2026-10-21/`](../content/ddi/ddinter/2026-10-21/).
On 2026-10-21, the owner confirmed completed review and directed publication.
The separate [`owner-reviewed-manifest.json`](../content/ddi/ddinter/2026-10-21/owner-reviewed-manifest.json)
records that confirmation, all 33,896 Major occurrence references, and the limited
coverage acceptance. The local application database `x_insight` on port 5432 was
backed up, migrated from `0021` to `0022`, and published as
`ddi-c45e2051cf16` (`released_limited`). See the
[publication record](verification/ddinter-2026-10-21/publication.md) for target
verification and recovery evidence. Normal fresh-deployment setup now installs
this same approved release as described below; existing deployments still need
their own target checks before deliberate publication or default changes. The earlier
[isolated acceptance evidence](verification/ddinter-2026-10-21/README.md)
used explicitly simulated test approval and remains a separate historical drill.

The archive contains all 16 original and generated files, byte for byte. Only
`evidence.jsonl` uses Git LFS; its Git entry is a pointer to the full payload.
Normal application setup does **not** require this LFS payload: the bootstrap
rebuilds the generated artifacts from ordinary Git files. To inspect or manually
publish the archived generated bundle without rebuilding it, install Git LFS
before cloning, or run these commands from the repository root in an existing checkout:

```sh
git lfs install --local
git lfs pull --include="content/ddi/ddinter/2026-10-21/evidence.jsonl"
```

After downloading the LFS payload, copy the archived directory to a separate
operator-owned working directory such as `/tmp/ddinter-candidate` for the commands
below. The original `review-manifest.json` retains the pending review state; the
owner's reviewed manifest is separate. Committing the bundle is separate from
pushing Git commits and LFS objects to the remote.

## Fresh installation with the bundled approved release

After configuring the application, run `make dev` locally or
`make prod-up PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"` after building production
images. No separate migration or import command is needed. Local startup waits
for database health and runs `x_insight.operations.migrate` before API/web launch.
Production startup runs that same operation in the one-shot `migrate` service;
API and worker require successful completion. It upgrades schema to `0022` and
bootstraps approved DDI using the setup connection. Manual `make migrate` and
`make prod-migrate` remain optional operator tools; direct Alembic is schema only.

The bundle under `content/ddi/ddinter/2026-10-21/` supplies the eight CSVs,
`sources/docs/explanation.txt`, `candidate_dataset.json`, and the separate
`owner-reviewed-manifest.json` as ordinary Git files. The production image copies
these inputs. Bootstrap rebuilds into temporary staging, checks that the candidate
matches the committed inventory, and uses the existing approval/publish validator.
It needs no generated LFS evidence, network, LLM, or new clinical approval. It
reuses the existing `approved_limited` decision; data and coverage do not change.

On a database with no released dataset, successful setup prints
`READY: ddi-c45e2051cf16 (reused=False)`. Expect 1,939 drugs and 222,383 occurrences,
with release content hash
`c45e2051cf16dfb6b0d7253ce87589ef293f6ee64cf78066e3d2a59bdaa8c039`.
First setup may take several minutes for the offline rebuild/import; allow it to
finish before using Medications. This remains `released_limited`: severity-only
positive assertions, no inferred management advice, missing pairs unavailable.

Repeated setup finds the exact release hash and prints `reused=True` without
rebuilding sources. If any other released dataset already exists, bootstrap prints
`SKIPPED: existing DDI release preserved` and leaves it in place. Setting
`DDI_DEFAULT_DATASET_VERSION=ddi-c45e2051cf16` explicitly allows installation of the
bundle alongside existing releases. Bootstrap does not repair any other invalid
configured default; runtime lookup continues to fail explicitly until it is corrected.
Existing encounter pins and selection associations retain their release.

An already schema-migrated target with no DDI data is also provisioned by normal
startup. An operator who wants only the data step can optionally run from
`backend/` with its privileged `DATABASE_URL` securely configured:

```sh
uv run python -m x_insight.ddi bootstrap
```

The subcommand also accepts `--database-url` and `--bundle`; avoid putting secrets
in shell history. A missing/changed source, changed candidate, or invalid approval
fails setup without a partial DDI publication. Schema migration and DDI publication
are separate transactions: a bootstrap failure can leave the schema at head with
no dataset. Restore the matching committed inputs, correct the target/default
configuration as applicable, and rerun `make dev` or `make prod-up`. Setup failures
prevent the dependent servers from starting. Inspect local foreground output or
production `docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" logs migrate`.
`/ready` alone checks schema,
not DDI availability. Back up existing deployments before upgrades as in section 3.

### Fresh-install verification

From the repository root, `bash e2e/run-ddi-fresh-install.sh` creates an isolated
PostgreSQL 16 cluster and copies ordinary Git bundle inputs. It launches the
actual `make dev` recipe from a temporary checkout with test-only configuration,
then tests real registration, lookup, save and reload in Chromium and Firefox.
Only Docker's external commands are adapted to the already-healthy private
database fixture; schema/DDI provisioning, API, Vite and browser operations run
for real. This does not verify Docker runtime ordering. It requires installed
locked backend/web/root
dependencies, Playwright browser binaries, Linux `setsid`, PostgreSQL tools at
`/usr/lib/postgresql/16/bin` (override `PG_BIN`), and free ports 55582/18082/15182
(override `DDI_E2E_PG_PORT`, `DDI_E2E_API_PORT`, `DDI_E2E_WEB_PORT`). When run as root,
it uses the local `postgres` account for PostgreSQL. Services stop on exit; logs and
source-only content remain under the printed `/tmp/x-insight-ddi-e2e.*` directory.
This suite uses `e2e/playwright.fresh-install.config.ts`; the default browser suite
excludes it because other DDI scenarios use synthetic database fixtures.

## 1. Build and retain a review bundle

Use the locked backend environment (`uv sync --locked` during setup) and an output
directory separate from the source directory. No database or provider is needed:

```sh
uv run python -m x_insight.ddi build-ddinter \
  --sources ~/ddinter \
  --output /tmp/ddinter-candidate
```

Required CSV names are `ddinter_downloads_code_A.csv`, `_B.csv`, `_D.csv`, `_H.csv`,
`_L.csv`, `_P.csv`, `_R.csv`, and `_V.csv` with that same full prefix. The supporting
file is `docs/explanation.txt`. A missing file fails the build. Headers must be
exactly `DDInterID_A,Drug_A,DDInterID_B,Drug_B,Level`. Invalid records make the
candidate ineligible; no successful release silently skips invalid rows. Inspect
`report.json` even when the command exits nonzero.

Keep these artifacts together:

| Artifact | Purpose |
| --- | --- |
| `sources/` | Original bytes of all nine inputs |
| `candidate_dataset.json` | Source/artifact SHA-256 inventory, parser/profile, candidate identity |
| `catalog.json` | Every selectable ID, source spelling, and generic `drug` type |
| `evidence.jsonl` | Every CSV occurrence with endpoints, raw record, and provenance |
| `major-review-inventory.jsonl` | Exact references requiring high-risk review |
| `duplicates.jsonl` | Repeated export occurrences and locations |
| `report.json` | Counts, conflicts/anomalies, supplied severity definitions, coverage/limitations |
| `review-manifest.json` | Pending owner-review template bound to this candidate |

The format is `ddi-ddinter-stream/1`, parser `ddinter-csv/1`, source profile
`ddinter-positive-assertions/1`. Records use logical record numbers starting at 1
after the header, plus inclusive physical line spans. A/B ordering is preserved;
interaction direction is `unknown`. IDs are source identities, e.g.
`ddinter:DDInter419` / catalog ID `DDInter419`, never derived from names or row order.

For this supplied snapshot, expect 1,939 selectable IDs, 222,383 occurrences,
160,235 unordered pairs, and 62,148 repeats. Unique pair severity counts are
Major 26,914; Moderate 96,675; Minor 6,833; Unknown 29,813. These are snapshot
verification values, not constraints for future source updates. All nine source
hashes and per-file counts are in the retained [report](verification/ddinter-2026-10-21/report.json).
The candidate hash is
`ed2811f81e566c753aad4772ca81f0ea85e40365d8694e192739660b2fff52b4`.
It is a candidate identity, not the eventual approved release version/hash.

Rebuilding the same bytes/parser yields the same canonical artifacts regardless of
local paths or build time. A build recreates the pending manifest; keep an owner's
reviewed manifest separately and never rebuild over the only review copy.

## 2. Complete explicit owner review

Review the full source inventory, catalog, anomalies/conflicts, duplicate inventory,
and supplied severity definitions. The current report has no anomalies/conflicts.
Repeated rows remain provenance, not independent clinical corroboration.

The owner must review the Major inventory: **33,896 occurrences covering 26,914
unique pairs**. Grouped review may help, but each included occurrence must have an
exact reference in `reviewed_evidence` containing `source_path`, `checksum`,
`span_start`, `span_end`, and `source_record_number`. The publisher requires the
exact inventory without missing, extra, or duplicate references. Do not populate
these records automatically to imply review.

Only after actual review, save a separate `owner-reviewed-manifest.json` with:

- `reviewer: "owner"`, `decision: "approved_limited"`, an ISO-8601 `date`, and a
  nonblank `record` identifying the review record.
- The unchanged candidate hash, format, parser, source profile, artifact hashes,
  source inventory, and exact generated coverage object.
- Both `accept_severity_only` and `accept_unavailable_pair_coverage` explicitly
  `true`; all generated limitations retained; `corrections: []`.
- The reviewed Major occurrence references described above.

A fresh generated manifest starts with both acceptance flags `false`. The original
archived `review-manifest.json` has both flags `true`, while retaining
`decision: awaiting_review`,
`date: null`, `record: null`, and `reviewed_evidence: []`. It is not an approval:
it preserves the pre-approval snapshot. The separate owner-reviewed manifest
records the subsequently confirmed review.
Ingestion success and this runbook provide no approval. Keep the separate
monograph review and [content ledger](../content/review-ledger.md) statuses intact.

Even with all supplied inputs, the published status is `released_limited`:
positive assertions only, missing-pair policy `coverage_unavailable`, clinical
details `severity_only`, no excluded sources. This no-exclusions limited release
is specific to the DDInter profile; existing monograph rules still apply.

## 3. Identify and prepare the application target

Use configured credentials without printing connection strings or secrets. Record
the intended database/server identity, current schema head, existing immutable
releases and DDI counts before changing a deployment. In its database console:

```sql
SELECT current_database(), current_user, inet_server_addr(), inet_server_port();
SELECT version_num FROM alembic_version;
SELECT version, content_hash, status, created_at
FROM ddi_dataset_releases ORDER BY created_at DESC, version DESC;
SELECT 'releases' AS item, count(*) FROM ddi_dataset_releases
UNION ALL SELECT 'sources', count(*) FROM ddi_source_documents
UNION ALL SELECT 'concepts', count(*) FROM ddi_concepts
UNION ALL SELECT 'evidence', count(*) FROM ddi_interaction_evidence;
```

Take a consistent full backup through the currently deployed version's admin
Backup surface before upgrading or migrating an existing deployment. Download it,
verify its digest and validation result, and retain a known recovery path before
publication. A new `0022` process must not be used to label a pre-upgrade `0021`
database backup. Use the staged-restore procedure
in the [deployment runbook](../deploy/README.md#4-compatible-applicationschema-rollback).
Confirm sufficient memory and staging/disk space from the recorded measurements;
backup peak RSS was about 1.44 GiB on the shared acceptance host.

Migration `0022` follows `0021`, adds original source bytes/format/metadata and
structured evidence metadata plus catalog indexes; it adds no tables (32 backup
tables remain). Normal startup performs this schema-and-data operation automatically.
For deliberate operator setup without starting the application, the same operation
can be run with the privileged migration connection:

```sh
uv run python -m x_insight.operations.migrate
uv run alembic current
```

For packaged deployment, follow the image upgrade/backup steps and start with
`make prod-up PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"`. The optional `prod-migrate`
target remains available for operator use. Verify head `0022` and `/api/v1/ready`.
Runtime release
access remains read-only for the application role; publication needs the
privileged import connection, not a grant of runtime write access.
For a separately reviewed replacement release, follow section 4 after migration;
the bundled bootstrap does not approve that new candidate.

## 4. Publish the reviewed release atomically

With `DATABASE_URL` set securely to the verified privileged target connection:

```sh
uv run python -m x_insight.ddi publish \
  --staging /tmp/ddinter-candidate \
  --manifest /path/to/owner-reviewed-manifest.json
```

`--database-url` is also supported, but passing credentials as command arguments
can expose them in process listings/history. The CLI revalidates original bytes,
all staged artifacts, inventories, endpoints, coverage, and approval against a
private rebuilt snapshot. One transaction serializes identical content publication,
inserts bounded batches, verifies counts, and commits. Failure rolls back the whole
import. Identical approved content returns `reused=True`; changed source/approval
content produces a different immutable release. Retain the printed version and
full release content hash from the target, plus the reviewed bundle and backup.

For Compose, mount the complete readable review directory into the one-off
`migrate` service. For example, from the repository root, with
`DDINTER_REVIEW_DIR` an absolute operator-owned directory that also contains
`owner-reviewed-manifest.json`:

```sh
docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" \
  run --rm -v "$DDINTER_REVIEW_DIR:/review:ro" migrate \
  .venv/bin/python -m x_insight.ddi publish \
  --staging /review --manifest /review/owner-reviewed-manifest.json
```

This uses the configured migration connection; perform the target identity checks
first. Never substitute the acceptance drill's simulated approval or test version.

## 5. Select the default and verify the workflow

Set `DDI_DEFAULT_DATASET_VERSION` to an existing immutable version to choose a
default. When unset/empty, selection uses `created_at DESC, version DESC`.
A nonempty invalid version fails explicitly (`503 INVALID_DEFAULT_DATASET`),
without silently choosing another release. Compose forwards the setting to both
app and worker. After editing the external deployment environment file, recreate
both services so the environment changes take effect, from the repository root:

```sh
docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" \
  up -d app worker
```

Existing encounter report pins take precedence. Before the first strict save,
the persisted, validated `selection_dataset_version` also preserves the release
used for selecting medications across reload/reentry and default changes. It does
not claim a current DDI report; strict medication save alone establishes the
authoritative report pin/fingerprint. Conflicting hints are refused. Older
`catalog_*` identities are never automatically converted to DDInter IDs.

Verify using authenticated application sessions:

- `/api/v1/drugs?dataset_version=<version>&limit=25&offset=0` returns the source
  label and total 1,939; Previous/Next can reach all items, including positions
  beyond 100 and 1,000. Search accepts source names and DDInter IDs; selected names
  survive save/reload. Save is available for first pin and stale-fingerprint states.
- Medication preview/save and DDI report keep the searched/selected version even
  if a new default is published before save. Check explicit follow-up reconciliation
  and an existing signed record using its older release.
- Native `Major > Moderate > Minor > Unknown` is visible without monograph
  relabeling or invented advice. An Unknown-only assertion is `interaction_found`.
  An absent pair is `coverage_unavailable`; zero/one drug has no pairs but still
  shows limitations. No missing DDInter row becomes `covered_no_listed_interaction`.
- Medication/proposal views state that mechanism and management were not supplied;
  expandable provenance shows escaped raw CSV and all repeated locations. Reversed
  selection order is symmetric; duplicate selections produce no self-pairs.
- DDI works offline with provider disabled. Checking resolves selected IDs and
  performs one batched indexed evidence query. Proposal display verification does
  not establish successful generation against a live provider.

Recheck target counts and all nine source hashes, then take and validate a new
backup and rehearse restore in an isolated target before relying on recovery.
Backup creation and restore enforce 256 MiB per file, 512 MiB expanded archive,
100 MiB compressed archive, with the existing ZIP-entry/zip-bomb checks. Older
`0021` monograph backups remain supported.

## 6. Verify changes on disposable databases

The focused backend commands and actual run outcomes are recorded in
[S18](../project-documents/dev/progress/s18.md#follow-up-2026-10-21--complete-supplied-ddinter-candidate)
and [S20](../project-documents/dev/progress/s20.md#follow-up-2026-10-21--ddinter-workflow-and-release-association).
Load a separate disposable `TEST_DATABASE_URL` before running pytest; recovery
tests replace database state. `make check` runs repository static/build checks.
Browser journeys are `e2e/ddinter.spec.ts`, the older `e2e/ddi.spec.ts`, and the
signed-follow-up coverage, in Chromium and Firefox against an isolated API.

The full-volume drill is intentionally destructive and restricted to loopback
databases named exactly `ddinter_acceptance` and `ddinter_restore`. With both
dedicated databases created, set `DDINTER_ACCEPTANCE_SOURCES=~/ddinter` (expand
the home path), `DDINTER_ACCEPTANCE_DATABASE_URL`, and
`DDINTER_RESTORE_DATABASE_URL` securely, then run from `backend/`:

```sh
uv run python tests/ddi/full_volume_acceptance.py \
  --workdir /tmp/ddinter-acceptance-new
```

It builds, imports with simulated test approval, backs up, and restores; records
per-phase duration/RSS and exact provenance/canonical data comparisons. It is
never an application publication procedure. The supplied acceptance record includes
a resumed read-only `--phase verify` after an initial JSON object-key-order
comparison failed following a successful restore; full restore timing/RSS was
not recovered. Read the [measurement limitations](verification/ddinter-2026-10-21/README.md).

## 7. Roll back the default

Set `DDI_DEFAULT_DATASET_VERSION` back to the retained previous version, recreate
app and worker as above, and verify new unassociated searches select it. Existing
report pins and selection associations retain their original versions. Do not
delete a release to roll back the default. Schema/image rollback and disaster
restore follow the deployment runbook and require their own compatibility check;
default rollback does not undo migration `0022`.

If target identity, backup validation, privilege, review evidence, or readiness
fails, stop publication and retain the previous release/default. Resolve operator
connection/schema/backup failures with the deployment maintainer; unresolved
approval/coverage questions belong to the owner. For another deployment, identify
and inspect its configured application target before following the publication steps.
