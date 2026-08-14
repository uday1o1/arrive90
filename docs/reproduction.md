# Reproduction guide

## Reproduction levels

The repository separates a small portfolio demonstration from the large full-year rebuild.
The demo and software suite need only committed artifacts.
The full pipeline additionally needs about 8.8 GB of verified raw and expanded source data, about 3.7 GB of derived data, and the accepted frozen final-prediction runtime.

No GPU, AWS account, AWS CLI, payment card, or private credential is required.

## Toolchain

The accepted environment uses Python 3.12, uv 0.11.23, Node.js 24.16.0, npm 11.13.0, and Playwright 1.61.0.
Python dependencies are locked by `uv.lock`, and browser dependencies are locked by `package-lock.json`.
The model uses CPU-only XGBoost 3.3.0 with one training thread.

## Clean reader workflow

```sh
git clone https://github.com/uday1o1/arrive90.git
cd arrive90
uv sync --frozen
make demo
make demo-serve
```

Open `http://127.0.0.1:8000` and complete one prediction and reveal.
`make demo` must print a `PASSED` terminal manifest and exit zero before the server is started.

## Complete local software verification

Install the exact Node dependency and Chromium binary once.

```sh
make browser-install
```

Run formatting checks, lint, strict type checking, the Python coverage suite, deterministic chart verification, and all four browser workflows.

```sh
make check-all
```

Run the nine paired seeded-defect and nearby-control scenarios.

```sh
make qualify-milestone6-robustness
```

Run the dependency and attribution inventory.

```sh
make license-evidence
```

Run the active repository audit only when the worktree is clean.

```sh
make repository-audit
```

## Download the immutable 2024 inputs

The public source workflow reads the committed inventory and acquired-content locks.
It downloads one object at a time per worker, resumes exact byte ranges, and rejects any changed ETag, size, digest, row count, or schema.

```sh
uv run arrive90 source download --year 2024 --workers 4
```

Expected acquired content:

| Source measure | Expected value |
| --- | ---: |
| Vehicle objects | 368 |
| Vehicle source rows | 208,444,419 |
| Bus Observatory bytes | 7,684,705,816 |
| Expanded schedule database bytes | 968,630,272 |
| Acquisition lock SHA-256 | `af6b8967a422f18d0ccb35dd206c9a533daf91ed4eaac81faa7ba70109adc2f9` |

The command writes only ignored raw data and must reproduce the committed acquired-content lock.
If a public object has changed or is unavailable, the correct result is failure rather than a rewritten expected hash.

## Rebuild normalized data and the model population

```sh
uv run arrive90 data normalize --year 2024
uv run arrive90 data build-dataset
```

The normalization command must reproduce dataset-manifest SHA-256 `add71239ed0a81d146e18390958db66708304821800acb4b332a1b1b16a429b3`.
The population command must reproduce unsampled manifest SHA-256 `e02e40b899bfa02f441aa5e2f7352e7871961eb079b5867755c4872bef8b91d7` and selected-population manifest SHA-256 `568971b631aa91ed12044182c2a3e9bd4a17274392529cb0dc9d4d43c7130cc4`.

The accepted full-year normalization took 1,371 to 1,459 seconds on the recorded ARM64 machine.
The complete selected-population build took about 648 seconds and peaked at 1,145,915,806 bytes of process RSS.

## Rebuild the frozen model registry

```sh
uv run arrive90 model train
```

The command trains and independently calibrates seven final-compared bundles.
It must reproduce the promoted identifier `FULL-normal-scale-0p5`, the frozen feature schema, prediction tolerance, and registry manifests.
The accepted training and calibration run took about 113 seconds.

## Final-evaluation boundary

The original metric-producing final evaluation was opened once after the protocol and models were frozen.
Do not rerun `arrive90 evaluate final` as an exploratory command or use a changed implementation to overwrite the accepted report.

The supported reproducibility path rebuilds the deterministic report from the frozen prediction manifest and protocol through `scripts/reproduce_full_year.py`.
That script verifies the raw lock, normalized and population manifests, seven model bundles, accepted final report, public claims, and demo terminal before producing the expected terminal manifest.

The repository-owned clean-reproduction qualifier requires:

- A local Git repository containing the exact qualified commit.
- The ignored raw-data root with all locked inputs.
- A new nonexistent rebuild root.
- The accepted frozen Milestone 4 runtime containing the prediction manifest and evaluation protocol.

```sh
COMMIT=85824356cd433b2054f21c62e55d476ca5155ce4
REBUILD_ROOT=/absolute/path/to/new-arrive90-rebuild

make qualify-milestone6-reproduction \
  REPOSITORY="$(pwd)" \
  COMMIT="$COMMIT" \
  DATA_ROOT="$(pwd)/data" \
  FROZEN_RUNTIME="$(pwd)/artifacts/runtime/milestone-4-recovery" \
  REBUILD_ROOT="$REBUILD_ROOT"
```

The accepted qualification cloned commit `85824356cd433b2054f21c62e55d476ca5155ce4`, rebuilt every derived stage, matched terminal SHA-256 `0a01c5f96561e9925eef2f419d670dd11e76e666aedf0b0003ae5ba605ecf3c1`, and then verified a no-op second pass without rewriting 4,827 files.

## Repository-owned clean-checkout verification

After a documentation or implementation commit has been pushed, run the complete reader path against that exact remote commit.

```sh
QUALIFIED_COMMIT=$(git rev-parse HEAD)
make clean-checkout \
  REPOSITORY=https://github.com/uday1o1/arrive90.git \
  COMMIT="$QUALIFIED_COMMIT" \
  OUTPUT=artifacts/reports/qualification/clean-checkout-v1.2.json
```

The runner clones the exact detached commit, installs locked dependencies, runs the demo, complete quality and browser suites, robustness qualification, license audit, repository audit, and accepted milestone gates, and verifies that the clone remains clean.
It records infrastructure failures as failures.

## Generated output policy

Keep raw feeds, normalized rows, model populations, full registries, profilers, and runtime output in their ignored directories.
Do not add a changed source hash, expected output, metric table, or terminal manifest merely to make a failing command pass.
Only reviewed compact reports, charts, source locks, acceptance configs, the allow-listed demo bundle, and the sanitized replay fixture belong in Git.
