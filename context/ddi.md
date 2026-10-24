# DDI module design

## No LLM as the DDI engine

**Id:** e0809f6b-19b4-4666-b22b-60d4ff23afda
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc DDI-Module.md, 2026-10-03
**Revisit when:** the checker strategy changes
**Verification:** corroborated — re-confirmed on 2026-10-21 against the confirmed existing foundation and backend/src/x_insight/ddi/checker.py and router.py. The 2026-10-20 review flag identified a stale proposed-only description; implementation is now acknowledged without changing the original rationale.

The runtime DDI checker is a deterministic database lookup over a
versioned knowledge base built offline. The LLM is never asked at
runtime whether two drugs interact. This is recorded in the design as
the most important architectural decision. The deterministic checker and runtime
HTTP lookup are implemented; real source publication remains separately gated
by owner approval.

**Reason:** an LLM answering "do these interact?" would introduce
hallucination risk, nondeterministic answers, latency, difficult
auditing and difficult regression testing. For a medical CDSS the final
interaction determination should be traceable back to a stored record.
An LLM may optionally help during ingestion (for example identifying an
unusually formatted paragraph), but anything it produces is validated
before entering the production knowledge base.

**Rejected alternative:** send the monographs to a GPT-like model at
runtime and display its answer. Rejected for the reasons above; RAG /
vector search through the monographs is likewise avoided for
interaction determination.

## Offline ingestion, simple runtime lookup

**Id:** 4f7ce741-0537-400b-8800-7b2b2ac00dde
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc DDI-Module.md, 2026-10-03

The design separates an offline ingestion/build phase (preprocessor,
deterministic parser, normalizer, validation, knowledge base) from a
runtime lookup phase (normalize, generate pairs, lookup, aggregate)
within the existing application modules. S2 owns the reusable catalog
and DDI definitions; S1's Medication Review consumes them.

**Reason:** the ingestion pipeline can be complicated while the runtime
checker stays extremely simple. The phases are not new independently
deployed subsystems and not separate authoritative databases.

**Rejected alternative:** a separate DDI service or database. Rejected
because the separation of phases already gives the needed boundary
without new deployment units.

## Evidence model preserves every source assertion

**Id:** fe841c13-85c8-4146-a20a-29e08e4d91c7
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc DDI-Module.md, 2026-10-03

The core table is interaction_evidence: multiple source assertions
about the same pair are preserved (the Sitagliptin sample places
ofloxacin in both Monitor Closely and Minor — both assertions are kept).
Lookup is symmetrical through canonical unordered pair keys while the
underlying statement keeps its directional subject/object. Severity is
stored separately from management action because the prose may carry a
stronger recommendation than the category heading.

**Reason:** the source material must remain intact; structured fields
are the queryable representation and raw source text is the clinical
provenance, not a replacement for it. Conflicts are surfaced to
review, never silently resolved.

**Rejected alternative:** one interaction row per pair with a single
severity value. Rejected because differing source assertions would be
lost and code must not arbitrarily overwrite one record with another
when both monographs exist.

## DDI data lives in the application database

**Id:** f1ae8eff-a76c-4d11-9609-9992877300b9
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc DDI-Module.md, 2026-10-03

The structured knowledge base is stored in the application's
authoritative relational database (PostgreSQL proposed); the local DDI
release is bundled and imported there rather than adding a separate
runtime database.

**Reason:** at the current scale of roughly 100 medication monographs
a relational SQL handles the workload perfectly well; a DDI network
looks like a graph conceptually but does not need one.

**Rejected alternative:** vector database, Elasticsearch, Neo4j,
Redis or Kafka. Rejected as unnecessary for this scale; if the app
already has PostgreSQL, introducing another database is avoided.

## Deterministic parser with source-count integrity checks

**Id:** dab1212f-0d56-457a-a548-9f8647b08d75
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc DDI-Module.md, 2026-10-03

Parsing uses a state machine over the structured monographs (category
headings such as Contraindicated (0) / Serious (4) / Monitor Closely
(92) / Minor (70)). Declared per-category counts must equal the number
of parsed entries; a mismatch fails that document's release
eligibility and is never repaired by truncation or padding.

