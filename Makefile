.PHONY: sync lock-check format format-check lint typecheck test frontend-check browser-install browser-test docs-assets docs-assets-check demo demo-serve check check-all source-lock qualify-milestone0 qualify-milestone1 qualify-milestone2 qualify-milestone3 qualify-milestone4 qualify-milestone5 qualify-milestone6-robustness qualify-milestone6-reproduction qualify-milestone7 milestone1-evidence milestone2-evidence milestone3-evidence milestone4-evidence milestone5-evidence milestone6-evidence milestone7-evidence reproduce-full-year benchmark-milestone6 license-evidence repository-audit public-claims-evidence clean-checkout gate

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

frontend-check:
	node --check packages/service/src/arrive90_service/web/app.js
	node --check playwright.config.js
	node --check tests/browser/rider-workflows.spec.js

browser-install:
	npm ci
	npx playwright install chromium

browser-test: frontend-check
	npm run test:e2e

demo:
	$(UV_RUN) python scripts/run_demo_smoke.py

demo-serve:
	$(UV_RUN) arrive90-api

check: lock-check format-check lint typecheck test
	$(UV_RUN) python scripts/build_docs_assets.py --check

check-all: check browser-test

docs-assets:
	$(UV_RUN) python scripts/build_docs_assets.py

docs-assets-check:
	$(UV_RUN) python scripts/build_docs_assets.py --check

source-lock:
	$(UV_RUN) arrive90 source lock

qualify-milestone0:
	$(UV_RUN) python scripts/qualify_milestone_0.py

qualify-milestone1:
	$(UV_RUN) python scripts/qualify_milestone_1.py

qualify-milestone2:
	$(UV_RUN) python scripts/qualify_milestone_2.py

qualify-milestone3:
	$(UV_RUN) python scripts/qualify_milestone_3.py

qualify-milestone4:
	$(UV_RUN) python scripts/qualify_milestone_4.py

qualify-milestone5:
	$(UV_RUN) python scripts/qualify_milestone_5.py

milestone1-evidence: qualify-milestone1

milestone2-evidence: qualify-milestone2

milestone3-evidence: qualify-milestone3

milestone4-evidence: qualify-milestone4

milestone5-evidence:
	$(UV_RUN) python scripts/report_milestone_5.py

qualify-milestone6-robustness:
	$(UV_RUN) python scripts/qualify_milestone_6_robustness.py

qualify-milestone6-reproduction:
	@test -n "$(REPOSITORY)" || (echo "REPOSITORY is required" >&2; exit 2)
	@test -n "$(COMMIT)" || (echo "COMMIT is required" >&2; exit 2)
	@test -n "$(DATA_ROOT)" || (echo "DATA_ROOT is required" >&2; exit 2)
	@test -n "$(FROZEN_RUNTIME)" || (echo "FROZEN_RUNTIME is required" >&2; exit 2)
	@test -n "$(REBUILD_ROOT)" || (echo "REBUILD_ROOT is required" >&2; exit 2)
	$(UV_RUN) python scripts/qualify_milestone_6_reproduction.py \
		--repository "$(REPOSITORY)" \
		--commit "$(COMMIT)" \
		--data-root "$(DATA_ROOT)" \
		--frozen-evaluation-runtime "$(FROZEN_RUNTIME)" \
		--rebuild-root "$(REBUILD_ROOT)" \
		--output artifacts/reports/qualification/milestone-6-reproduction-v1.2.json

milestone6-evidence:
	$(UV_RUN) python scripts/report_milestone_6.py

qualify-milestone7:
	$(UV_RUN) python scripts/qualify_milestone_7.py

milestone7-evidence:
	$(UV_RUN) python scripts/report_milestone_7.py

reproduce-full-year:
	$(UV_RUN) python scripts/reproduce_full_year.py

benchmark-milestone6:
	$(UV_RUN) python benchmarks/run_milestone6.py

license-evidence:
	$(UV_RUN) python scripts/audit_licenses.py \
		--output artifacts/reports/qualification/licenses-v1.json

repository-audit:
	$(UV_RUN) python scripts/audit_repository.py \
		--output artifacts/reports/qualification/repository-audit-v1.2.json

public-claims-evidence:
	$(UV_RUN) python scripts/build_public_claims.py \
		--output artifacts/reports/qualification/public-claims-v1.2.json

clean-checkout:
	@test -n "$(REPOSITORY)" || (echo "REPOSITORY is required" >&2; exit 2)
	@test -n "$(COMMIT)" || (echo "COMMIT is required" >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo "OUTPUT is required" >&2; exit 2)
	$(UV_RUN) python scripts/qualify_clean_checkout.py \
		--repository "$(REPOSITORY)" --commit "$(COMMIT)" --output "$(OUTPUT)"

gate:
	@test -n "$(MILESTONE)" || (echo "MILESTONE is required" >&2; exit 2)
	$(UV_RUN) python scripts/gate.py "$(MILESTONE)"
