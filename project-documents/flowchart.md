---
title: Patient Routing graph
---
flowchart TD
    subgraph Session Type
        A(Primary evaluation of a new case)
        B(Primary evaluation of a known case) 
        C(follow up a registered patient)                          
    end

    subgraph BN-1
        D[(Choose antipsychotic)]
    end

    subgraph BN-2
    end

    subgraph BN-3
        E[(Continuing the Same Antipsychotics or switch?)]
        F((Continue the same antipsychotic))
        F1{No}
        F2(Plan for LAI)
    end

    subgraph BN-4
        G[(Indication for Aripiprazole?)]
        H((Aripiprazole))
    end
                                                                                       
    B1(Name of the current antipsychotic known?)
    

    B --> B1
    B1 --> |No| D
    B1 --> |Yes| E
    E --> |Yes| F
    E --> F1
    F1 --> G
    F1 --> F2
    G --> |Yes| H
    G --> |No| D
