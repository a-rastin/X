# web/src/features/reasoning-explorer/types.ts

Source SHA-256: 9460db394f8e34414bffde89ac60ef83fa67f2d9e482b4d1b03c6c3763a28e6f

## Behavior
SimulationRequest identifies the displayed proposal and carries all current
changes. SimulationResponse models probability comparisons, decomposition,
structured diff, active pathways and partial/non-model status. Renderers
should use the returned semantic fields directly. Tests compare IDs, versions
and enums against a backend-produced fixture.
