# SHIELD — developer interface. Run `make` for help.
#
# --env-file .env is required because compose lives in compose/ (not project
# root), so its default .env discovery looks in compose/, not at the project
# root where the gitignored .env actually lives. Without this flag every
# ${VAR:-default} substitution falls back to its default — including
# ANTHROPIC_API_KEY=, which breaks AI calls silently.
COMPOSE        := docker compose --env-file .env -f compose/docker-compose.yml
COMPOSE_DEV    := $(COMPOSE) -f compose/docker-compose.dev.yml
SHELL          := /bin/bash

.PHONY: help
help: ## list targets
	@awk 'BEGIN{FS=":.*##"}/^[a-zA-Z0-9_.-]+:.*##/{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: env
env: ## copy .env.example to .env if it doesn't exist
	@test -f .env || cp .env.example .env
	@grep -q '^ANTHROPIC_API_KEY=$$' .env && echo "WARNING: set ANTHROPIC_API_KEY in .env" || true

.PHONY: build
build: env ## build all images
	$(COMPOSE) build

.PHONY: up
up: env ## bring core services up (db, keycloak, app)
	$(COMPOSE) up -d db keycloak app

.PHONY: down
down: ## stop containers (keeps volumes)
	$(COMPOSE) down

.PHONY: nuke
nuke: ## stop AND remove volumes (DESTROYS DATA)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## tail app logs
	$(COMPOSE) logs -f app

.PHONY: migrate
migrate: ## run alembic upgrade head inside app container
	$(COMPOSE) exec app flask --app wsgi:app db upgrade

.PHONY: seed
seed: ## load demo client + 75-product capability list + projects
	$(COMPOSE) exec app flask --app wsgi:app seed

.PHONY: reset
reset: ## drop schema, re-migrate, reseed
	$(COMPOSE) exec app flask --app wsgi:app reset-db --yes
	$(COMPOSE) exec app flask --app wsgi:app db upgrade
	$(MAKE) seed

.PHONY: demo
demo: env build ## first-run: bring everything up + seed
	$(COMPOSE) up -d db keycloak
	@echo "Waiting for db + keycloak healthchecks..."
	@$(COMPOSE) up -d app
	@sleep 5
	$(MAKE) migrate
	$(MAKE) seed
	@echo ""
	@echo "================================================================"
	@echo " SHIELD is up:    http://localhost:8000"
	@echo " Keycloak admin:  http://localhost:8080  (admin / admin)"
	@echo " Users:           admin@demo / client@demo / reviewer@demo   pw: demo"
	@echo "================================================================"

.PHONY: vendor-assets
vendor-assets: ## download USWDS + HTMX into shield/static/
	$(COMPOSE) exec app python scripts/vendor_assets.py

.PHONY: agent
agent: env ## run the headless dev-agent inside docker. Usage: make agent ARGS="your prompt"
	@test -n "$(ARGS)" || (echo "Usage: make agent ARGS=\"your prompt\""; exit 2)
	$(COMPOSE) --profile agent run --rm dev-agent "$(ARGS)"

.PHONY: agent-shell
agent-shell: env ## interactive bash shell INSIDE the sandboxed dev-agent container
	$(COMPOSE) --profile agent run --rm --entrypoint /bin/bash dev-agent

.PHONY: security-scan
security-scan: ## run OWASP ZAP baseline against the running app
	@mkdir -p reports/zap
	$(COMPOSE) --profile security run --rm zap
	@echo "Report: reports/zap/baseline.html"

.PHONY: test
test: ## run pytest inside the app container
	$(COMPOSE) exec app pytest -q

.PHONY: lint
lint: ## ruff lint
	$(COMPOSE) exec app ruff check shield scripts tests

.PHONY: shell
shell: ## bash inside the app container
	$(COMPOSE) exec app /bin/bash
