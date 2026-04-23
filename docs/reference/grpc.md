# gRPC status

Maxwell-Daemon does not currently ship a public gRPC service contract. The package metadata exposes an optional `grpc` extra for future transport work, but the repository does not include `.proto` definitions, generated stubs, or a supported gRPC server entry point.

Use the [REST API](api.md) and [OpenAPI reference](openapi.md) for supported remote control-plane integration today.

## Current contract

- No stable `.proto` files are published.
- No generated Python, TypeScript, or Go gRPC clients are published.
- No compatibility promise exists for a gRPC transport until proto definitions are committed and checked in CI.
- The REST/OpenAPI surface remains the source of truth for fleet control, task submission, gate status, artifacts, audit, and cost reporting.

## Acceptance gate for adding gRPC

Before this page can become a true API reference, a gRPC implementation PR should include:

- Versioned `.proto` files under a predictable path such as `proto/maxwell_daemon/v1/`.
- Generated-code guidance that does not require committing local machine paths.
- Server startup documentation covering TLS, auth, reflection, and port binding.
- Parity notes that map each gRPC service to the REST/OpenAPI route it mirrors or intentionally omits.
- Contract tests that regenerate stubs and fail when committed docs drift from the proto definitions.

Until then, treat gRPC as roadmap-only.
