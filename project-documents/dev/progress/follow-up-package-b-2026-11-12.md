# Follow-up Package B partial checkpoint — 2026-11-12

Package B is **incomplete**. Migration `0032` adds the follow-up proposal table,
workflow constraint, signed-reference variant and role grants, preserving the
existing workflow immutability trigger and old signature rows. Downgrade refuses
stored follow-up encounters or proposals. The table joins backup ordering after
model and DDI parents and before signatures; the serializer exposes its nullable
reference. Archive schema gates preserve v4 support at `0032` and old archives
may omit the new table.

Restore deliberately rejects follow-up workflows, proposals and signatures until
the typed state, payload and baseline contracts can be checked. Do not interpret
this checkpoint as mixed-record recovery support. Still needed: complete raw-state,
source/baseline ownership, canonical proposal/content hashes, DDI/model identities,
signature schema and exactly-one-variant validation; safe old-archive defaults;
malformed and mixed-record tests, including preserved signed hashes and downgrade
protection. Package C's state contract and package F's proposal/signature contract
must agree with these checks. No new follow-up is created or signed by this work.

Verification: Ruff passed on touched backend files. Four focused state/rejection
checks passed. A disposable PostgreSQL database upgraded from 0031 to 0032;
synthetic SQL checked follow-up kind and immutable workflow identity. The broad
recovery/contract run had 81 passes and three failures: two stale inventory
expectations corrected afterward, and a role-list test affected by naming the
disposable database superuser `x_insight` (the test expects only three roles
with that prefix). The two corrected inventory tests passed on rerun; the role
test passed against a second disposable database using `postgres` as superuser.
No user database was migrated, and no commit or deployment was made.

## Continuation — 2026-11-13

The missing implementation is completed in the [B/F/G integration checkpoint](follow-up-bfg-2026-11-13.md), with exact verification and remaining Package H release work. The preceding partial status describes its earlier checkpoint.
