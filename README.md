[![Keep the Why](https://keepthewhy.com/assets/badge.svg)](https://keepthewhy.com)

# X

Project documentation lives in [project-documents/dev/](project-documents/dev/).
Recorded progress and agent handoffs start at the
[progress index](project-documents/dev/progress/README.md).
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

See the [S17 outcome and verification commands](project-documents/dev/progress/s17.md#s17--controlled-medication-concepts-and-aliases)
and the [DDI decision record](context/ddi.md).

## Immutable DDI release (S18)

From `backend/`, publish a staged candidate with an owner-reviewed manifest
(`content/ddi/review-manifest.template.json` defaults to `awaiting_review`
and never imports):

```sh
uv run python -m x_insight.ddi publish \
  --staging /tmp/x-s17-ddi \
  --manifest ../content/ddi/review-manifest.template.json
```

Only explicit owner `approved_complete`/`approved_limited` publishes; the same
content hash reuses version `ddi-<12hex>`, changed sources yield a new version,
and rejected/invalid publishes leave the prior release readable. The real
corpus remains `awaiting_review` (0 approved aliases + 26 structural
failures); S18 verification used synthetic fixtures only.

See the [S18 outcome and verification commands](project-documents/dev/progress/s18.md#s18--build-review-and-publish-an-immutable-ddi-release-2026-10-06).

## Pharmacotherapy review draft (S27)

The [pharmacotherapy package](content/questions/pharmacotherapy/manifest.json)
and isolated candidate history describe established-treatment review. They remain
`awaiting_review`; the runtime defaults and active clinical content are unchanged.
See the [S27 report](project-documents/dev/progress/s27.md) for source provenance,
public verification seams, review questions and the S29 handoff.

From `backend/`, verify the package with:

```sh
.venv/bin/python -m pytest tests/models/test_pharmacotherapy_package.py -q
```
