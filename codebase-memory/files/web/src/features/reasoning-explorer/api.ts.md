# web/src/features/reasoning-explorer/api.ts

Source SHA-256: ba2d59822fbd03602ab513d702995154a0621848464be963d594ee7b8aa4c882

## Behavior
simulateReasoning encodes the encounter ID, posts the entire scenario request
and enables CSRF on the identity transport. The caller owns the AbortSignal.
The route needs neither a revision nor an idempotency key.
