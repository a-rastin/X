# Follow-up Package G partial checkpoint — 2026-11-13

Package G is **incomplete**. At base revision `eb4663eddc5bdc023d57f7d8249c65c78ad32a12`, added a typed frontend client for the existing follow-up config, preview and revision-checked step-validation endpoints. The client uses the shared authenticated request implementation. The existing server activation gate and fail-closed frontend dispatch remain unchanged.

Verification: `npm --prefix web run build` passed (TypeScript and Vite; Vite reported its existing large-chunk warning). No database was migrated; no browser or public HTTP journey was run. No commit or deployment was made.

Handoff: Package F still lacks public proposal persistence, freshness, final-plan and v5 signing routes. Finish those prerequisites and implement the seven-step bilingual wizard, saved-state navigation and autosave recovery, proposal/final-plan UI, signed chart/report and addenda, then perform Package G's required browser and accessibility acceptance. Do not activate follow-up creation or claim Package G complete until the dependent workflows work end to end. The new client is not yet connected to a screen; its contracts must be checked against the final backend as integration proceeds.

## Continuation — 2026-11-13

The missing implementation is completed in the [B/F/G integration checkpoint](follow-up-bfg-2026-11-13.md), with exact verification and remaining Package H release work. The preceding partial status describes its earlier checkpoint.
