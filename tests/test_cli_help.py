from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [
    "export_bilibili_cookies.py",
    "fetch_manifest.py",
    "download_audio.py",
    "run_creator_pipeline.py",
    "transcribe.py",
    "summarize.py",
    "build_index.py",
]


def test_cli_help_commands_exit_successfully() -> None:
    for script_name in SCRIPTS:
        script_path = ROOT / "scripts" / script_name
        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "usage:" in result.stdout
