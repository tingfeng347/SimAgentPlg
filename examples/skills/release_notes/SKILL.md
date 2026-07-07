---
name: release_notes
description: Convert software changes into concise, user-facing release notes.
---

# Release Notes Skill

Write release notes for users of the software rather than for its maintainers.

## Rules

- Start with the version as a heading.
- Group changes under `Added`, `Changed`, or `Fixed` only when needed.
- Explain user-visible behavior and compatibility impact.
- Use short bullet points and avoid implementation details.
- Do not invent changes that are not present in the task.
- Put the complete release note in the `run_finish` summary.
