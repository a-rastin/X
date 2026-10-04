---
name: dev-commit
description: subagent to commit changes to git.
mode: subagent
model: opencode-go/muse-spark-1.3-contributor#xhigh
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: allow
---

## Skills to Use

- `commit`