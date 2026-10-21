# DDInter isolated acceptance evidence — 2026-10-21

Subsequent owner-confirmed publication to the local application database is
recorded separately in [publication.md](publication.md). The account below
describes the earlier simulated acceptance drill and its then-pending review.

This is deployment planning evidence, **not owner approval or application
publication**. The candidate remains `awaiting_review`; no application
`DATABASE_URL` was configured. Only named disposable databases were used, with
ephemeral approval explicitly labeled `SIMULATED TEST FIXTURE ONLY`. Acceptance
servers have been stopped.

- [report.json](report.json) preserves the exact compact report: all nine original
  byte hashes, per-source counts, severities, anomalies/conflicts and limitations.
- [candidate_dataset.json](candidate_dataset.json) preserves exact candidate
  metadata/artifact hashes. The independently rebuilt acceptance candidate matched
  the main `/tmp/ddinter-candidate` bundle byte for byte. The candidate hash is
  `ed2811f81e566c753aad4772ca81f0ea85e40365d8694e192739660b2fff52b4`.
- [acceptance-summary.json](acceptance-summary.json) retains sanitized measurements
  and canonical before/after restore hashes from
  `/tmp/ddinter-acceptance-results.json`. It excludes connection strings,
  simulated approval/release identities, backup archives, and source/evidence payloads.

The complete pending review bundle is archived at
[`content/ddi/ddinter/2026-10-21/`](../../../content/ddi/ddinter/2026-10-21/),
with `evidence.jsonl` stored through Git LFS. Follow the
[operator runbook](../../ddinter-operations.md) to download that payload before
review or publication. All 16 archived files preserve the bytes from
`/tmp/ddinter-candidate`, including the current pending manifest (both acceptance
flags true, decision `awaiting_review`, no review date/record or reviewed evidence).
The independently built acceptance copy is `/tmp/ddinter-acceptance/candidate`;
that copy and original backend handoffs/logs under `/tmp/ddinter-*` are temporary.
S18/S20 record the relevant test commands and limits.

## Full supplied snapshot

| Check | Result |
| --- | --- |
| Selectable catalog IDs | 1,939 |
| Preserved occurrences / unique pairs / repeats | 222,383 / 160,235 / 62,148 |
| Unique Major / Moderate / Minor / Unknown pairs | 26,914 / 96,675 / 6,833 / 29,813 |
| Major occurrence review inventory | 33,896 |
| Source byte/record/span/endpoint provenance checks | Every one of 222,383 occurrences |
| Dangling evidence endpoints | 0 |
| Catalog IDs reached by backend pagination | 1,939 |
| Backup → isolated restore | All four full DDI table canonical hashes and all nine original source byte hashes match |

The complete import used the real supplied corpus. Browser tests used a synthetic
1,939-ID catalog and focused interaction fixtures. Proposal rendering used a real
checker report with controlled generation fixtures; live-provider generation was
not established.

## Measured capacity

Shared 2 CPU / approximately 4 GB RAM host, with concurrent tests. These single-run
measurements reflect contention and are not an SLA or a deployment capacity limit.

| Operation | Wall time / size | Peak RSS |
| --- | --- | --- |
| Build | 20.99 s | 90,432 KiB |
| Approved publication call (including revalidation) | 83.97 s | Included in phase below |
| Whole import acceptance phase | 192.96 s | 206,240 KiB |
| Database after import | 193,420,311 bytes | — |
| Backup creation | 146.05 s | 1,511,668 KiB |
| Compressed backup ZIP | 19,038,844 bytes | — |
| Expanded `database.json` | 219,115,919 bytes | — |
| Ten-drug / 45-pair checker call | 151 ms; one batched evidence query | Not separately measured |
| Sample pair query plan | `ix_ddi_evidence_release_pair`; 0.039 ms execution | — |

Restore committed successfully before the original harness failed a textual JSON
object-key-order comparison. Resumed read-only verification compared canonical
semantic values: all four DDI tables match, including approval metadata, raw CSV
strings, original source bytes, release hashes, catalog, and evidence. Key order
was the representation difference; source bytes and data values were preserved.
Precise original restore duration and peak RSS are unavailable. The resumed
65.41 s / 166,384 KiB measurement is **verification only**, not a full restore
measurement. Rehearse and measure the complete recovery path on the target host.

The increased limits are 256 MiB per file, 512 MiB expanded archive and 100 MiB
compressed archive; backup creation and restore enforce the same bounds. Older
`0021` monograph backup compatibility was also tested.

For implementation verification and outstanding deployment gates, see
[S18](../../../project-documents/dev/progress/s18.md#follow-up-2026-10-21--complete-supplied-ddinter-candidate)
and [S20](../../../project-documents/dev/progress/s20.md#follow-up-2026-10-21--ddinter-workflow-and-release-association).
