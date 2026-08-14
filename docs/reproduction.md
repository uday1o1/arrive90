# Reproduction guide

## Scope

This guide reproduces the locally executable software, browser, security, fault, license, and synthetic protocol evidence.
It does not download restricted data, create an empirical model, or turn a failing milestone into a pass.

The primary workflow works without a GPU.
OpenTripPlanner graph construction and the performance containers require Docker.
The historical source gate requires external data described separately below.

## Toolchain

The verified local toolchain uses:

- Git.
- Python 3.12 selected by `.python-version`.
- uv 0.11.23.
- Node.js 24.16.0 and npm 11.13.0 for browser verification.
- Docker 29.5.2 with a Linux ARM64 runtime for the retained performance and image-scan evidence.

Python dependencies are resolved by `uv.lock`.
Node dependencies are resolved by `package-lock.json`.
Container bases, OpenTripPlanner, and Trivy use exact image digests.

## Fresh checkout

Clone the repository and install the frozen Python environment.

```sh
git clone https://github.com/uday1o1/arrive90.git
cd arrive90
uv python install
uv sync --frozen
```

Install the exact browser dependency and Chromium binary.

```sh
npm ci
npx playwright install chromium
```

Run the complete local and browser suite.

```sh
make check-all
```

Run the repository and release-image security gate.

```sh
make security-scan
make security-evidence
```

Run the dependency-license and fault qualifications.

```sh
make license-evidence
make reliability-evidence
```

Run the repository audit only from a clean worktree.

```sh
make repository-audit
```

The audit writes a tracked report by default, so a maintainer generating new evidence should review and commit that report afterward.
The clean-checkout automation writes its audit under ignored runtime storage so its fresh clone remains clean.

## Start the user-facing path

```sh
uv run arrive90-api \
  --host 127.0.0.1 \
  --port 8000 \
  --state artifacts/runtime/arrive90.sqlite3
```

Open `http://127.0.0.1:8000`.
The current expected state is explicit schedule-only abstention with null model output and no trip-start capability.

## Reproduce synthetic qualifications

```sh
make qualify-milestone6
make qualify-milestone8
```

Milestone 6 intentionally remains `INSUFFICIENT_EVIDENCE` because a synthetic fixture cannot satisfy the empirical gate.
Milestone 8 should report `PASSED` only for `SYNTHETIC_PROTOCOL_MECHANICS_ONLY` and must not create a prospective claim.

Run the browser qualification after Playwright finishes.

```sh
make browser-test
make qualify-milestone7
```

## Reproduce performance measurements

The retained performance evidence uses an ARM64 Docker runtime with four allocated CPUs and 8,307,167,232 memory bytes.
Do not compare results from another allocation as if they were the same benchmark.

```sh
make benchmark-milestone5
M5_IMAGE_ID=$(docker image inspect arrive90/milestone5-benchmark:v1 --format '{{.Id}}')
docker run --rm \
  --cpuset-cpus 0-3 \
  --cpus 4 \
  --memory 8307167232 \
  --network none \
  -e ARRIVE90_BENCHMARK_IMAGE_ID="$M5_IMAGE_ID" \
  -v "$(pwd)/artifacts/reports/qualification:/out" \
  arrive90/milestone5-benchmark:v1 \
  --output /out/milestone-5-latency.json
```

Then run the candidate and replay workload, which binds the new API report hash.

```sh
make benchmark-milestone6
M6_IMAGE_ID=$(docker image inspect arrive90/milestone6-benchmark:v1 --format '{{.Id}}')
docker run --rm \
  --cpuset-cpus 0-3 \
  --cpus 4 \
  --memory 8307167232 \
  --network none \
  -e ARRIVE90_BENCHMARK_IMAGE_ID="$M6_IMAGE_ID" \
  -v "$(pwd)/artifacts/reports/qualification:/app/artifacts/reports/qualification" \
  arrive90/milestone6-benchmark:v1 \
  --output /app/artifacts/reports/qualification/milestone-6-performance.json
```

Performance outputs vary with scheduling and hardware.
Review the exact environment and gate fields before committing a refreshed artifact.

## Automated second-environment qualification

After the commit exists on the remote, run the repository-owned fresh-clone workflow from a clean checkout.

```sh
QUALIFIED_COMMIT=$(git rev-parse HEAD)
make clean-checkout \
  REPOSITORY=https://github.com/uday1o1/arrive90.git \
  COMMIT="$QUALIFIED_COMMIT" \
  OUTPUT=artifacts/reports/qualification/clean-checkout-v1.json
```

The runner clones into a temporary directory, checks out the exact detached commit, installs locked environments, runs Python and Chromium verification, builds and scans the release image, reruns security, license, reliability, and repository audits, confirms the exact SHA, and verifies that the clone remains clean.
It returns `FAILED` at the first failed command and never converts an infrastructure failure into success.

## Historical source gate

Do not run the empirical pipeline against the current coalesced rail export and treat it as accepted evidence.
First obtain an authorized primitive Vehicle Position archive or updated export meeting [source-feasibility.md](source-feasibility.md).

Set explicit absolute paths to the immutable downloaded inputs and run:

```sh
make audit-source \
  INDEX="$ARRIVE90_LAMP_INDEX" \
  PARQUET="$ARRIVE90_LAMP_PARQUET" \
  LAMP_ROOT="$ARRIVE90_LAMP_SOURCE" \
  LICENSE_PDF="$ARRIVE90_MASSDOT_LICENSE"
```

Verify the resulting source hashes and run:

```sh
make gate MILESTONE=0
```

Proceed to later empirical commands only if that gate is `PASSED`.
The current public inputs produce `FAILED`, which is the expected truthful result.

## Generated output policy

Keep raw feeds under `data/raw`, normalized rows under `data/normalized`, model binaries under `artifacts/models`, graphs under `artifacts/graphs`, profiler output under `artifacts/profiler`, and runtime state and raw scan output under `artifacts/runtime`.
Those paths are ignored and must not be added to Git.

Commit only reviewed source, configuration, compact aggregate reports, public-safe cards, and synthetic demonstration artifacts.
Never commit credentials, HMAC keys, rider information, coordinates, restricted data, mutable SQLite state, raw model artifacts, or scanner databases.
