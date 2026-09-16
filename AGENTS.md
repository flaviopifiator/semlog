# AGENTS.md: semlog

This file is for coding agents that CONTRIBUTE to the semlog repository itself. If you are writing call sites in a service that USES semlog, read the agent guide instead (`src/semlog/agent_guide.md`, also returned by `semlog.llm()` and `python -m semlog llm`); this file does not repeat that content.

## Setup

```bash
uv sync
```

Fallback, when `uv` is unavailable:

```bash
pip install -e .
```

## Test

```bash
uv run python -m unittest discover
```

Fallback:

```bash
python -m unittest discover
```

## Lint

```bash
uv run ruff check
```

Fallback:

```bash
ruff check
```

## Format check

```bash
uv run ruff format --check
```

Fallback:

```bash
ruff format --check
```

## Build

```bash
uv build
```

Fallback:

```bash
python -m pip wheel . --no-deps --wheel-dir dist
```

## Hard rules

- **Stdlib only, by default.** Any tool used in development, testing, building, or benchmarking must be stdlib, one of the explicitly approved third-party test/benchmark subjects (FastAPI, Starlette, Django, loguru, structlog), `ruff` (lint and format), GitHub Actions (CI), or the approved build backend (`uv_build`) and its build frontend (the `uv` CLI). `mypy`, `coverage.py`, and `jsonschema` are explicitly not approved. Anything else needs explicit maintainer approval before use; never adopt a new third-party package silently.
- **Zero runtime dependencies.** The built package must always declare zero runtime (non-dev) dependencies, regardless of any development, testing, or benchmarking tooling choice.
- **Flat, simple architecture.** The log-record flow is exactly three fixed stages: a formatter (context, redaction, limits, exception rendering, and JSON encoding, all pre-queue on the caller's thread), a queue handler, and a writer thread. No separate filter stage, no ports/adapters layer, no dependency-injection container, no plugin registry, no configuration classes or settings dicts.
- **No class budget, but no indirect class layers either.** This document does not publish a maximum source-line count or a maximum class count; the guardrail against unbounded growth is human review together with the three-stage architecture (CP-016) and the rules below. The package source code defines no abstract base class (`abc.ABC`, `ABCMeta`, `typing.Protocol`) and creates no classes through factories (`collections.namedtuple`, `typing.NamedTuple`, three-argument `type()`). The public surface is exactly 9 names. Any growth in the public surface is a deliberate, documented, user-approved change.
- **No vendor-specific code.** No company, vendor, or trademark name appears in code, defaults, or examples. Use `example.com` and generic service names instead.

## Tooling policy

The full policy behind the first hard rule (STANDARDS.md section 11, DOC-008):

- Development, testing, packaging, and benchmarking use only the Python standard library by default.
- The only approved third-party test and benchmark subjects are FastAPI, Starlette, Django, loguru, structlog: FastAPI, Starlette, and Django for the compatibility matrix, loguru and structlog for the benchmark comparison.
- `ruff` is approved for lint and format, locally and in CI.
- GitHub Actions is approved for continuous integration.
- The build backend (PEP 517/518) is `uv_build`, the one deliberate exception to the standard-library-only policy, because `distutils` was removed in Python 3.12 (PEP 632) and the standard library ships no build backend:

  ```toml
  [build-system]
  requires = ["uv_build>=0.12.13,<0.13"]
  build-backend = "uv_build"
  ```

- The `uv` CLI (Astral) is approved as the build frontend and development environment manager (`uv build`, `uv sync`, `uv run`). It is used in development and in CI, never at runtime, and the built wheel keeps zero `Requires-Dist` entries regardless of that choice.
- When `uv` is unavailable, the `pip`-based fallbacks are `python -m pip wheel . --no-deps --wheel-dir dist` and `pip install -e .`.
- `mypy`, `coverage.py`, `jsonschema`, `gunicorn`, and `uvicorn` are not approved. Where their function would be needed, the standard-library alternative is used: the tests' own JSON Schema subset validator instead of `jsonschema`, no third-party coverage tool, and `wsgiref` plus a hand-written ASGI caller instead of `gunicorn` and `uvicorn`.
- No further tool is pending approval (none pending in this revision). Any other third-party package needs explicit maintainer approval before use.
- The agent-facing documentation set (`llms.txt`, this `AGENTS.md`, and the agent guide packaged inside the library) introduces no runtime or development dependency beyond what this policy already approves.

## Workflow

- **Strict TDD.** Every code change starts with a failing test (RED), then the minimal implementation to pass it (GREEN), then refactor with the test still passing. No production code is written without a preceding failing test.
- **Conventional Commits.** Every commit message follows the Conventional Commits format. The commit types decide the next version, and the `version-check` pull request check enforces the matching `pyproject.toml` version and CHANGELOG.md section; see [RELEASING.md](RELEASING.md).

## Keep-in-sync rule

A change to the public API, to `configure()`'s parameters, or to the emitted record contract must update the agent guide (`src/semlog/agent_guide.md`) in the same change. Anti-drift checks fail otherwise: the guide's public API section must list exactly `semlog.__all__` with signatures matching `inspect.signature`, and its required section structure must stay intact.

`README.md` (English) and `README.es.md` (Spanish) change together, in the same change: a parity test requires the same heading structure, byte-identical code blocks, the same configuration parameters, and the same badges. Benchmark results are withheld until the maintainer validates them (STANDARDS.md CP-005): neither README nor `BENCHMARKS.md` may publish a measured figure, a results table or chart, or a comparative performance claim, and only the methodology and the harness are published. Neither README has a performance section or links to `BENCHMARKS.md`. The "requirements proven" badge must match the number of requirement IDs declared in STANDARDS.md.

## Traceability

Every new or changed normative rule in STANDARDS.md needs a unique requirement ID (grammar `[A-Z]{2,4}-[0-9]{3}`, section 1.1 of STANDARDS.md), a Backing cell in Annex A stating either an external reference (`External: [TAG], ...`) or `Design decision` (plus a `> **Design decision (ID).**` rationale line for a design-decision rule), and at least one automated test that cites that ID in a `Proves:` docstring line. The test suite enforces this with a stdlib-only traceability check: a declared ID with no citing test, or a test citing an unknown ID, fails the suite.

## Workflows

Only the approved actions are used in any workflow: `actions/checkout`, `actions/setup-python`, `actions/upload-artifact`, `actions/download-artifact`, and `pypa/gh-action-pypi-publish`. Every `uses:` reference is pinned by its full commit SHA, with a `# vX.Y.Z` comment naming the version it resolves to. Every workflow declares `permissions: {}` at the top level and least-privilege `permissions` per job. The only write permissions are `id-token: write` in the `publish` jobs of `release.yml` and `tag.yml`, and `contents: write` in the `tag` job of `tag.yml`, whose checkout is the only one that keeps credentials. No `${{ }}` expression appears inside a `run:` step; pass values through `env:`. A single-line `run:` value containing `: ` or ` #` must be quoted or written as a block scalar (`run: |`), or the workflow is invalid YAML.

To refresh a pin, resolve the tag to its commit SHA with `git ls-remote https://github.com/<owner>/<repo> refs/tags/<tag>`, then update both the SHA and the version comment.

## Branch protection

- A ruleset on `main` requires a pull request and the `ci` checks, and blocks force pushes and branch deletion.
- A tag ruleset restricts creating `v*` tags to maintainers and to the `tag` workflow's `GITHUB_TOKEN`; existing tags cannot be updated or deleted.
- The `pypi` environment requires a reviewer and only allows deployments from `main` (`tag.yml` and manual runs of `release.yml`) or from `v*` tags (`release.yml` on a tag push).
- The Actions settings restrict allowed actions to the approved list above, with the default `GITHUB_TOKEN` in read-only mode.

## Where to look

- [README.md](README.md): how to install and use semlog as a library: quick start, configuration, and recipes for services that consume it. [README.es.md](README.es.md) is its Spanish translation.
- [BENCHMARKS.md](BENCHMARKS.md): the benchmark methodology, its scenarios, and how to run the harness; results are not published until the maintainer validates them.
- [STANDARDS.md](STANDARDS.md): the normative, public form of the project's record contract, severity mapping, extension limits, pipeline guarantees, and compatibility policy, with requirement identifiers and a traceability annex.
- [Agent guide](src/semlog/agent_guide.md): the call-site API reference, rules, and recipes for services that use semlog.
- [llms.txt](llms.txt): the repository's agent-facing entry point, linking to all of the above.
- [RELEASING.md](RELEASING.md): how versions are decided, what the pull request check enforces, and how a merge to `main` is tagged and then published after approval.
- [SECURITY.md](SECURITY.md): how to report a vulnerability and how to verify a release's PEP 740 attestations.
