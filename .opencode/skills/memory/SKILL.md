---
name: memory
description: Memory of this codebase. Agent reads and writes to this skill to remember things about the codebase. Use when you want to store or retrieve information about the codebase, such as design decisions, module responsibilities, or other relevant knowledge. Read this skill in every session to maintain context and continuity. Write to this skill when you want to persist information for future sessions.
---

# Memory

Create and maintain a memory of the codebase. The final goal is to give the agent the ability to find and recognize different files in this codebase faster and more accurate while burning less tokens.

## Files

Look into the `codebase-memory` folder (if it is absent, then create it!).
/codebase-memory
  /list-of-files.json - a list of all files (only those that are tracked by Git) in the codebase, including their names, paths and labels.
  /file-contents - a folder containing multiple markdown files, each representing the contents of a file in the codebase. The file names should be the same as the original files, but with a `.md` extension. For example, if there is a file named `example.py`, its contents should be stored in `codebase-memory/file-contents/example.py.md`.

### list-of-files.json

The `list-of-files.json` file should contain a JSON array of objects, each representing a file in the codebase. Each object should have the following structure:

```json
[
  {
    "name": "example.py",
    "path": "src/example.py",
    "labels": ["python", "example"]
  },
  {
    "name": "another_file.js",
    "path": "src/another_file.js",
    "labels": ["javascript", "utility"]
  }
]
```

### Labels

The `labels` field in each file object is an array of strings that describe the file's purpose. Labels include:
- `frontend`: files related to the user interface or client-side code.
- `backend`: files related to server-side code or business logic.
- `database`: files related to database interactions or data storage.
- `utility`: files that provide utility functions or helper methods.
- `test`: files that contain tests for the codebase.
- `documentation`: files that contain documentation or comments about the codebase.
- `template`: files that contain templates or boilerplate code.
- `config`: files that contain configuration settings or environment variables.


### File Contents

The `file-contents` folder should contain markdown files representing the contents of each file in the codebase. Each markdown file should have the same name as the original file, but with a `.md` extension. For example, if there is a file named `example.py`, its contents should be stored in `codebase-memory/file-contents/example.py.md`. In each markdown file, include the following sections (not necessary to include all sections if not applicable):
- **File Path**: The relative path of the file in the codebase.
- **File Name**: The name of the file.
- **File Labels**: The labels associated with the file.
- **File Contents**: A summary of the file's contents, including its purpose, functionality, and any important details.
- **Code Snippets**: Include relevant code snippets from the file, if applicable. Use triple backticks (```) to format the code snippets.
- **Dependencies**: List any dependencies or imports used in the file, if applicable.
- **Related Files**: List any related files in the codebase, if applicable.
- **Notes**: Include any additional notes or comments about the file, if applicable.
- **Last Updated (Git)**: The date when the file was last updated, if applicable.


## Session Routine

In every session, the agent should read the `list-of-files.json` then choose and read relevant files from the `file-contents` folder based on the user's queries. The agent should also write to the memory when new information is discovered or when updates are made to the codebase. This ensures that the memory remains up-to-date and accurate for future sessions.

## Commands

- `/memory` - Read this skill and `/codebase-memory/list-of-files.json` to do the user's task.
- `/memorize` - Write to `/codebase-memory` to store new information about the codebase.