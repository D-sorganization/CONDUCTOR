# Maxwell Eval Harness

The `maxwell-daemon eval` command runs deterministic workflow scenarios, evaluates model performance across complex tasks, and produces stable regression reports.

## Configuration and Architecture

The eval harness uses a dynamic, configuration-driven architecture based on YAML suite definitions (loaded via `maxwell_daemon/evals/registry.py`). This decouples benchmark scenarios from core source code, improving maintainability and ensuring DRY compliance.

Current starter scenarios and curated benchmark suites cover:

- **SWE-bench-lite**: End-to-end software engineering evaluations.
- **HumanEval+**: Enhanced Python coding evaluations.
- **MBPP**: Mostly Basic Python Problems dataset.
- **repo_local**: Local repository-specific smoke tests (single-file bug fixes, GAAI import workflows).
- **tool_use_smoke**: Agent tool use, policy checks, and required-approval workflows.
- **latency_suite**: Model and backend latency benchmarks.

## Command Line Interface

Typical local commands:

```powershell
maxwell-daemon eval list
maxwell-daemon eval run --suite swe_bench_lite --output .maxwell/evals
maxwell-daemon eval report <run-id> --output .maxwell/evals
maxwell-daemon eval compare <base-run-id> <candidate-run-id> --output .maxwell/evals
```

## API Endpoints (Issue #494)

The harness is deeply integrated into the daemon control plane. The following REST routes trigger and report on runs:
- `POST /api/v1/evals/run`
- `GET /api/v1/evals/leaderboard`

## Determinism and Stability
Normal CI smoke evals (`repo_local`, `tool_use_smoke`) must remain deterministic and avoid network or live provider access unless specifically mocked. Live model or provider-backed evaluations (like SWE-bench against OpenAI/Anthropic APIs) should be added behind explicit opt-in flags and should not replace the deterministic fixture suites.
