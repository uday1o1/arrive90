.PHONY: sync lock-check format format-check lint typecheck test frontend-check browser-install browser-test check check-all audit-source milestone1-evidence milestone2-evidence milestone3-evidence milestone4-evidence milestone5-evidence milestone6-evidence milestone7-evidence milestone8-evidence qualify-milestone6 qualify-milestone7 qualify-milestone8 build-otp-graph benchmark-milestone5 benchmark-milestone6 gate

UV_CACHE_DIR ?= .cache/uv
UV := UV_CACHE_DIR=$(UV_CACHE_DIR) uv
UV_RUN := $(UV) run --no-sync

sync:
	$(UV) sync --frozen

lock-check:
	$(UV) lock --check

format:
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix .

format-check:
	$(UV_RUN) ruff format --check .

lint:
	$(UV_RUN) ruff check .

typecheck:
	$(UV_RUN) mypy

test:
	$(UV_RUN) pytest

browser-install:
	npm ci
	npx playwright install chromium

frontend-check:
	node --check packages/service/src/arrive90_service/web/app.js
	node --check playwright.config.js
	node --check tests/browser/rider-workflows.spec.js

browser-test: frontend-check
	npm run test:e2e

check: lock-check format-check lint typecheck test

check-all: check browser-test

audit-source:
	@test -n "$(INDEX)" || (echo "INDEX is required" >&2; exit 2)
	@test -n "$(PARQUET)" || (echo "PARQUET is required" >&2; exit 2)
	@test -n "$(LAMP_ROOT)" || (echo "LAMP_ROOT is required" >&2; exit 2)
	@test -n "$(LICENSE_PDF)" || (echo "LICENSE_PDF is required" >&2; exit 2)
	$(UV_RUN) arrive90-source-audit \
		--index "$(INDEX)" \
		--parquet "$(PARQUET)" \
		--data-dictionary "$(LAMP_ROOT)/Data_Dictionary.md" \
		--transformation-source "$(LAMP_ROOT)/src/lamp_py/performance_manager/flat_file.py" \
		--license "$(LICENSE_PDF)" \
		--acceptance-charter configs/acceptance/v1.yaml \
		--source-commit "$(shell git -C "$(LAMP_ROOT)" rev-parse HEAD)" \
		--reported-command "make audit-source with external immutable inputs" \
		--output artifacts/reports/gates/milestone-0.json

milestone1-evidence:
	$(UV_RUN) python scripts/report_milestone_1.py

milestone2-evidence:
	$(UV_RUN) python scripts/report_milestone_2.py

milestone3-evidence:
	$(UV_RUN) python scripts/report_milestone_3.py

milestone4-evidence:
	$(UV_RUN) python scripts/report_milestone_4.py

milestone5-evidence:
	$(UV_RUN) python scripts/report_milestone_5.py

qualify-milestone6:
	$(UV_RUN) python scripts/qualify_milestone_6.py --output artifacts/reports/qualification/milestone-6-synthetic.json

milestone6-evidence:
	$(UV_RUN) python scripts/report_milestone_6.py

qualify-milestone7:
	$(UV_RUN) python scripts/qualify_milestone_7.py --input artifacts/runtime/playwright-results.json --output artifacts/reports/qualification/milestone-7-browser.json

milestone7-evidence:
	$(UV_RUN) python scripts/report_milestone_7.py

qualify-milestone8:
	$(UV_RUN) python scripts/qualify_milestone_8.py

milestone8-evidence:
	$(UV_RUN) python scripts/report_milestone_8.py

benchmark-milestone5:
	docker build --file benchmarks/milestone5.Dockerfile --tag arrive90/milestone5-benchmark:v1 .
	@echo "Run the image with ARRIVE90_BENCHMARK_IMAGE_ID set to its inspected image ID."

benchmark-milestone6:
	docker build --file benchmarks/milestone6.Dockerfile --tag arrive90/milestone6-benchmark:v1 .
	@echo "Run benchmarks/run_milestone6.py in this image with 4 CPUs, 8307167232 bytes of memory, the inspected image ID, and the Milestone 5 latency report mounted read-only."

build-otp-graph:
	@test -n "$(GTFS)" || (echo "GTFS is required" >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo "OUTPUT is required" >&2; exit 2)
	$(UV_RUN) python tools/build_otp_graph.py --gtfs "$(GTFS)" --output "$(OUTPUT)"

gate:
	@test -n "$(MILESTONE)" || (echo "MILESTONE is required" >&2; exit 2)
	$(UV_RUN) python scripts/gate.py "$(MILESTONE)"
