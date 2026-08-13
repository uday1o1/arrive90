.PHONY: sync lock-check format format-check lint typecheck test check audit-source milestone1-evidence gate

UV_CACHE_DIR ?= .cache/uv
UV := UV_CACHE_DIR=$(UV_CACHE_DIR) uv

sync:
	$(UV) sync --frozen

lock-check:
	$(UV) lock --check

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

format-check:
	$(UV) run ruff format --check .

lint:
	$(UV) run ruff check .

typecheck:
	$(UV) run mypy

test:
	$(UV) run pytest

check: lock-check format-check lint typecheck test

audit-source:
	@test -n "$(INDEX)" || (echo "INDEX is required" >&2; exit 2)
	@test -n "$(PARQUET)" || (echo "PARQUET is required" >&2; exit 2)
	@test -n "$(LAMP_ROOT)" || (echo "LAMP_ROOT is required" >&2; exit 2)
	@test -n "$(LICENSE_PDF)" || (echo "LICENSE_PDF is required" >&2; exit 2)
	$(UV) run arrive90-source-audit \
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
	$(UV) run python scripts/report_milestone_1.py

gate:
	@test -n "$(MILESTONE)" || (echo "MILESTONE is required" >&2; exit 2)
	$(UV) run python scripts/gate.py "$(MILESTONE)"
