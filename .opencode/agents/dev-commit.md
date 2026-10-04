---
name: dev-commit
description: subagent to commit changes to git.
mode: subagent
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