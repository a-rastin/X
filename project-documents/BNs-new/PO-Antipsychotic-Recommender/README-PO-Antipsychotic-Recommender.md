# BN-01 Bayesian Network: Variables, Priors, and Ranking Probabilities

**Model:** `BN-PO-Antipsychotic-Recommender.xml`

**Model version:** 1.1

**Documentation date:** November 5, 2026

**Format:** XMLBIF 0.3, using the supplied [schema.xml](../schema.xml) authoring profile.

## 1. Purpose and interpretation

BN-01 ranks 22 candidate antipsychotic medications using 16 binary patient factors and a user-supplied table of ordinal appropriateness weights. It combines the weights for a patient's profile and expresses the result as a normalized categorical probability distribution over medications.

Three types of numbers must be distinguished:

| Quantity | Meaning | Example |
|---|---|---|
| Patient-factor prior | The default probability assigned to a factor before it is observed. | `P(Smoking = Yes) = 0.57`. |
| Drug weight | The ordinal contribution of a factor answered `Yes` to a medication's score. | A weight of 3 is three times a weight of 1 under that factor, before normalization. |
| Target probability | A medication's share of the total ranking score, or the prior-weighted average of that share when some factors are unknown. | Approximately 7.0916% for Aripiprazole when smoking is observed and all other factors are unknown. |

The drug weights and patient-factor priors are supplied modeling parameters. The target probabilities describe the ranking model; they are not measured probabilities of treatment success, safety, adverse events, or actual prescribing. The model has not undergone clinical validation. This guide documents its implemented behavior without independently verifying the clinical evidence behind the supplied parameters.

## 2. Patient variables and their prior percentages

Each patient variable has exactly two outcomes, declared in this order: **`No`, `Yes`**. In every root probability table, the first number is therefore `P(No)` and the second is `P(Yes)`.

The percentages below are extracted from the current model. `No` means a negative answer to the corresponding question. An unknown answer is left unobserved; it is not a third outcome and is not automatically recorded as `No`.

| Code | XML variable | Meaning of `Yes` in the original rule | P(Yes) | P(No) |
|---|---|---|---:|---:|
| F01 | `Smoking` | The patient currently smokes tobacco. | 57% | 43% |
| F02 | `Marijuana_Smoking` | The patient currently smokes marijuana. | 33.7% | 66.3% |
| F03 | `Hepatic_Failure` | The patient has hepatic failure. | 1.09% | 98.91% |
| F04 | `Renal_Failure` | The patient has renal failure. | 2% | 98% |
| F05 | `Sedative_Effects_Concern` | Sedative effects are a major concern for this patient. | 1% | 99% |
| F06 | `Seizure_Risk` | The patient has an increased risk of seizure. | 1% | 99% |
| F07 | `Glucose_Abnormalities` | The patient has glucose abnormalities. | 33.5% | 66.5% |
| F08 | `Hyperlipidemia` | The patient has hyperlipidemia. | 20% | 80% |
| F09 | `Weight_Gain_Risk` | The patient has an increased risk of weight gain. | 22% | 78% |
| F10 | `QTc_Prolongation_Risk` | The patient is at risk of QT/QTc prolongation. | 1% | 99% |
| F11 | `Orthostatic_Hypotension_Risk` | The patient has an increased risk of orthostatic hypotension. | 1% | 99% |
| F12 | `Hyperprolactinemia` | The patient has hyperprolactinemia. | 1% | 99% |
| F13 | `Tardive_Dyskinesia` | The patient has tardive dyskinesia. | 1% | 99% |
| F14 | `Dystonia` | The patient has dystonia. | 1% | 99% |
| F15 | `Parkinsonism` | The patient has parkinsonism. | 1% | 99% |
| F16 | `Akathisia` | The patient has akathisia. | 1% | 99% |

For example, hepatic failure has the root table `0.9891 0.0109`: 98.91% `No` and 1.09% `Yes`. Smoking has `0.43 0.57`: 43% `No` and 57% `Yes`. Each pair sums to 100%.

### 2.1. How the supplied labels map to the existing variables

The model retains the original rule questions and drug weights. Some of the labels used when supplying the updated priors describe a different or narrower concept:

