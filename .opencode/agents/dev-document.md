---
name: dev-document
description: Subagent to update the project documentation.
mode: subagent
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