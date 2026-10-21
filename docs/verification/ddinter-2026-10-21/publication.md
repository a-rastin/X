# DDInter local application publication — 2026-10-21

The owner explicitly confirmed completed DDInter review and requested publication
using the runbook. That confirmation is recorded in the separate
[owner-reviewed manifest](../../../content/ddi/ddinter/2026-10-21/owner-reviewed-manifest.json).
Its exact 33,896 Major occurrence references were transcribed from the inventory
to record the owner's completed review. The agent did not perform a separate
clinical review. The original pending manifest and all 16 archived candidate files
remain unchanged.

The local application target was identified using the application's default
connection: database and owner/import role `x_insight`, loopback port 5432. There
was no `DATABASE_URL`, `.env`, production environment file, or running HTTP/worker
process. The database contained application records, schema `0021`, and zero rows
in all four DDI release tables. The owner directed the agent to discover the
configuration and proceed. No other deployment is claimed published.

Publication completed atomically through `python -m x_insight.ddi publish`, using
an operator-owned copy of the complete archive and the genuine owner-confirmed
manifest. The command returned `reused=False`.

| Identity | Value |
| --- | --- |
| Candidate SHA-256 | `ed2811f81e566c753aad4772ca81f0ea85e40365d8694e192739660b2fff52b4` |
| Release version | `ddi-c45e2051cf16` |
| Release content SHA-256 | `c45e2051cf16dfb6b0d7253ce87589ef293f6ee64cf78066e3d2a59bdaa8c039` |
| Approval / stored status | `approved_limited` / `released_limited` |
| Schema transition | `0021` → `0022` |
| Default | Newest release, with no environment override |

Before migration, an unmodified copy of the schema-`0021` backend at commit
`ab111d7` created a full backup through its authenticated administrator API. The
archive was downloaded, its digest checked, and both archive verification and
restore validation passed with manifest schema `0021` and 32 tables. Migration
`alembic upgrade head` completed at `0022`; application readiness returned 200
before publication. No runtime-role write grants were added.

Post-publication checks against the application database passed:

- Exactly 1,939 catalog IDs, 222,383 occurrences, 160,235 unordered pairs and
  62,148 repeated occurrences; no dangling endpoints.
- Unique pair severities: Major 26,914; Moderate 96,675; Minor 6,833;
  Unknown 29,813. All nine stored original source byte hashes match the archive.
- Authenticated application API pagination reached every catalog ID, including
  offsets beyond 100 and 1,000; exact DDInter ID search passed. Default search
  selected the published version and reported severity-only source details.
- Authenticated DDI checks preserved `interaction_found` for Major and Unknown
  assertions, `coverage_unavailable` for an absent pair, and limitations with
  zero/one selected drug. Reversed and duplicate selections produced identical
  pair results. The provider was disabled.
- Patient, encounter and user counts stayed unchanged.

API verification used the actual application with in-process HTTP transport and
the real target database. It did not start a listening server, create or edit
patient encounters, exercise a browser, or generate a proposal with a provider.
Existing encounter/release reconciliation behavior retains its earlier test
evidence; this publication adds no claim of a new browser acceptance run.

A fresh schema-`0022` backup was then created and downloaded through the same
admin API. Digest verification, archive verification, and restore validation
passed. Both before/after backups are retained with private permissions under
`.operator/ddinter-2026-10-21/`, excluded from Git. Their successful backup jobs
also retain the original staging archives for the application's download route.
Only sanitized identities, counts, digests and outcomes belong in the
[publication summary](publication-summary.json); backup archives contain
application data and are not versioned.

The post-publication archive completed the full staged restore procedure into
`x_insight_ddinter_restore_20261021` on a separate PostgreSQL 16 instance with a
private Unix socket. Restore committed successfully, readiness passed,
maintenance cleared, all four DDI tables' canonical hashes matched the published
database, and all nine original source hashes matched. Migration, staging,
restore and final hash verification took 227.42 seconds with 866,808 KiB peak RSS
for the rehearsal process. This measures this local run, not a capacity guarantee.
The separate instance was stopped after verification; no restore was applied to
the application database. The unused empty rehearsal database initially created
on the main server was removed after an explicit empty-table check.

This is the first DDI release on the target, so no previous DDI default exists.
The retained pre-upgrade archive and compatible `0021` code provide the recorded
pre-publication recovery path through the deployment runbook's staged procedure.
