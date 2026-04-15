.PHONY: install lint test audit run run-debug smoke-test seed-demo web

install:
	uv sync --all-extras

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff check --fix src/ tests/
	uv run ruff format src/ tests/

test:
	uv run pytest -v

audit:
	uv run pip-audit

run:
	uv run qbr run --input task/sample_data --output reports/

run-debug:
	uv run qbr run --input task/sample_data --output reports/ --debug

smoke-test:
	uv run qbr smoke-test

seed-demo:
	uv run qbr seed-demo

web:
	uv run uvicorn qbr_web.app:app --reload --host 0.0.0.0 --port 8000
