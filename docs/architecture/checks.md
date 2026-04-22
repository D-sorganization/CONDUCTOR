# Source-Controlled Checks

Maxwell checks live in `.maxwell/checks/*.md`. Each file has YAML frontmatter
plus a prompt body. The first implementation slice validates definitions,
matches them to changed files, and records structured local results. Backend
execution and GitHub check-run publishing are separate layers.

```markdown
---
id: scope-drift
name: Scope Drift Review
severity: required
applies_to:
  globs:
    - "**/*.py"
trigger:
  events: [pull_request, task_completed]
model_tier: moderate
---

Review the changed files for scope drift against the work item acceptance
criteria. Return structured findings with file and line references.
```

Supported severities are `advisory`, `required`, and `blocking`. Invalid
frontmatter fails fast; duplicate check IDs are rejected.

Use `maxwell-daemon checks list --repo PATH` to inspect definitions and
`maxwell-daemon checks run --repo PATH --changed-file src/app.py` for local
definition validation and applicability results.
