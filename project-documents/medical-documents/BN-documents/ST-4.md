# STATEMENT 4: Pharmacotherapy

## Clinical Phrases

1. Patients with schizophrenia must be treated with an antipsychotic medication and monitored for effectiveness and side effects.

2. No evidence-based ranking of FGAs vs SGAs and no algorithmic selection is possible.

3. No reliable predictor of response/side effects for one agent vs another, except clozapine's special role.

4. Short-acting parenteral formulations of antipsychotic agents are available for short-term use in individuals who are unable to take oral medications or for emergency administration in acutely agitated patients.

5. A common cause of drug-drug interactions relates to interactions at metabolic enzymes such as cytochrome P450 (CYP) enzymes, UDP-glucuronosyltransferases, and flavin-containing monooxygenases.

6. Medications may compete with each other for the same CYP enzyme, or they may induce or inhibit the activity of CYP enzymes, altering levels of drugs that are metabolized through that route. 

7. Smoking tobacco or marijuana induces CYP1A2, resulting in a corresponding reduction of levels of drugs that are metabolized through that enzyme, including clozapine and olanzapine.

8. With cessation of smoking (either intentionally or with admission to a smoke-free facility), there will be corresponding increases in the levels of drugs metabolized via CYP1A2. These shifts in blood levels can be quite significant and contribute to shifts in medication effectiveness or toxicity.

9. An oral antipsychotic medication with a longer half-life or an LAI may be preferable for patients who are prone to forget doses or who are intermittently nonadherent to treatment.

10. If an antipsychotic medication (or active metabolite) half-life is significantly longer than 24 hours, it is important to be aware that steady state may not be reached for some time.

Chlorpromazine
Fluphenazine
Haloperidol
Loxapine
Molindone
Perphenazine
Pimozide
Thioridazine
Thiothixene
Trifluoperazine
Aripiprazole
Asenapine
Brexpiprazole
Cariprazine
Clozapine
Iloperidone
Lurasidone
Olanzapine
Paliperidone
Quetiapine
Risperidone
Ziprasidone


P(Chlorpromazine) = `` | P(Fluphenazine) = `` | P(Haloperidol) = `` | P(Loxapine) = `` | P(Molindone) = `` | P(Perphenazine) = ``| P(Pimozide) = `` | P(Thioridazine) = `` | P(Thiothixene) = `` | P(Trifluoperazine) = `` | P(Aripiprazole) = `` | P(Asenapine) = `` | P(Brexpiprazole) = `` | P(Cariprazine) = `` | P(Clozapine) = `` | P(Iloperidone) = `` | P(Lurasidone) = `` | P(Olanzapine) = `` | P(Paliperidone) = `` | P(Quetiapine) = `` | P(Risperidone) = `` | P(Ziprasidone) = `` 

**BN-1, Q-1:**Is patient a tabacco smoker?
[The answer to this question (Yes, No, Some degree of changes but not fully improved, Unknown) should be obtained from the user inputs.] 
[The default answer to this question is `Unknown`.]
    - If `Yes` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `0` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `0` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`
    - If `No` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`
    - If `Unknown` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`



**BN-1, Q-2:**Is patient a marijuana smoker?
[The answer to this question (Yes, No, Some degree of changes but not fully improved, Unknown) should be obtained from the user inputs.] 
[The default answer to this question is `Unknown`.]
    - If `Yes` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `0` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `0` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`
    - If `No` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`
    - If `Unknown` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`



**BN-1, Q-3:**Does the patient have hepatic impairment?
[The answer to this question (Yes, No, Some degree of changes but not fully improved, Unknown) should be obtained from the user inputs.] 
[The default answer to this question is `Unknown`.]
    - If `Yes` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `0` | P(Haloperidol) = `++` | P(Loxapine) = `++` | P(Molindone) = `+` | P(Perphenazine) = `+` | P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `++` | P(Trifluoperazine) = `0` | P(Aripiprazole) = `++` | P(Asenapine) = `0` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`
    - If `No` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Perphenazine) = `+`| P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`
    - If `Unknown` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Perphenazine) = `+`| P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`

**BN-1, Q-4:**Does the patient have renal impairment?
[The answer to this question (Yes, No, Some degree of changes but not fully improved, Unknown) should be obtained from the user inputs.] 
[The default answer to this question is `Unknown`.]
    - If `Yes` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `++` | P(Loxapine) = `++` | P(Molindone) = `++` | P(Perphenazine) = `+`| P(Pimozide) = `+` | P(Thioridazine) = `++` | P(Thiothixene) = `++` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `0` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `0` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`
    - If `No` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Perphenazine) = `+`| P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`
    - If `Unknown` then P(Chlorpromazine) = `+` | P(Fluphenazine) = `+` | P(Haloperidol) = `+` | P(Loxapine) = `+` | P(Molindone) = `+` | P(Perphenazine) = `+`| P(Pimozide) = `+` | P(Thioridazine) = `+` | P(Thiothixene) = `+` | P(Trifluoperazine) = `+` | P(Aripiprazole) = `+` | P(Asenapine) = `+` | P(Brexpiprazole) = `+` | P(Cariprazine) = `+` | P(Clozapine) = `+` | P(Iloperidone) = `+` | P(Lurasidone) = `+` | P(Olanzapine) = `+` | P(Paliperidone) = `+` | P(Quetiapine) = `+` | P(Risperidone) = `+` | P(Ziprasidone) = `+`