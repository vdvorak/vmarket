.PHONY: test lint fix check image

DEV := docker build -q --target dev -t vmarket-dev app >/dev/null && docker run --rm -v $(CURDIR)/app:/app -w /app vmarket-dev

test: ## pytest, např. make test ARGS="-k health"
	$(DEV) pytest $(ARGS)

lint: ## ruff + mypy
	$(DEV) sh -c "ruff check && ruff format --check && mypy"

fix: ## ruff autofix + format
	$(DEV) sh -c "ruff check --fix && ruff format"

image: ## runtime image
	docker build --target runtime -t vmarket app

check: lint test
