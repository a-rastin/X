# Bayesian Network 3 (based on STATEMENT 6: Continuing the Same Medications)

## Clinical Phrases

1. Symptoms have improved with an antipsychotic medication => Continue to be treated with the same antipsychotic medication.

2. Once symptoms have improved => Antipsychotic medication to be maintained.

3. Circumstances it may be necessary to consider a change from one antipsychotic medication to another =>
    - Some degree of response to initial treatment but may still have significant symptoms or difficulties in functioning.
    - Initiate treatment with an LAI antipsychotic if the current oral medication is unavailable in an LAI formulation.
    - Patient preferences
    - Medication availability
    - Side effects
    - Long-term health risks of metabolic syndrome and obesity, weight gain and development of diabetes or metabolic syndrome.

4. No need to waite before discontinuing the first antipsychotic agent after starting the second antipsychotic.

---

## When should this bayesian network activates? 

1. In the first visit (primary evaluation) of a known schizophrenia patient who has been taking antipsychotics.

2. In follow up sessions of a previously registered patient.

---

## Clinical Routing

```
>  Acronyms: `BN=Bayesian Network`, `S=Scenario`, `Q=Question`, `SQ=Subquestion`.
>  Legend used to describe probabilities: `++++=very strong (Ki<1 nM)`, `+++=strong (1–10 nM)`, `++=moderate (10–100 nM)`, `+=weak (100–1,000 nM)`, `0=very weak/negligible (Ki≥1,000 nM)`.

```

### Scenario 1 (First visit (primary evaluation) of a known schizophrenia patient who has been taking antipsychotics)

**BN-3, S-1, Q-1:** Did the symptoms have improved with `X` antipsychotic medication? 
[The answer to this question (Yes, No, Some degree of changes but not fully improved, Unknown) should be obtained from the user inputs.] 
[The default answer to this question is `Unknown`.]
    - If `Yes` then P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`
    - If `No` then  P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
    - If `Some degree of changes but not fully improved` then  P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
    - If `Unknown` then P(Continue the same antipsychotic) = `+++` | P(Stop current antipsychotic and switch to another one) = `+`



**BN-3, S-1, Q-2:** Clinition decides to switch from oral to LAI antipsychotics? 
[The answer to this question (Yes, No) should be obtained from the user inputs.]
[The default answer to this question is `No`.]
    - If `Yes` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
    - If `No` then  P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`



**BN-3, S-1, Q-3:** Does the patient prefer to take another antipsychotic for any reason? 
[The answer to this question (Yes, No) should be obtained from the user inputs.]
[The default answer to this question is `No`.]
    - If `Yes` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
    - If `No` then  P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`



**BN-3, S-1, Q-4:** Is there any doubt in sustainable medication availability?
[The answer to this question (Yes, No) should be obtained from the user inputs.]
[The default answer to this question is `No`.]
    - If `No` then  P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`
    - If `Yes` then ask **DA-3, S-1, Q-4, SQ-1:** Level of the doubt?
                        [This is a conditional question and should only activate if the answer to the `DA-3, S-1, Q-4` is `YES`]
                        [The answer to this question (The drug is not accessible, It is hard to obtain the drug, Unkown) should be obtained from the user inputs.]
                        [The default answer to this question is `Unknown`.]
                            - If `The drug is not accessible` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
                            - If `It is hard to obtain the drug` then P(Continue the same antipsychotic) = `+` | P(Stop current antipsychotic and switch to another one) = `+++`
                            - If `Unkown` then P(Continue the same antipsychotic) = `+` | P(Stop current antipsychotic and switch to another one) = `+++`

 