**Reason:** the source counts are one of the strongest characteristics
of these documents — a built-in validation system that is much safer
than judging parsing quality subjectively.

**Rejected alternative:** prompt an LLM with all 100 files at once.
Rejected as too large a unit of work that produces architectural drift
and offers no built-in count validation.

## No fuzzy medication matching

**Id:** 392d99ba-ec14-49ec-94a6-a28aa46d6fc5
**Type:** constraint
**Status:** active
**Evidence:** confirmed
**Source:** project design doc DDI-Module.md, 2026-10-03

Look-alike names (clozapine / clonazepam / clomipramine) must not be
resolved through uncontrolled approximate matching. Normalization goes
lowercase/whitespace, exact canonical match, approved alias match,
external terminology mapping if available, then a manual resolution
queue. Failure yields MEDICATION_NOT_RECOGNIZED rather than a guess.

**Reason:** a wrong resolution is worse than an explicit unknown for a
medical checker. Patient entry uses catalog IDs, never arbitrary
medication strings or strength stripping.

**Rejected alternative:** fuzzy or suffix-stripping matching without
reviewed rules. Rejected because silent mismatches would corrupt
interaction results; salt removal, strength stripping and
combination-drug splitting are applied only under reviewed rules.

## Complete supplied DDInter scope with stable source identities

**Id:** fc8a7ef1-c2f4-4e9d-8941-d0d5b0732578
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** explicit implementation request, 2026-10-21

Include every drug ID from both endpoints of the eight supplied DDInter CSVs:
1,939 IDs in this snapshot, including non-psychiatric entries. Completeness means
the supplied files, not DDInter's broader catalog. The supporting explanation is
preserved with the eight CSVs. Stable concept/catalog identifiers derive from the
DDInter ID, not a name or file order; source spelling is retained and the generic
type is `drug`.

**Reason:** the supplied files define the requested scope. Their rows do not
reliably classify ingredients, combinations, herbal products, or other subtypes;
assigning finer types would manufacture source knowledge.

**Rejected alternatives:** a psychiatric-only filter or 100-drug cap would omit
requested supplied entities. Name/order-derived identities would break the
confirmed source-identity contract. Additional catalog acquisition, scraping, and
monograph merging are outside this release's chosen source boundary.

The [operations runbook](../docs/ddinter-operations.md) covers reproducible builds,
review, and publication; snapshot counts/hashes are in its linked evidence.

## Positive assertions remain limited clinical coverage

**Id:** 21e2d4e0-bfaf-4e14-824d-406b3d06a5d7
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** explicit implementation request, 2026-10-21
**See:** ddi.md#evidence-model-preserves-every-source-assertion — fe841c13-85c8-4146-a20a-29e08e4d91c7 — as of 2026-10-21

The DDInter profile includes every supplied occurrence (222,383 in this snapshot),
including repeated exports of the same unordered pair. Repetition preserves source
locations and is not independent clinical corroboration. A/B order is retained as
source provenance while causal direction stays unknown.

**Reason:** the CSVs provide positive severity assertions, not complete pair
coverage, mechanisms, management recommendations, or pair-specific citations.
Therefore a complete input import still becomes `released_limited`, with no excluded
sources and missing pairs reported as `coverage_unavailable`. This exception to
the excluded-source rule applies only to `ddinter-positive-assertions/1`.

**Rejected alternatives:** deduplicating away occurrences would lose traceability;
interpreting an absent row as no interaction would overstate coverage. Generated
clinical prose or scraped explanations would exceed the accepted severity-only
scope. Native `Major > Moderate > Minor > Unknown` is preserved instead of
relabeling Major as Contraindicated or inferring management advice. Unknown-only
assertions are still interactions, not missing coverage.

