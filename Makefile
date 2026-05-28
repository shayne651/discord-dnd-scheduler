VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: run setup clean docker-up docker-down docker-logs docker-build

# ── Local (venv) ──────────────────────────────────────────────────────────────

run: .env $(VENV)/bin/activate
	@$(PYTHON) bot.py; $(MAKE) deactivate

.env:
	$(error Missing .env file — copy .env.example and fill it in)

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install -r requirements.txt

.PHONY: deactivate
deactivate:
	@echo "Bot stopped."

clean:
	rm -rf $(VENV) __pycache__

# ── Docker ────────────────────────────────────────────────────────────────────

docker-up: .env
	@touch dnd_schedule.db
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

docker-build: .env
	@touch dnd_schedule.db
	docker compose up -d --build