| Supplied prior label | Existing XML variable | Interpretation in version 1.1 |
|---|---|---|
| Current tobacco use | `Smoking` | Assigned to the original tobacco-smoking question. |
| Current marijuana use | `Marijuana_Smoking` | Assigned to the marijuana-smoking question. The earlier heading “marijuana addiction” does not introduce a separate dependence diagnosis. |
| Overweight: 22% | `Weight_Gain_Risk` | Used as a proxy prior for increased weight-gain risk. Being overweight and being at increased risk of future weight gain are distinct events. |
| Diabetes or prediabetes: 33.5% | `Glucose_Abnormalities` | Assigned to the broader glucose-abnormalities node. |
| Sedative effect risk: 1% | `Sedative_Effects_Concern` | Assigned to the original question about sedation being a major concern. It is not a drug-specific probability of sedation. |
| Risks of hyperprolactinemia, tardive dyskinesia, dystonia, parkinsonism, and akathisia: 1% each | The corresponding presence nodes | Assigned as requested to nodes whose original questions ask whether the condition is present. A risk state and an existing condition are different concepts; version 1.1 retains this mapping rather than introducing additional nodes. |
| QT prolongation risk | `QTc_Prolongation_Risk` | The supplied wording is interpreted as 1% `Yes` and 99% `No`. |

These mappings matter when entering observations or applying population estimates. The percentages are default marginal priors for the model's existing events, not individualized diagnoses or treatment-emergent event rates.

## 3. Network structure and medication target

The network contains **17 variables and 16 directed edges**:

- The 16 patient factors are root nodes with no parents.
- All 16 root nodes are parents of the categorical target `Antipsychotic`.
- `Antipsychotic` has 22 outcomes, one per medication. There are no separate binary drug nodes.
- All nodes use XMLBIF type `nature`. The file does not contain decision nodes, utility nodes, or an expected-utility treatment policy.

The 22 medications, in the target's declared outcome order, are:

Chlorpromazine, Fluphenazine, Haloperidol, Loxapine, Molindone, Perphenazine, Pimozide, Thioridazine, Thiothixene, Trifluoperazine, Aripiprazole, Asenapine, Brexpiprazole, Cariprazine, Clozapine, Iloperidone, Lurasidone, Olanzapine, Paliperidone, Quetiapine, Risperidone, Ziprasidone.

The directed edges express how the ranking calculation depends on patient factors. They do not assert that a patient characteristic causes a medication to be prescribed.

The root factors are assumed mutually independent. If $X=(X_1,\ldots,X_{16})$ denotes the patient factors and $D$ denotes the medication target, the joint model is:

$$
P(X,D)=P(D\mid X)\prod_{i=1}^{16}P(X_i).
$$

Consequently, observing smoking does not change the priors of the other patient factors in the intended query. Correlations between patient factors are not encoded.

## 4. Ordinal drug weights

The original symbols are translated into numbers as follows:

| Source symbol | Numeric weight | Implemented meaning when that factor is `Yes` |
|---|---:|---|
| `0` | 0 | Exclude this drug under the supplied rule system: its complete-profile score becomes zero. |
| `+` | 1 | Reference contribution. |
| `++` | 2 | Twice the reference contribution. |
| `+++` | 3 | Three times the reference contribution. |

The current matrix uses weights 0, 1, and 3. The value 2 is part of the agreed scale but does not occur in the supplied medication lists. A weight is not itself a probability and does not imply a percentage.

For **every factor answered `No`, every drug receives a neutral multiplier of 1**, regardless of its `Yes` weight. For every factor answered `Yes`, the appropriate weight below is used. A zero is an exclusion specified by this model's supplied rules; it does not establish a universally applicable clinical contraindication.

Perphenazine was absent from the two original smoking lists. Its weight for both tobacco and marijuana smoking was completed as **1 (`+`)** with the user's confirmation.

### 4.1. Weights for factors F01–F08

The factor codes refer to the variable table in Section 2. Every entry is the weight applied for `Yes`.

