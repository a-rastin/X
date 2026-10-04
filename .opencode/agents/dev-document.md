---
name: dev-document
description: Subagent to update the project documentation.
mode: subagent
model: opencode-go/muse-spark-1.3-contributor#xhigh
permissions:
  - action: edit
    resource: "*"
    effect: allow
  - action: shell
    resource: "*"
    effect: deny
---

# Dev Document

Update these files: `README.md`, `project-documents/dev/progress-tracker.md`

## Skills to Use

- `documentation` 