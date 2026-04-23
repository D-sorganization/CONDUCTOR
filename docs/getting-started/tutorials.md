# Tutorials

Issue #19 originally called for short video tutorials. The current repository
standard is a written tutorial set that can be reviewed in pull requests,
validated with `mkdocs build --strict`, and kept in sync with CLI and API
changes. Each tutorial below is intentionally short, task-focused, and
copyable.

## Tutorial 1: Bring Up a Local Daemon from Source

Use this when you want a clean first-run path from a source checkout without
guessing which bootstrap commands need to run first.

1. Start from a fresh checkout of `main`.
2. Run the launcher for your platform.
3. Confirm the API is healthy before dispatching any work.

```bash
# Windows
Launch-Maxwell.bat

# macOS
./Launch-Maxwell.command

# Linux
./Launch-Maxwell.sh
```

Then verify the service:

```bash
maxwell-daemon status
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/docs > /dev/null
```

If the launcher reports backend credential warnings, stop there and fix config
or secrets before submitting real tasks.

## Tutorial 2: Dispatch One GitHub Issue Safely

Use this when you want Maxwell-Daemon to plan or implement a single issue while
keeping the task observable.

1. Start the daemon locally.
2. Dispatch the issue in `plan` mode first.
3. Inspect the queued task before switching to implementation.

```bash
maxwell-daemon serve
maxwell-daemon issue dispatch example/service 42 --mode plan
maxwell-daemon tasks list --kind issue --status queued
maxwell-daemon tasks show <task-id>
```

When the task looks correct, re-dispatch or continue with implementation:

```bash
maxwell-daemon issue dispatch example/service 42 --mode implement
maxwell-daemon-runner
```

For the full fleet-oriented variant, continue into the
[fleet issue queue walkthrough](fleet-issue-queue.md).

## Tutorial 3: Enforce a Docs Gate Before Opening a PR

Use this when the change touches Markdown, MkDocs navigation, or docs-linked
contracts and you want the same guard Maxwell can run around agent work.

Create a repo-carried checks file:

```yaml
checks:
  - id: docs
    name: Documentation build
    command: python -m mkdocs build --strict
    trigger_events: [docs, pre_pr]
```

Then run the docs event explicitly:

```bash
maxwell-daemon checks run --repo . --event docs
git diff --check
```

If the docs gate fails, fix broken links, nav drift, or malformed Markdown
before opening the PR.

## Acceptance Gate for Written Tutorials

The written tutorial requirement is satisfied when all of the following remain
true:

- This page is discoverable from `mkdocs.yml`.
- Each tutorial maps to a concrete operator workflow rather than marketing copy.
- Commands shown here remain consistent with the live CLI and documented API.
- Docs pull requests keep `python -m mkdocs build --strict` green.

If the project later adds video tutorials, keep this page as the canonical
text-first reference and link the recordings from here instead of replacing the
written guides.
