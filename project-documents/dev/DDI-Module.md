# Recommended strategy for the DDI module

Aligned 2026-10-03 to [user-requirements.md](user-requirements.md), [system-design.md](system-design.md), and [system-architecture.md](system-architecture.md). This is a proposed module design, not a verified source-corpus inventory or implemented checker. Monograph names, counts and clinical examples retained below are inherited illustrations; verify them against supplied content before releasing data.

> **Convert the ~100 drug monographs once into a structured, versioned DDI knowledge base, validate it, and make the runtime checker a deterministic database lookup.**

The inherited sample description uses an `Interactions` section with severity groups such as `Contraindicated`, `Serious`, `Monitor Closely`, and `Minor`, and entries containing substance names and mechanism/management text. These are parser drafting leads, not newly checked clinical facts.

**Application contract:** medications are selected from the bundled demo catalog only, without free-text additions or dose/unit/route/frequency/active-stopped fields. A catalog drug can lack DDI coverage and must be marked “coverage unavailable”. The checker uses local bundled data in the application's authoritative database. Findings become part of the immutable initial proposal and signed report, independently of LLM estimation and CPT sliders. Medication edits refresh DDI findings and invalidate only dependent questions/final review; slider changes do not rerun DDI.

---

# 1. Requirements

## Functional requirements

The runtime module accepts saved catalog identifiers and a pinned dataset version (IDs below are illustrative):

```json
{
  "medications": [
    {"catalog_drug_id": "catalog_001"},
    {"catalog_drug_id": "catalog_002"},
    {"catalog_drug_id": "catalog_003"}
  ],
  "dataset_version": "<released-version>"
}
```

Return every source assertion and a status for every unique unordered pair: `interaction_found`, `covered_no_listed_interaction`, or `coverage_unavailable`. Also identify uncovered catalog medications. A missing record is not evidence of no interaction; the covered-no-listed status requires explicit dataset coverage. An illustrative interaction entry is:

```json
{
  "pairs": [
    {
      "drug_a": "sitagliptin",
      "drug_b": "aripiprazole",
      "severity": "monitor_closely",
      "status": "interaction_found",
      "effect": "...",
      "mechanism": "...",
      "management": "...",
      "source": "Sitagliptin.txt"
    }
  ]
}
```

The important distinction is:

```text
No DDI record found
```

must **never** automatically mean:

```text
These drugs do not interact
```

Your database only knows what exists in the source material you imported.

The report pins catalog/dataset versions, medication fingerprint and generation time, preserves raw evidence/provenance, and distinguishes a failed/missing dataset from valid limited coverage. Retain reports in proposal/signed snapshots and backups. Notes are never checker inputs.

---

# 2. High-level architecture

Separate **offline ingestion and runtime lookup phases** within existing application modules. Under the system architecture, S2 owns reusable catalog/DDI definitions and S1's Medication Review consumes them. These phases are not new independently deployed subsystems or separate authoritative databases.

```text
                 OFFLINE / BUILD TIME
┌─────────────────────────────────────────────────────┐
│                                                     │
│    ~100 medication .txt documents                   │
│                │                                    │
│                ▼                                    │
│        Document Preprocessor                        │
│                │                                    │
│                ▼                                    │
│        Deterministic Parser                         │
│                │                                    │
│                ▼                                    │
│        Drug Name Normalizer                         │
│                │                                    │
│                ▼                                    │
│        Validation / QA                              │
│                │                                    │
│                ▼                                    │
│        DDI Knowledge Base                           │
│                                                     │
└─────────────────────────────────────────────────────┘

                         │
                         ▼

                    RUNTIME
┌─────────────────────────────────────────────────────┐
│                                                     │
│ Patient medication list                             │
│         │                                           │
│         ▼                                           │
│ Drug Normalizer                                     │
│         │                                           │
│         ▼                                           │
│ Generate all drug pairs                             │
│         │                                           │
│         ▼                                           │
│ DDI Lookup Service ───────► DDI Knowledge Base      │
│         │                                           │
│         ▼                                           │
│ Aggregate + classify                                │
│         │                                           │
│         ▼                                           │
│ UI / Clinical warning                               │
│                                                     │
└─────────────────────────────────────────────────────┘
```

That separation is important.

The **ingestion pipeline can be complicated**.

The **runtime checker should be extremely simple**.

---

# 3. Do not make an LLM the DDI engine

This is the most important architectural decision.

Avoid:

```text
Doctor enters drugs
       ↓
Send monographs to GPT
       ↓
"Do these interact?"
       ↓
Display answer
```

