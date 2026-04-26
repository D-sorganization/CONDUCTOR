# Criterion G: Dependency Hygiene

**Repo:** Maxwell-Daemon
**Score:** 60/100
**Weight:** 8%
**Weighted Contribution:** 4.80

## Evidence

```json
{
  "req_lockfiles": 0
}
```

## Findings

### P1: [Maxwell-Daemon] No dependency lockfile

Generate lockfile (pip freeze, Cargo.lock, package-lock.json) for reproducible builds.
