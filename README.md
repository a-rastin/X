[![Keep the Why](https://keepthewhy.com/assets/badge.svg)](https://keepthewhy.com)

# X

Project documentation lives in [project-documents/dev/](project-documents/dev/).
The reasoning behind project decisions is recorded in the
[context index](context/index.md).

## Offline DDI ingestion and terminology

From `backend/`, build a candidate dataset and validation report with the bundled
draft terminology:

```sh
uv run python -m x_insight.ddi build \
  --sources ../project-documents/medical-documents/DDI-text \
  --terminology ../content/ddi/aliases.json \
  --output /tmp/x-s17-ddi
```

This is an offline candidate build. The current corpus writes both outputs and
exits with status 1 because 26 of 128 documents fail structural validation.
The draft contains 136 concepts, 128 proposed catalog IDs and 415 pending aliases;
no aliases have owner approval, and proposed catalog IDs are not activated.
Unresolved names and pending decisions require review before an S18 release.
Omitting `--terminology` retains raw parsing; an explicitly supplied missing or
invalid terminology file fails and writes a report.

See the [S17 outcome and verification commands](project-documents/dev/progress-tracker.md#s17--controlled-medication-concepts-and-aliases)
and the [DDI decision record](context/ddi.md).
