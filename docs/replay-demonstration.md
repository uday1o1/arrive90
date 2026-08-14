# Held-out replay demonstration

The local explorer serves 200 outcome-blind examples selected from the frozen November and December 2024 final split.
Each example is scored by the exact `FULL-normal-scale-0p5` bundle evaluated in the immutable final report.

![Replay explorer](../artifacts/demos/replay-explorer.png)

[Open the recorded walkthrough](../artifacts/demos/replay-explorer-walkthrough.webm).

## Run it

```sh
uv sync --frozen
make demo
make demo-serve
```

Open `http://127.0.0.1:8000`.
Select a direction, origin, destination, horizon, and held-out replay.
Request the prediction before revealing the later observation.

The prediction view displays the anchor cutoff, split, model bundle, p50, p80, p90, seven fixed-horizon probabilities, CDF, calibration diagnostics, schedule and empirical comparators, and artifact lineage.
The history panel contains only observations visible by the anchor cutoff.
The reveal action then displays the separately stored later interval and outcome state.

## Integrity boundary

The replay fixture was selected by outcome-blind HMAC from the final split and is separately manifested.
It contains no raw vehicle identifier, vehicle label, trip identifier, coordinates, or original source row.
Source-example hashes remain only in the local selection manifest for lineage verification.

The prediction API never receives lower duration bound, upper duration bound, or outcome state.
The browser and API tests verify that the user must cross a separate reveal endpoint before those values become available.
The API prediction also matches the offline scorer within `1e-12` for the same feature row and bundle.

`make demo` loads all allow-listed assets, scores one frozen replay, verifies the prediction and reveal boundary, and compares the generated terminal manifest with the committed expectation byte for byte.
This path is network free after the locked Python environment is installed.