You would introduce hallucination risk, nondeterministic answers, latency, difficult auditing, and difficult regression testing.

Instead:

```text
Drug A + Drug B
      ↓
normalized IDs
      ↓
database query
      ↓
stored source-derived interaction
```

For a medical CDSS, the final interaction determination should be traceable back to a stored record.

An LLM can optionally help **during ingestion**, for example identifying an unusually formatted paragraph, but anything produced by it should still be validated before entering the production KB.

---

# 4. Your source requires more than a simple `drugA → drugB → severity` table

This is an important finding from the Sitagliptin sample.

Consider ofloxacin.

The inherited example places one assertion in `Monitor Closely` and describes hyper/hypoglycemia; verify the supplied source before using that assertion.

Later, ofloxacin appears again under the `Minor` portion as:

> unspecified interaction mechanism / potential dysglycemia

This illustrates preserving differing source assertions rather than selecting one silently.

Therefore this schema would be wrong:

```text
sitagliptin + ofloxacin
    severity = ?
```

There can be **multiple source assertions about the same pair**.

Use an evidence-based model instead.

---

# 5. Recommended data model

## `drug`

```text
drug
----
id
canonical_name
normalized_name
concept_type
catalog_available
```

Example:

```text
23
sitagliptin
sitagliptin
ingredient
true
```

`catalog_available` describes selectable catalog content, never a patient's medication active/stopped status. Source interaction entities can include drugs, herbs, foods and substances; ingestion concepts do not automatically become selectable patient medications.

Possible values:

```text
ingredient
combination_drug
herbal
food
substance
other
```

---

## `drug_alias`

```text
drug_alias
----------
id
drug_id
alias
normalized_alias
alias_type
```

For the sample:

```text
sitagliptin
Januvia
Zituvio
Brynovin
```

Verify source-backed aliases before releasing them; the names here are inherited examples, not newly verified terminology.

This lets:

```text
Januvia
Sitagliptin
JANUVIA
```

resolve to the same ingestion/catalog concept only through approved aliases. Patient entry uses catalog IDs, not arbitrary medication strings or strength stripping.

Do **not** rely on raw strings when checking interactions.

---

## `source_document`

```text
source_document
---------------
id
primary_drug_id
filename
source_name
source_url
document_date
checksum
imported_at
parser_version
```

## `dataset_release` and `pair_coverage`

Pin every report to an immutable bundled dataset release with source/catalog versions, checksums and validation/review provenance. Store explicit pair coverage with its source basis; recognizing both drugs does not prove the pair was covered. Without evidence or explicit coverage, return “coverage unavailable”, not an empty reassuring interaction list. Full backup/restore preserves releases, raw evidence and coverage alongside proposal/signed report references.

The checksum matters.

If you later replace:

```text
Sitagliptin.txt
```

with a newer version, the system can detect that the underlying source changed and rebuild affected records.

---

## `interaction_evidence`

This should be the core table.

```text
interaction_evidence
--------------------
id

drug_a_id
drug_b_id

subject_drug_id
object_drug_id

source_document_id

source_severity
action_class

mechanism
effect
management
raw_text

source_location

parser_confidence
review_status
created_at
```

Why both:

```text
drug_a_id
drug_b_id
```

and:

```text
subject_drug_id
object_drug_id
```

?

Because lookup should be symmetrical:

```text
sitagliptin + sotorasib
```

and:

```text
sotorasib + sitagliptin
```

must find the same DDI.

But the underlying statement is directional:

> sotorasib will decrease the level or effect of sitagliptin

The directional wording is an inherited source example to verify.

So you must preserve directionality rather than converting everything into a generic `"interacts with"` relationship.

---

# 6. Keep `severity` separate from `recommended action`

Do not collapse all clinical information into one enum.

The document contains information such as:

```text
Severity category:
Serious

Effect:
increases level/effect

Mechanism:
P-glycoprotein transporter

Management:
Avoid or use alternate drug
```

Those are different concepts.

For example:

```json
{
  "source_severity": "serious",
  "effect": "increase_level_or_effect",
  "mechanism": "P-glycoprotein (MDR1) efflux transporter",
  "action": "avoid_or_use_alternate",
  "management_text": "..."
}
```

This will become valuable later if INSIGHT needs rules such as:

```text
show only Contraindicated/Serious interactions
```

or:

```text
show DDIs requiring glucose monitoring
```

or:

```text
find interactions caused by CYP3A4
```

---

# 7. Parsing strategy

I would **not start by giving all 100 files to an LLM**.

The sample is sufficiently structured to justify a deterministic parser.

The interaction area follows recognizable category headings such as:

