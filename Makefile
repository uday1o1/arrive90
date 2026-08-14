.PHONY: sync lock-check format format-check lint typecheck test frontend-check browser-install browser-test demo demo-serve check check-all source-discovery source-discovery-live audit-source audit-milestone0 qualify-milestone0 qualify-milestone1 qualify-milestone2 qualify-milestone3 qualify-milestone4 qualify-milestone5 milestone1-evidence milestone2-evidence milestone3-evidence milestone4-evidence milestone5-evidence milestone6-evidence milestone7-evidence milestone8-evidence milestone9-evidence qualify-milestone6 qualify-milestone6-robustness qualify-milestone6-reproduction qualify-milestone7 qualify-milestone8 security-scan-repository security-build-image security-scan-image security-scan security-evidence license-evidence reliability-evidence repository-audit public-claims-evidence clean-checkout build-otp-graph benchmark-milestone5 benchmark-milestone6 gate

UV_CACHE_DIR ?= .cache/uv
UV := UV_CACHE_DIR=$(UV_CACHE_DIR) uv
UV_RUN := $(UV) run --no-sync
TRIVY_IMAGE := aquasec/trivy@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c
SECURITY_RUNTIME := $(CURDIR)/artifacts/runtime/security
RELEASE_IMAGE := arrive90/release-candidate:v1

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

demo:
	$(UV_RUN) python scripts/run_demo_smoke.py

demo-serve:
	$(UV_RUN) arrive90-api

check: lock-check format-check lint typecheck test

check-all: check browser-test

source-discovery:
	@test -n "$(SOURCE_METADATA)" || (echo "SOURCE_METADATA is required" >&2; exit 2)
	@test -n "$(SOURCE_ARCHIVE)" || (echo "SOURCE_ARCHIVE is required" >&2; exit 2)
	@test -n "$(SOURCE_ACQUIRED_AT)" || (echo "SOURCE_ACQUIRED_AT is required" >&2; exit 2)
	$(UV_RUN) arrive90-discover-rapid-transit-source \
		--metadata "$(SOURCE_METADATA)" \
		--archive "$(SOURCE_ARCHIVE)" \
		--acquired-at-utc "$(SOURCE_ACQUIRED_AT)"

source-discovery-live:
	$(UV_RUN) arrive90-discover-rapid-transit-source --download

audit-milestone0:
	@test -n "$(EVENT_METADATA)" || (echo "EVENT_METADATA is required" >&2; exit 2)
	@test -n "$(EVENT_ARCHIVE)" || (echo "EVENT_ARCHIVE is required" >&2; exit 2)
	@test -n "$(EVENT_ACQUIRED_AT)" || (echo "EVENT_ACQUIRED_AT is required" >&2; exit 2)
	@test -n "$(SCHEDULE_ARCHIVE)" || (echo "SCHEDULE_ARCHIVE is required" >&2; exit 2)
	@test -n "$(SCHEDULE_DATABASE)" || (echo "SCHEDULE_DATABASE is required" >&2; exit 2)
	@test -n "$(SCHEDULE_ACQUIRED_AT)" || (echo "SCHEDULE_ACQUIRED_AT is required" >&2; exit 2)
	@test -n "$(LAMP_ROOT)" || (echo "LAMP_ROOT is required" >&2; exit 2)
	@test -n "$(PRODUCER_ROOT)" || (echo "PRODUCER_ROOT is required" >&2; exit 2)
	@test -n "$(LICENSE_PDF)" || (echo "LICENSE_PDF is required" >&2; exit 2)
	$(UV_RUN) arrive90-audit-milestone0 \
		--event-metadata "$(EVENT_METADATA)" \
		--event-archive "$(EVENT_ARCHIVE)" \
		--event-acquired-at-utc "$(EVENT_ACQUIRED_AT)" \
		--schedule-archive "$(SCHEDULE_ARCHIVE)" \
		--schedule-database "$(SCHEDULE_DATABASE)" \
		--schedule-acquired-at-utc "$(SCHEDULE_ACQUIRED_AT)" \
		--lamp-root "$(LAMP_ROOT)" \
		--producer-root "$(PRODUCER_ROOT)" \
		--license "$(LICENSE_PDF)"

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

milestone1-evidence:
	$(UV_RUN) python scripts/report_milestone_1.py

milestone2-evidence:
	$(UV_RUN) python scripts/qualify_milestone_2.py

milestone3-evidence:
	$(UV_RUN) python scripts/qualify_milestone_3.py

milestone4-evidence:
	$(UV_RUN) python scripts/qualify_milestone_4.py

milestone5-evidence:
	$(UV_RUN) python scripts/report_milestone_5.py

