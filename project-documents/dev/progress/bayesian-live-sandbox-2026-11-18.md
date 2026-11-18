# Live reasoning sandbox — 2026-11-18

Step 5 (S1–S3) mounts the same sandbox in the New case and follow-up
proposal views. The encounter, workflow and displayed proposal ID form
the component key. Its first request has empty overrides. The response
pins the patient baseline and chooses the initial treatment.

The control strip uses the endpoint's descriptors and the released
assessment prompts. Each edit updates the local input immediately.
PANSS slider requests wait 150 ms, while discrete changes and treatment
selections are sent immediately. Hidden branch answers remain in the
local override set. Automatic conditions send null and use the workflow's
ordinary derivation. Each control can revert to its recorded value.

The last complete response drives the charts, evaluated assessments and
field issues during an update. A newer intent cancels the previous request.
The client checks the request epoch, frozen identity, selected treatment
and normalized overrides before displaying a response. Retry submits
the same current intent. The sandbox has no autosave or final-plan
callbacks.

## Verification

- The web production build and all eight web test files passed. Vite
  reported its existing large-chunk warning.
- Chromium and Firefox exercised the mounted shared proposal page using
  responses captured from the PostgreSQL-backed tests. They verified rapid
  slider edits, ignored responses after cancellation, treatment selection,
  retry, proposal replacement, patient ticks, follow-up branch revelation and
  preserved answers, no writes from simulation controls, and layout in Persian
  RTL and dark mode at 360 px.
- All four simulation HTTP tests passed on disposable PostgreSQL 16,
  including checks of unchanged clinical domain state.
- On the local i7-3537U, an in-process HTTP probe of New case against the
  disposable database measured 1,429 ms after clearing the model cache.
  Twenty subsequent requests across four treatments and five PANSS scores
  had a median of 360 ms and a p95 of 737 ms. This includes attribution
  cache misses when changing treatment, and exceeds the 500 ms warm p95
  target. The probe excluded browser debounce and network transport.

## Handoff

Step 6 (S4–S6) can render the active graph and diff from the same committed
response and connect the selected factor to graph highlighting. The hook
retains the initial response for the proposal and owns request invalidation.
Reset can restore that initial response locally and fence any outstanding
request.
