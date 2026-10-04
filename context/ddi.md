# DDI module design

## No LLM as the DDI engine

**Id:** e0809f6b-19b4-4666-b22b-60d4ff23afda
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc DDI-Module.md, 2026-10-03
**Revisit when:** the DDI module is implemented or the checker strategy changes

The runtime DDI checker is a deterministic database lookup over a
versioned knowledge base built offline. The LLM is never asked at
runtime whether two drugs interact. This is recorded in the design as
the most important architectural decision. The module is a proposed
design, not an implemented checker.

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
