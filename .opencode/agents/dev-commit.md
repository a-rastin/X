---
name: dev-commit
description: subagent to commit changes to git.
mode: subagent
model: OpenCode-Go/Muse-Spark-1.3-Contributor#high
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