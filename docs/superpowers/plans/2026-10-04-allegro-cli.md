# Allegro Sandbox CLI Implementation Plan

> **For agentic workers:** Execute natively, task by task; use an independent Python reviewer before delivery.

**Goal:** Deliver a Docker CLI that authenticates and posts Allegro Sandbox offers.

**Architecture:** A transport module enforces Sandbox destinations, an API client
owns OAuth and operation polling, and argparse builds and dispatches commands.
Tokens persist in a mounted volume under a process lock.

**Tech Stack:** Python 3.12+, requests, unittest, Docker Compose.

**Spec:** ../specs/2026-10-04-allegro-cli-design.md

## Global Constraints

- Sandbox endpoints only; TLS verification enabled; no credentialed redirects.
- JSON stdout; diagnostics stderr; nonzero exits for validation/API errors.
- Never automatically replay an uncertain POST.
- Tokens use atomic writes, mode 0600, and an advisory lock.

## Review Focus

- Decimal inputs including NaN, infinity and fractional cents must be rejected.
- Unexpected Location hosts must never receive the bearer token.
- Expired credentials must refresh once, with the rotated token persisted.
- Pending operations must time out with recoverable identifiers.
- Invalid JSON and malformed API responses must produce readable failures.

### Task 1: Authenticated Sandbox client

Files: allegro/transport.py, allegro/client.py, tests/test_client.py.
Interface: Client(state_dir: Path), login(), request(method, path, **kwargs),
complete(response, timeout=120), locked_state(path) context manager.

- [x] Write tests for OAuth pending/slow_down/denied, refresh and rotation,
  401 handling, atomic persistence, operation 202/303 and deadlines, unsafe URLs.
- [x] Implement fixed-host requests with 30-second timeouts and no redirects,
  JSON error rendering, saved credentials and bounded operation polling.
- [x] Run python3 -m unittest discover -s tests -v; all tests must pass.

### Task 2: Offer commands and container delivery

Files: allegro/cli.py, allegro/__main__.py, tests/test_cli.py, Dockerfile,
compose.yaml, requirements.txt, .env.example, examples/*.json, README.md.
Interface: main(argv=None) -> int and build_offer(args) -> dict.

- [x] Write tests for price/quantity, JSON validation, dry-run without auth,
  optional offer fields, draft and publication failure exit behavior.
- [x] Implement command dispatch and offer construction; preserve full custom
  JSON fields; show status and recovery identifiers in diagnostics.
- [x] Build Docker image; run help, dry-run and unittest suite in the image.
- [x] Review code independently, fix findings, repeat affected checks.

## Verification outcome

Image `alluploadgro:local` built successfully. All 29 tests passed locally and
inside the Python 3.12 image. Compose help and JSON-file dry-run succeeded;
status without saved login returned exit 1 with a readable error, proving the
non-root process can create and lock state in the named volume. Independent
Python review approved after malformed-input and pending-state fixes.
Live Sandbox authentication and publication remain unverified without credentials.

No git commits: the workspace is not a Git repository.
