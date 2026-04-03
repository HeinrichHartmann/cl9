.PHONY: test lint install

UV ?= uv

test:
	$(UV) run python -m unittest discover -s tests -p 'test_*.py'

lint:
	$(UV) run ruff check .

install:
	$(UV) tool install --force --reinstall .
