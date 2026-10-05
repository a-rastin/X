# Test fixtures

## Child-first TRUNCATE order (notes before encounters)

**Id:** 589045e3-bd5c-4e42-b2e9-fbb348a0efe4
**Type:** decision
**Type:** workaround
**Status:** active
**Evidence:** confirmed
**Source:** S13 implementation session report; commit b3bd50d
**Verification:** corroborated — migration 0006 declares the notes→encounters FK, and 7 test modules truncate `notes` before `encounters`
**Revisit when:** a new table with an FK into encounters, patients, or users is added, or the truncate helpers stop swallowing errors

Every test truncate that touches encounters lists child tables first, in
FK-dependency order: `TRUNCATE notes, encounters, patients`.

**Reason:** migration 0006 added a `notes→encounters` FK, and PostgreSQL
refuses to truncate a referenced parent while child rows exist (without
CASCADE). The fixture truncate helpers swallow failures, so the resulting
error was silent: patient and encounter rows accumulated across tests and
unrelated suites cascaded into `409 CONFLICT`s. A mid-S13 test run caught
exactly this; listing `notes` first fixed it, and the full suite was
re-verified green twice afterwards. The ordering constraint is invisible in
any single test file — each TRUNCATE looks fine on its own — which is why
the failure showed up far from its cause.

**Rejected alternative:** unknown — no alternative (e.g. `CASCADE`
truncates) is recorded as having been considered; if one surfaces, record
it here.

**Consequence:** any future child table with an FK into encounters,
patients, or users must be added to those TRUNCATE lists in dependency
order, or its leftover rows will silently poison other suites. The
swallowing helpers remain as-is, so an omission will keep failing
silently rather than loudly.