| Medication | F01 | F02 | F03 | F04 | F05 | F06 | F07 | F08 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Chlorpromazine | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 3 |
| Fluphenazine | 1 | 1 | 0 | 1 | 3 | 3 | 3 | 3 |
| Haloperidol | 1 | 1 | 3 | 3 | 3 | 3 | 3 | 3 |
| Loxapine | 1 | 1 | 3 | 3 | 1 | 3 | 3 | 3 |
| Molindone | 1 | 1 | 1 | 3 | 1 | 3 | 3 | 3 |
| Perphenazine | 1 | 1 | 1 | 1 | 1 | 3 | 3 | 3 |
| Pimozide | 1 | 1 | 1 | 1 | 3 | 0 | 3 | 3 |
| Thioridazine | 1 | 1 | 1 | 3 | 0 | 1 | 3 | 3 |
| Thiothixene | 1 | 1 | 3 | 3 | 3 | 0 | 3 | 3 |
| Trifluoperazine | 1 | 1 | 0 | 3 | 3 | 3 | 3 | 3 |
| Aripiprazole | 1 | 1 | 3 | 3 | 3 | 3 | 3 | 3 |
| Asenapine | 1 | 1 | 0 | 3 | 1 | 3 | 1 | 1 |
| Brexpiprazole | 1 | 1 | 1 | 1 | 1 | 3 | 3 | 1 |
| Cariprazine | 1 | 1 | 1 | 1 | 1 | 3 | 3 | 3 |
| Clozapine | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 0 |
| Iloperidone | 1 | 1 | 1 | 1 | 1 | 3 | 1 | 3 |
| Lurasidone | 1 | 1 | 1 | 1 | 1 | 3 | 1 | 1 |
| Olanzapine | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 |
| Paliperidone | 1 | 1 | 1 | 1 | 3 | 3 | 3 | 1 |
| Quetiapine | 1 | 1 | 1 | 3 | 0 | 1 | 1 | 0 |
| Risperidone | 1 | 1 | 1 | 1 | 1 | 3 | 1 | 3 |
| Ziprasidone | 1 | 1 | 1 | 3 | 1 | 3 | 3 | 3 |

### 4.2. Weights for factors F09–F16

| Medication | F09 | F10 | F11 | F12 | F13 | F14 | F15 | F16 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Chlorpromazine | 1 | 0 | 0 | 3 | 0 | 1 | 1 | 1 |
| Fluphenazine | 1 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| Haloperidol | 1 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| Loxapine | 3 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Molindone | 3 | 1 | 3 | 1 | 1 | 1 | 1 | 1 |
| Perphenazine | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Pimozide | 3 | 0 | 3 | 0 | 0 | 1 | 0 | 0 |
| Thioridazine | 1 | 0 | 0 | 1 | 3 | 3 | 3 | 3 |
| Thiothixene | 3 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| Trifluoperazine | 1 | 1 | 3 | 1 | 1 | 1 | 1 | 1 |
| Aripiprazole | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 1 |
| Asenapine | 1 | 1 | 1 | 1 | 1 | 1 | 3 | 1 |
| Brexpiprazole | 3 | 1 | 3 | 3 | 3 | 3 | 3 | 1 |
| Cariprazine | 1 | 1 | 3 | 3 | 3 | 3 | 3 | 1 |
| Clozapine | 0 | 1 | 0 | 3 | 3 | 3 | 3 | 3 |
| Iloperidone | 1 | 0 | 0 | 1 | 3 | 3 | 3 | 3 |
| Lurasidone | 3 | 3 | 3 | 3 | 1 | 1 | 1 | 1 |
| Olanzapine | 0 | 1 | 1 | 1 | 3 | 3 | 1 | 1 |
| Paliperidone | 1 | 1 | 1 | 0 | 1 | 1 | 1 | 1 |
| Quetiapine | 1 | 1 | 1 | 3 | 3 | 3 | 3 | 3 |
| Risperidone | 1 | 1 | 1 | 0 | 1 | 1 | 1 | 1 |
| Ziprasidone | 3 | 0 | 1 | 1 | 3 | 3 | 3 | 1 |

## 5. How complete-profile probabilities are calculated

For medication $d$ and a fully observed patient profile $x$, let $w_i(d)$ be the drug weight for factor $i$ when that factor is `Yes`. Its contribution is:

$$
g_i(d,x_i)=
\begin{cases}
w_i(d), & x_i=\mathrm{Yes},\\
1, & x_i=\mathrm{No}.
\end{cases}
$$

The combined score is the product of the 16 contributions:

$$
s_d(x)=\prod_{i=1}^{16}g_i(d,x_i).
$$

The target conditional probability is obtained by dividing by the sum of scores across all 22 medications:

$$
P(D=d\mid X=x)=\frac{s_d(x)}{\sum_{c=1}^{22}s_c(x)}.
$$

This construction gives each complete profile a probability row that sums to 1. For example, three active factors with weights 3, 1, and 3 give a score of $3\times1\times3=9$; changing any active weight to 0 makes that drug's score 0.

No smoothing is applied. Aripiprazole has a positive weight for every factor in this particular matrix, so at least one drug has a positive score for every complete profile. The denominator is therefore never zero in the delivered model.

When all factors are observed, the root priors do not affect the conditional ranking. They affect results when one or more factors remain unknown.

## 6. Known, negative, and unknown answers

| Input status | Evidence entered | Effect on inference |
|---|---|---|
| Known positive | Set the factor to `Yes`. | Use its `Yes` weight; the factor is fixed to `Yes` in this query. |
| Known negative | Set the factor to `No`. | Use a neutral multiplier of 1. |
| Unknown | Enter no evidence for that factor. | Average over its `No` and `Yes` states using its prior. |

For example, if smoking is known to be present, the query uses `Smoking = Yes`; its 57% prior is not used as a partial smoking observation. An unknown hepatic-failure status is still averaged over 1.09% `Yes` and 98.91% `No`.

Let $e$ be the observed factors, $U$ the set of unknown factors, and $u$ a configuration of those unknown factors. The exact target posterior is:

$$
P(D=d\mid e)=\sum_u P(D=d\mid e,u)\prod_{i\in U}P(X_i=u_i).
$$

Each complete-profile distribution is normalized first and then averaged with its configuration's prior probability. In general, this is not equivalent to multiplying the mean weights and normalizing once. A calculation that treats all unknown factors as `No` would also produce a different result.

Under independent priors, the conditional distribution of each unobserved root remains its specified prior when only other root factors are observed. The intended workflow queries the medication target without entering it as evidence.

## 7. Worked examples from the current network

### 7.1. Every factor is observed as `No`

All 22 medications have score 1. Each therefore receives:

$$
P(D=d\mid X_1=\mathrm{No},\ldots,X_{16}=\mathrm{No})=\frac{1}{22}\approx4.5455\%.
$$

This is a fully negative profile, not a profile with no available information.

### 7.2. Smoking is `Yes` and all other factors are observed as `No`

The smoking rule gives Clozapine and Olanzapine weight 0. Each of the other 20 medications has weight 1, including Perphenazine. The result is **0% for Clozapine and Olanzapine and 5% for each other medication**.

### 7.3. Smoking is `Yes` and all other factors are unknown

Only `Smoking = Yes` is entered as evidence. The remaining 15 factors have $2^{15}=32,768$ possible configurations. Each configuration is weighted by the product of its corresponding root probabilities. These configurations are not equally likely under the updated priors.

The following results were computed directly from version 1.1. The comparison column shows the target marginal when all 16 factors are unknown, averaged over $2^{16}=65,536$ configurations.

| Rank with smoking observed | Medication | All factors unknown | Smoking = Yes; other 15 unknown |
|---:|---|---:|---:|
| 1 | Aripiprazole | 7.0328% | 7.0916% |
| 2 | Ziprasidone | 6.4615% | 6.5163% |
| 3 | Loxapine | 6.3778% | 6.4307% |
| 4 | Molindone | 6.3416% | 6.3938% |
| 5 | Thiothixene | 6.1941% | 6.2440% |
| 6 | Pimozide | 5.9580% | 6.0069% |
| 7 | Brexpiprazole | 5.5503% | 5.6067% |
| 8 | Cariprazine | 5.1887% | 5.2452% |
| 9 | Thioridazine | 5.0959% | 5.1521% |
| 10 | Trifluoperazine | 5.0272% | 5.0791% |
| 11 | Haloperidol | 4.9566% | 5.0065% |
| 12 | Perphenazine | 4.8594% | 4.9111% |
| 13 | Fluphenazine | 4.7436% | 4.7922% |
| 14 | Paliperidone | 4.1224% | 4.1736% |
| 15 | Lurasidone | 4.0512% | 4.1054% |
| 16 | Iloperidone | 3.7659% | 3.8219% |
| 17 | Risperidone | 3.5826% | 3.6338% |
| 18 | Chlorpromazine | 3.5331% | 3.5846% |
| 19 | Asenapine | 3.1165% | 3.1690% |
| 20 | Quetiapine | 2.9775% | 3.0356% |
| 21 (tie) | Clozapine | 0.5432% | 0.0000% |
| 21 (tie) | Olanzapine | 0.5201% | 0.0000% |