Explicit owner approval remains required, including every Major occurrence's
hash/location reference and explicit acceptance of severity-only data and
unavailable pair coverage. Successful parsing is not clinical review. The
operational approval procedure is in the [runbook](../docs/ddinter-operations.md#2-complete-explicit-owner-review).

## Release association survives default changes without legacy conversion

**Id:** 1c054d08-2d65-42b1-94da-c1fffe7dce14
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** explicit implementation request, 2026-10-21

Search, medication validation/checking, analysis, follow-up, and signed history
retain their associated immutable release. Existing `catalog_*` IDs are not
automatically matched or converted to DDInter IDs. Before a strict report pin,
a separately validated selection-release association preserves the catalog used
for the selected IDs; only strict medication save establishes a current report.

**Reason:** a medication shown from one release must not later be validated
against another after default publication, reload, or reentry. Existing encounter
provenance, fingerprints, analysis references, and signed records must remain
interpretable against their original release.

**Rejected alternative:** automatically rebase drafts or convert legacy IDs when
publishing DDInter. This would silently change the release/identity meaning of
existing data. A configurable default instead changes selection for unassociated
work; previous releases remain readable and support rollback without deletion.

## Bootstrap from committed sources without generated LFS evidence

**Id:** 80c59e6c-ce6d-43a5-9e97-745b698d9a9f
**Type:** decision
**Status:** active
**Evidence:** inferred
**Verification:** corroborated — rechecked on 2026-10-23 after the provisioning-path change; automatic local startup reproduced the approved release and passed real registration in both browsers
**Revisit when:** the bundled source snapshot, parser, approval manifest or provisioning path changes

Startup preparation rebuilds the existing approved DDInter release from ordinary
Git source files and its retained approval inputs, then publishes through the normal
validator. The source-only choice remains current after moving setup into normal
launch. Runtime requests do not import the dataset. Existing released datasets are
retained unless the bundled version is explicitly selected.

**Reason:** the confirmed requirement is that a GitHub clone can use DDI during
new-patient registration after normal setup. A release published only into one
local database cannot meet that requirement. Rebuilding from source avoids making
setup depend on downloading the large generated evidence archive through Git LFS,
while preserving the exact reviewed content identity and approval gate. This
implementation tradeoff is inferred from the selected design and its verification;
it does not represent a new owner approval of clinical data.

**Rejected alternative:** retain manual-only publication for each clone. It leaves
normal migration with empty DDI tables and the registration failure unresolved.

**Rejected alternative:** add another compressed generated bundle for setup.
Source reconstruction uses the already committed source/approval inventory and
avoids a second generated payload to retain and keep aligned with that inventory.

**Consequence:** first setup pays the offline rebuild/import cost; exact repeats
reuse the immutable release. Operational commands and recovery live in the
[DDInter runbook](../docs/ddinter-operations.md#fresh-installation-with-the-bundled-approved-release).

## Prepare the approved dataset during normal startup

**Id:** c6ccdd94-6ee1-4978-8124-484cd9b2fd66
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** explicit startup requirement, 2026-10-23
**Verification:** corroborated — actual local startup prepared the genuine release before API/Vite launch; both browser registration tests passed
**Revisit when:** supported launch commands, setup privileges or schema/DDI preparation change
**See:** ddi.md#bootstrap-from-committed-sources-without-generated-lfs-evidence — 80c59e6c-ce6d-43a5-9e97-745b698d9a9f — as of 2026-10-23

Normal local and production launch includes schema and approved DDI preparation,
and dependent application processes wait for successful completion. Manual setup
commands remain available for operator recovery and inspection.

**Reason:** the confirmed startup requirement excludes a separate migration command.
Merely bundling the source data while requiring an additional setup action leaves
the requested fresh-clone experience incomplete.

**Rejected alternative:** require every installer to run the schema-and-data command
separately. The earlier integration used that path, but it was explicitly rejected
because ordinary startup must make the approved dataset ready to use.

**Consequence:** first launch may spend several minutes rebuilding and importing
the approved release. Setup failures stop dependent startup; exact repeated setup
reuses the release. This is startup preparation, with the runtime lookup and
clinical approval boundaries unchanged. Procedures and recovery are in the
[DDInter runbook](../docs/ddinter-operations.md#fresh-installation-with-the-bundled-approved-release).
