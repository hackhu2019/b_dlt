PYTHON ?= python

.PHONY: check test help-cli verify

check:
	$(PYTHON) -m compileall scripts tests

test:
	pytest

help-cli:
	$(PYTHON) scripts/export_bilibili_cookies.py --help
	$(PYTHON) scripts/fetch_manifest.py --help
	$(PYTHON) scripts/download_audio.py --help
	$(PYTHON) scripts/run_creator_pipeline.py --help
	$(PYTHON) scripts/transcribe.py --help
	$(PYTHON) scripts/summarize.py --help
	$(PYTHON) scripts/build_index.py --help

verify: check test help-cli