Percentages are rounded to four decimal places, so displayed columns may not sum to exactly 100%. Exact distributions sum to 1 within numerical tolerance. Clozapine and Olanzapine are tied at zero in the smoking-observed scenario. Their probabilities are positive when all factors are unknown because the prior also allows configurations with neither smoking factor present and without another applicable exclusion.

The leading share for Aripiprazole in the smoking-observed scenario is approximately **7.0916%**. This is its share in this ranking model, not a 7.0916% probability of clinical appropriateness or treatment response.

## 8. XML probability tables and ordering

The network name is `BN_01_Antipsychotic_Appropriateness`. Each root has a two-entry `TABLE` with `No` first and `Yes` second. For example:

```xml
<DEFINITION>
  <FOR>Smoking</FOR>
  <TABLE>0.43 0.57</TABLE>
</DEFINITION>
```

This simplified excerpt omits the metadata properties present in the actual file.

The target definition lists its 16 `GIVEN` parents in the order F01 through F16 in Section 2. Its conditional probability table contains:

- **65,536 parent configurations:** $2^{16}$.
- **22 probabilities per configuration:** one per medication outcome.
- **1,441,792 target entries:** $65,536\times22$.

There are also 32 root-table entries, giving 1,441,824 probability entries across the complete network.

Within the target `TABLE`, the target outcome changes fastest, then the last parent (`Akathisia`), then earlier parents in reverse order. Parent states follow `No`, then `Yes`. The first target row represents all factors `No`; the final row represents all factors `Yes`. Root factor F01 (`Smoking`) changes slowest. Whitespace and line breaks separate numeric entries without adding extra semantics.

`PROPERTY` elements store labels, source descriptions, prior values, and drug weights as metadata. Standard inference uses the `TABLE` entries. Editing a recorded weight or prior in a `PROPERTY` alone does not update the executable probabilities. A changed root prior must be reflected in its root `TABLE`; changed drug weights require regeneration of the affected target CPT.

## 9. Validation and scope

The delivered model was validated against the supplied schema and checked for complete nature-node definitions, matching outcome/table dimensions, an acyclic graph, valid probability ranges, and normalized rows. All 16 updated root priors were verified, and all 65,536 target rows passed normalization checks using a tolerance of $10^{-12}$. Partial-evidence inference and the exact smoking-related zero exclusions were also checked.

The XSD validates document structure. Completeness, acyclicity, probability normalization, and the meaning of the variables require additional checks beyond the XSD. Mathematical and format validation do not constitute clinical validation.

The model's practical limits follow from its construction:

- The ordinal weights are user-supplied appropriateness rules, not estimated clinical likelihoods.
- The priors are the user-specified marginal percentages. The update did not supply supporting publications, a common reference population, severity definitions, or joint prevalence estimates.
- Independence is a modeling assumption; no correlations between patient factors are represented.
- The proxy and risk-versus-presence mappings in Section 2.1 remain part of version 1.1.
- No dose, formulation, treatment history, efficacy model, or utility function is encoded. The output is a ranking within the 22-drug candidate set.

## 10. Provenance

| Component | Source |
|---|---|
| Variable questions and drug weights | User-supplied `Pasted text(4).txt`. |
| Weight mapping, multiplicative combination, and Perphenazine completion | User-confirmed modeling choices in the conversation. |
| Updated patient-factor priors | User-supplied percentages dated November 5, 2026. |
| XML validation profile | User-supplied `schema.xml`. |
| Worked numerical results | Exact marginalization of the current `BN-PO-Antipsychotic-Recommender.xml` tables. |

The original version 1.0 used independent 50/50 root priors. Version 1.1 replaces those priors with the percentages in this guide, while retaining the original target CPT and ordinal rules.

**SHA-256 of the model documented here:**

```text
f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a
```
