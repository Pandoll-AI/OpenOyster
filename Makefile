.PHONY: install dev test lint typecheck format check build run serve demo clean package

install:
	python -m pip install .

dev:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests scripts

typecheck:
	mypy src/openoyster

format:
	ruff format src tests scripts
	ruff check --fix src tests scripts

check: lint typecheck test build

build:
	python -m build

run:
	openoyster run --cycles 1

serve:
	openoyster serve --host 0.0.0.0 --port 8080

demo:
	openoyster init
	openoyster ingest examples/inbox
	openoyster run --cycles 4 --sleep 0
	openoyster status
	openoyster doctor

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

package:
	python scripts/package_repo.py