qualify-milestone6: benchmark-milestone6 qualify-milestone6-robustness
	$(UV_RUN) python scripts/qualify_milestone_6_local.py
	$(UV_RUN) python scripts/reproduce_full_year.py

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
	$(UV_RUN) python scripts/qualify_milestone_7.py --input artifacts/runtime/playwright-results.json --output artifacts/reports/qualification/milestone-7-browser.json

milestone7-evidence:
	$(UV_RUN) python scripts/report_milestone_7.py

qualify-milestone8:
	$(UV_RUN) python scripts/qualify_milestone_8.py

milestone8-evidence:
	$(UV_RUN) python scripts/report_milestone_8.py

security-scan-repository:
	mkdir -p "$(SECURITY_RUNTIME)"
	docker run --rm \
		-v "$(CURDIR):/workspace:ro" \
		-v "$(SECURITY_RUNTIME):/out" \
		-v arrive90-trivy-cache:/root/.cache/trivy \
		$(TRIVY_IMAGE) fs \
		--scanners vuln,secret,misconfig,license \
		--include-dev-deps \
		--severity CRITICAL,HIGH \
		--exit-code 0 \
		--format json \
		--output /out/repository.json \
		--skip-dirs .git --skip-dirs .venv --skip-dirs .cache \
		--skip-dirs node_modules --skip-dirs artifacts/runtime \
		/workspace
	docker run --rm \
		-v arrive90-trivy-cache:/root/.cache/trivy \
		$(TRIVY_IMAGE) version --format json > "$(SECURITY_RUNTIME)/trivy-version.json"

security-build-image:
	mkdir -p "$(SECURITY_RUNTIME)"
	docker build --file deployment/Dockerfile --tag $(RELEASE_IMAGE) .
	docker run --rm --network none --read-only --entrypoint python $(RELEASE_IMAGE) -c \
		"import os; assert os.getuid() == 65532; import arrive90_service.app; print('release candidate import passed')"
	docker save --output "$(SECURITY_RUNTIME)/release-candidate.tar" $(RELEASE_IMAGE)

security-scan-image: security-build-image
	docker run --rm \
		-v "$(SECURITY_RUNTIME):/out" \
		-v arrive90-trivy-cache:/root/.cache/trivy \
		$(TRIVY_IMAGE) image \
		--input /out/release-candidate.tar \
		--scanners vuln,secret,misconfig,license \
		--severity CRITICAL,HIGH \
		--exit-code 0 \
		--format json \
		--output /out/image.json

security-scan: security-scan-repository security-scan-image

security-evidence:
	$(UV_RUN) python scripts/qualify_milestone_9_security.py \
		--repository-report artifacts/runtime/security/repository.json \
		--image-report artifacts/runtime/security/image.json \
		--version-report artifacts/runtime/security/trivy-version.json \
		--output artifacts/reports/qualification/milestone-9-security.json

license-evidence:
	$(UV_RUN) python scripts/audit_licenses.py \
		--output artifacts/reports/qualification/licenses-v1.json

reliability-evidence:
	$(UV_RUN) python scripts/qualify_milestone_9_reliability.py \
		--output artifacts/reports/qualification/milestone-9-reliability.json

repository-audit:
	$(UV_RUN) python scripts/audit_repository.py \
		--output artifacts/reports/qualification/repository-audit-v1.json

public-claims-evidence:
	$(UV_RUN) python scripts/build_public_claims.py \
		--output artifacts/reports/qualification/public-claims-v1.json

milestone9-evidence:
	$(UV_RUN) python scripts/report_milestone_9.py

clean-checkout:
	@test -n "$(REPOSITORY)" || (echo "REPOSITORY is required" >&2; exit 2)
	@test -n "$(COMMIT)" || (echo "COMMIT is required" >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo "OUTPUT is required" >&2; exit 2)
	$(UV_RUN) python scripts/qualify_clean_checkout.py \
		--repository "$(REPOSITORY)" --commit "$(COMMIT)" --output "$(OUTPUT)"

benchmark-milestone5:
	docker build --file benchmarks/milestone5.Dockerfile --tag arrive90/milestone5-benchmark:v1 .
	@echo "Run the image with ARRIVE90_BENCHMARK_IMAGE_ID set to its inspected image ID."

benchmark-milestone6:
	$(UV_RUN) python benchmarks/run_milestone6.py

build-otp-graph:
	@test -n "$(GTFS)" || (echo "GTFS is required" >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo "OUTPUT is required" >&2; exit 2)
	$(UV_RUN) python tools/build_otp_graph.py --gtfs "$(GTFS)" --output "$(OUTPUT)"

gate:
	@test -n "$(MILESTONE)" || (echo "MILESTONE is required" >&2; exit 2)
	$(UV_RUN) python scripts/gate.py "$(MILESTONE)"