```text
Contraindicated (0)
Serious (4)
Monitor Closely (92)
Minor (70)
```

These illustrative counts must be checked against the supplied monograph; they are not current ingestion results.

Use a parser implemented as a state machine.

```text
OUTSIDE_INTERACTIONS
        │
        │ find "Interactions"
        ▼
INSIDE_INTERACTIONS
        │
        ├── Contraindicated (N)
        │
        ├── Serious (N)
        │
        ├── Monitor Closely (N)
        │
        └── Minor (N)
                  │
                  ▼
             ENTRY_NAME
                  │
                  ▼
            ENTRY_CONTENT
                  │
                  ▼
             NEXT ENTRY
```

### Preprocessing

Before parsing:

```text
remove page headers
remove timestamps
remove repeated website URLs
join wrapped lines
normalize whitespace
retain original line offsets
```

But keep the original document untouched for provenance.

---

# 8. Use the source counts as automatic integrity checks

This is one of the strongest characteristics of these documents.

If a document says:

```text
Serious (4)
```

your parser should obtain exactly:

```text
4 serious entries
```

If it obtains 3 or 5:

```text
INGESTION FAILED
```

Do not silently continue.

Similarly:

```text
Monitor Closely (92)
```

should produce the corresponding number of parsed entries in that category.

That gives you a built-in validation system.

For each monograph generate:

```text
Sitagliptin.txt

Contraindicated:
expected: 0
parsed:   0
PASS

Serious:
expected: 4
parsed:   4
PASS

Monitor Closely:
expected: 92
parsed:   92
PASS

Minor:
expected: 70
parsed:   70
PASS
```

This is much safer than trying to judge parsing quality subjectively.

---

# 9. Drug normalization is probably the hardest part

Suppose one file says:

```text
quetiapine
```

another says:

```text
quetiapine fumarate
```

and the application receives:

```text
Seroquel
```

A string lookup would fail.

You therefore need a normalization layer:

```text
Input name
    ↓
lowercase / whitespace normalization
    ↓
exact canonical match?
    ↓
alias match?
    ↓
external terminology mapping if available
    ↓
manual resolution queue
```

For the first version, maintain a controlled alias table.

Later, you could cross-reference a terminology system such as RxNorm while retaining your own internal `drug_id`.

Your application should **never silently fuzzy-match a medication name**.

For example:

```text
clozapine
clonazepam
clomipramine
```

must not be resolved through uncontrolled approximate matching.

If normalization fails:

```text
MEDICATION_NOT_RECOGNIZED
```

is preferable to guessing.

---

# 10. Canonical pair keys

Internally create an order-independent pair.

For example:

```text
drug ID 14 = sitagliptin
drug ID 87 = aripiprazole
```

Generate:

```text
pair_key = 14:87
```

irrespective of whether the caller sends:

```text
[sitagliptin, aripiprazole]
```

or:

```text
[aripiprazole, sitagliptin]
```

A simple implementation is:

```python
pair_key = f"{min(a, b)}:{max(a, b)}"
```

This prevents duplicate pair identities while keeping the underlying evidence directional.

---

# 11. Do not force one record per pair

Instead:

```text
PAIR
sitagliptin ↔ ofloxacin

Evidence #1
severity = monitor_closely
...

Evidence #2
severity = minor
...
```

Then derive a summary.

```text
pair_summary
------------
pair_key
highest_severity
evidence_count
has_severity_conflict
has_multiple_mechanisms
last_reviewed_at
```

For UI purposes you could display:

```text
OFLOXACIN + SITAGLIPTIN

Severity: Monitor Closely

2 interaction statements found

• Potential dysglycemia ...
• Pharmacodynamic synergism ...

Source: ...
```

The important point is that **the source material remains intact**.

---

# 12. Runtime checking algorithm

If a patient has:

```text
A
B
C
D
```

generate:

```text
A-B
A-C
A-D
B-C
B-D
C-D
```

The number of comparisons is:

```text
n(n-1)/2
```

Even with 30 medications:

```text
30 × 29 / 2 = 435
```

which is trivial for a relational database.

No special scaling architecture is required.

Pseudo-code:

```python
def check_interactions(catalog_medications, dataset_version):
    drugs = validate_and_deduplicate_catalog_ids(catalog_medications)
    pairs = generate_unique_pairs([d.id for d in drugs])
    evidence, coverage = find_pair_evidence_and_coverage(pairs, dataset_version)
    return build_report(drugs, pairs, evidence, coverage, dataset_version)
```

---

# 13. Recommended API

A single endpoint is enough initially.

```http
POST /api/v1/ddi/check
```

Request:

```json
{
  "medications": [
    {"catalog_drug_id": "catalog_001"},
    {"catalog_drug_id": "catalog_002"},
    {"catalog_drug_id": "catalog_003"}
  ],
  "dataset_version": "<released-version>"
}
```

Response:

```json
{
  "dataset_version": "<released-version>",
  "catalog_version": "<catalog-version>",
  "medication_fingerprint": "<saved-medication-hash>",
  "resolved_medications": [
    {
      "catalog_drug_id": "catalog_001",
      "canonical_name": "sitagliptin",
      "drug_id": "drug_001"
    }
  ],

  "coverage_unavailable_medications": [],

  "pairs": [
    {
      "drug_a": {
        "id": "drug_001",
        "name": "sitagliptin"
      },
      "drug_b": {
        "id": "drug_047",
        "name": "aripiprazole"
      },

      "severity": "monitor_closely",
      "status": "interaction_found",

      "evidence": [
        {
          "effect": "...",
          "mechanism": "...",
          "management": "...",
          "source_document": "Sitagliptin.txt"
        }
      ]
    }
  ]
}
```

This illustrative response omits other pairs for brevity; the real report includes each pair's coverage status, evidence source/span/hash, raw text, conflicts and limitations. Expose verified stored descriptions, rather than generated clinical prose. Noncatalog input is rejected; an uncovered valid catalog entry remains selectable with “coverage unavailable”.

---

# 14. Storage choice

For your current scale, I would use:

> **Use the application's authoritative relational database; the detailed design proposes PostgreSQL. Bundle/import the local DDI release there rather than adding a separate runtime database.**

You do **not** need:

- a vector database;
- Elasticsearch;
- Neo4j;
- a dedicated graph database;
- a large language model;
- Redis;
- Kafka.

A DDI network looks like a graph conceptually, but relational SQL handles this workload perfectly well.

If your app already has PostgreSQL, avoid introducing another database.

---

# 15. What should happen when both monographs exist?

Eventually you may have:

```text
Sitagliptin.txt

contains:
sitagliptin ↔ aripiprazole
```

and:

```text
Aripiprazole.txt

contains:
aripiprazole ↔ sitagliptin
```

Do **not** discard either record.

Store both as evidence:

```text
PAIR sitagliptin ↔ aripiprazole
    ├── evidence from Sitagliptin.txt
    └── evidence from Aripiprazole.txt
```

If both agree, confidence increases.

If they differ:

```text
has_conflict = true
```

and route the pair to review.

Do not have code arbitrarily overwrite the first record with the second.

---

# 16. Validation system

Because this is medical data, I would build validation into the ingestion process from day one.

The checks that matter most are:

1. **Category-count validation**
   - `Serious (4)` → exactly four parsed entries.

2. **Unknown entity validation**
   - every interacting entity must resolve to a canonical concept.

3. **Duplicate detection**
   - same pair + same statement should not be imported repeatedly.

4. **Conflict detection**
   - same pair appears with different severity or management.

5. **Source traceability**
   - every production DDI must point back to document + original text.

6. **Symmetry test**
   - `check(A,B)` and `check(B,A)` return the same pair.

7. **Parser regression tests**
   - keep representative monographs as fixtures.

8. **Manual review**
   - all `Contraindicated`;
   - all `Serious`;
   - all parser anomalies;
   - all severity conflicts;
   - all unresolved drug names.

For your ~100-document dataset, this is entirely practical.

---

# 17. Preserve the original interaction description

Do not over-normalize the clinical information.

For example, source descriptions can distinguish substrate mechanisms from clinical effects and management. Verify inherited clinical examples against actual source spans rather than relying on uncitable reference placeholders.

You can extract normalized concepts such as:

```text
mechanism_type = P_GP
```

but also retain:

```text
raw_text
```

because your normalized schema will inevitably fail to represent some nuance.

Think:

```text
structured fields = queryable representation
raw source = clinical provenance
```

not:

```text
structured fields = replacement for source
```

---

# 18. Suggested severity model

Internally:

```text
CONTRAINDICATED
SERIOUS
MONITOR_CLOSELY
MINOR
UNKNOWN
```

For sorting only, you can assign:

```text
CONTRAINDICATED   40
SERIOUS           30
MONITOR_CLOSELY   20
MINOR             10
UNKNOWN             0
```

Those numbers should **never appear clinically**. They are only sort priorities.

Also keep separate:

```text
source_severity
```

and:

```text
management_action
```

because the prose may contain a stronger recommendation than the category heading.

---

# 19. The ingestion tool should be its own program

I would make something like:

```text
ddi/
├── ingestion/
│   ├── preprocess.py
│   ├── parser.py
│   ├── normalizer.py
│   ├── validator.py
│   └── importer.py
│
├── domain/
│   ├── drug.py
│   ├── interaction.py
│   └── severity.py
│
├── repository/
│   ├── drug_repository.py
│   └── interaction_repository.py
│
├── service/
│   └── ddi_checker.py
│
├── api/
│   └── ddi_routes.py
│
└── tests/
    ├── fixtures/
    │   └── Sitagliptin.txt
    ├── test_parser.py
    ├── test_normalizer.py
    └── test_ddi_checker.py
```

This is substantially better than embedding the parsing code inside your web application.

---

# 20. Best vibecoding workflow

Do **not** prompt an agent:

> "Build me a drug interaction system from these 100 files."

That is too large a unit of work and will produce architectural drift.

Build it incrementally:

```text
Phase 1
Define schemas and severity enums.

Phase 2
Make Sitagliptin.txt a test fixture.

Phase 3
Implement preprocessing.

Phase 4
Parse category headings and validate counts.

Phase 5
Parse individual interaction entries.

Phase 6
Implement canonical drug/alias handling.

Phase 7
Generate interaction_evidence records.

Phase 8
Run parser against all ~100 documents.

Phase 9
Produce anomaly/conflict report.

Phase 10
Manually review high-risk records.

Phase 11
Build deterministic DDI lookup service.

Phase 12
Build API.

Phase 13
Build UI.

Phase 14
Integrate it into the main application.
```

The agent should not move to the next phase until tests for the previous phase pass.

---

# 21. One particularly useful artifact: ingestion report

Every import should produce something like:

```text
DDI Knowledge Base Import
=========================

Documents discovered:       103
Documents processed:        103
Documents passed:            99
Documents requiring review:   4

Interaction records:       8,432
Unique drug pairs:         6,977

Contraindicated:              94
Serious:                     581
Monitor Closely:           4,721
Minor:                     3,036

Unknown entities:             12
Severity conflicts:           18
Duplicate evidence:           37

Parser version:
ddi-parser/1.0.0

Dataset version:
2026-09-20
```

This gives you an auditable build process instead of an opaque collection of generated JSON.

---

# 22. What I would explicitly avoid

| Approach | Recommendation |
|---|---|
| LLM determines DDI at runtime | **Avoid** |
| RAG/vector search through monographs | **Avoid for interaction determination** |
| Read `.txt` files on every request | **Avoid** |
| One interaction row per pair | **Avoid** |
| Exact string matching only | **Avoid** |
| Fuzzy drug matching | **Avoid** |
| Structured KB in the application's authoritative database | **Use; PostgreSQL is proposed** |
| Deterministic parser | **Use** |
| Preserve raw evidence | **Use** |
| Versioned imports | **Use** |
| Human review of high-risk anomalies | **Use** |

---

# 23. Final architecture

The architecture I recommend is:

```text
                    SOURCE LAYER
              Medication text monographs
                        │
                        ▼
                OFFLINE INGESTION MODULE
        ┌───────────────────────────────┐
        │ Preprocessor                  │
        │ Interaction parser            │
        │ Drug normalizer               │
        │ Validation engine             │
        │ Conflict detector             │
        └──────────────┬────────────────┘
                       │
                       ▼
          DDI TABLES IN APPLICATION DATABASE
        ┌───────────────────────────────┐
        │ Drug                          │
        │ DrugAlias                     │
        │ SourceDocument                │
        │ InteractionEvidence           │
        │ PairSummary                   │
        │ PairCoverage                  │
        │ ImportRun                     │
        └──────────────┬────────────────┘
                       │
                       ▼
                  DDI SERVICE
        ┌───────────────────────────────┐
        │ Medication resolver           │
        │ Pair generator                │
        │ Interaction lookup            │
        │ Evidence aggregator           │
        └──────────────┬────────────────┘
                       │
                       ▼
                     API
                       │
                       ▼
                  X-INSIGHT UI
```

## The key design principle

**Your text documents should be treated as source material used to build the knowledge base—not as the knowledge base itself.**

That makes the module deterministic, testable, auditable, fast, and much easier to safely vibecode.

For your current scale of roughly 100 medication monographs, I would **not introduce microservices**. Keep the ingestion pipeline as a separate CLI/package, store the resulting data in the application's existing relational database, and expose the runtime checker as a normal application service/module. If the corpus eventually grows to tens of thousands of drugs, multiple external data sources, or continuously updated interaction feeds, the ingestion and terminology components are the first pieces I would reconsider.