**BN-3, S-1, Q-5:** Is there any long-term health risk of current antipsychotic medication?
[The answer to this question (Yes, No) should be obtained from the user inputs.]
[The default answer to this question is `No`.]
    - If `No` then  P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`
    - If `Yes` then ask **DA-3, S-1, Q-5, SQ-1:** What type of long-term health risk?
                        [This is a conditional question and should only activate if the answer to the `DA-3, S-1, Q-5` is `YES`]
                        [The answer to this question (Metabolic syndrome, Obesity, Weight gain, Development of diabetes, Unkown) should be obtained from the user inputs.]
                        [The default answer to this question is `Unknown`.]
                            - If `Metabolic syndrome` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
                            - If `Obesity` then P(Continue the same antipsychotic) = `+` | P(Stop current antipsychotic and switch to another one) = `+++`
                            - If `Weight gain` then P(Continue the same antipsychotic) = `++` | P(Stop current antipsychotic and switch to another one) = `++`
                            - If `Development of diabetes` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
                            - If `Unkown` then P(Continue the same antipsychotic) = `+` | P(Stop current antipsychotic and switch to another one) = `+++`
                           

### Scenario 2 (Follow up sessions of a previously registered patient)

**BN-3, S-2, Q-1:** Did the symptoms have improved with the previous antipsychotic medication? 
[The answer to this question (Yes, No, Some degree of changes but not fully improved, Unknown) should be obtained from the user inputs.] 
[The default answer to this question is `Unknown`.]
    - If `Yes` then P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`
    - If `No` then  P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
    - If `Some degree of changes but not fully improved` then  P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
    - If `Unknown` then P(Continue the same antipsychotic) = `+++` | P(Stop current antipsychotic and switch to another one) = `+`



**BN-3, S-2, Q-2:** Clinition decides to switch from oral to LAI antipsychotics? 
[The answer to this question (Yes, No) should be obtained from the user inputs.]
[The default answer to this question is `No`.]
    - If `Yes` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
    - If `No` then  P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`



**BN-3, S-2, Q-3:** Does the patient prefer to take another antipsychotic for any reason? 
[The answer to this question (Yes, No) should be obtained from the user inputs.]
[The default answer to this question is `No`.]
    - If `Yes` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
    - If `No` then  P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`



**BN-3, S-2, Q-4:** Is there any doubt in sustainable medication availability?
[The answer to this question (Yes, No) should be obtained from the user inputs.]
[The default answer to this question is `No`.]
    - If `No` then  P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`
    - If `Yes` then ask **DA-3, S-2, Q-4, SQ-1:** Level of the doubt?
                        [This is a conditional question and should only activate if the answer to the `DA-3, S-2, Q-4` is `YES`]
                        [The answer to this question (The drug is not accessible, It is hard to obtain the drug, Unkown) should be obtained from the user inputs.]
                        [The default answer to this question is `Unknown`.]
                            - If `The drug is not accessible` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
                            - If `It is hard to obtain the drug` then P(Continue the same antipsychotic) = `+` | P(Stop current antipsychotic and switch to another one) = `+++`
                            - If `Unkown` then P(Continue the same antipsychotic) = `+` | P(Stop current antipsychotic and switch to another one) = `+++`



**BN-3, S-2, Q-5:** Is there any long-term health risk of current antipsychotic medication?
[The answer to this question (Yes, No) should be obtained from the user inputs.]
[The default answer to this question is `No`.]
    - If `No` then  P(Continue the same antipsychotic) = `++++` | P(Stop current antipsychotic and switch to another one) = `0`
    - If `Yes` then ask **DA-3, S-2, Q-5, SQ-1:** What type of long-term health risk?
                        [This is a conditional question and should only activate if the answer to the `DA-3, S-2, Q-5` is `YES`]
                        [The answer to this question (Metabolic syndrome, Obesity, Weight gain, Development of diabetes, Unkown) should be obtained from the user inputs.]
                        [The default answer to this question is `Unknown`.]
                            - If `Metabolic syndrome` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
                            - If `Obesity` then P(Continue the same antipsychotic) = `+` | P(Stop current antipsychotic and switch to another one) = `+++`
                            - If `Weight gain` then P(Continue the same antipsychotic) = `++` | P(Stop current antipsychotic and switch to another one) = `++`
                            - If `Development of diabetes` then P(Continue the same antipsychotic) = `0` | P(Stop current antipsychotic and switch to another one) = `++++`
                            - If `Unkown` then P(Continue the same antipsychotic) = `+` | P(Stop current antipsychotic and switch to another one) = `+++`
