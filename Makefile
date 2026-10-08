PYTHON ?= python3
HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: install run test browser-test check
install: .venv/.installed

.venv/.installed: requirements.txt
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements.txt
	touch $@

run: install
	.venv/bin/python -m uvicorn app.main:app --host $(HOST) --port $(PORT) --reload

test: install
	.venv/bin/python -m pytest -q

check: test
	.venv/bin/python -m compileall -q app
	node --check app/static/app.js

browser-test: install
	.venv/bin/python -m pip install playwright
	.venv/bin/python -m playwright install chromium
	BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_browser.py -q
