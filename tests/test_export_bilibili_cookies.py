from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "export_bilibili_cookies.py"
SPEC = importlib.util.spec_from_file_location("export_bilibili_cookies", SCRIPT_PATH)
export_bilibili_cookies = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = export_bilibili_cookies
SPEC.loader.exec_module(export_bilibili_cookies)


def test_resolve_yt_dlp_python_reads_shebang_from_cli(monkeypatch, tmp_path: Path) -> None:
    fake_yt_dlp = tmp_path / "yt-dlp"
    fake_yt_dlp.write_text(
        "#!/tmp/embedded-python\nfrom yt_dlp import main\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(export_bilibili_cookies.importlib.util, "find_spec", lambda name: None)

    resolved = export_bilibili_cookies.resolve_yt_dlp_python(
        which=lambda name: str(fake_yt_dlp) if name == "yt-dlp" else None
    )

    assert resolved == "/tmp/embedded-python"


def test_build_helper_command_contains_expected_arguments(tmp_path: Path) -> None:
    output_file = tmp_path / "bili.cookies.txt"

    command = export_bilibili_cookies.build_helper_command(
        "/tmp/embedded-python",
        browser="chrome",
        output_file=output_file,
        profile="Profile 1",
        keyring="kwallet",
        container=None,
    )

    assert command[:3] == ["/tmp/embedded-python", "-c", export_bilibili_cookies.HELPER_CODE]
    assert command[3:] == [
        "chrome",
        str(output_file),
        "Profile 1",
        "kwallet",
        "",
    ]


def test_run_export_creates_output_file_and_returns_summary(tmp_path: Path) -> None:
    output_file = tmp_path / "secret" / "bili.cookies.txt"
    fake_yt_dlp = tmp_path / "yt-dlp"
    fake_yt_dlp.write_text(
        "#!/tmp/embedded-python\nfrom yt_dlp import main\n",
        encoding="utf-8",
    )

    def fake_runner(command, check=False, capture_output=True, text=True):
        Path(command[4]).write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout='{"count": 2}\n', stderr="")

    summary = export_bilibili_cookies.run_export(
        output_file,
        browser="chrome",
        overwrite=True,
        runner=fake_runner,
        which=lambda name: str(fake_yt_dlp) if name == "yt-dlp" else None,
    )

    assert summary.output_file == output_file
    assert summary.browser == "chrome"
    assert summary.cookie_count == 2
    assert output_file.is_file()
    assert stat.S_IMODE(output_file.stat().st_mode) == 0o600


def test_run_export_rejects_existing_file_without_overwrite(tmp_path: Path) -> None:
    output_file = tmp_path / "bili.cookies.txt"
    output_file.write_text("existing", encoding="utf-8")

    with pytest.raises(export_bilibili_cookies.ExportCookiesError):
        export_bilibili_cookies.run_export(
            output_file,
            browser="chrome",
            which=lambda name: "/opt/homebrew/bin/yt-dlp",
        )


def test_parse_cookie_count_raises_on_invalid_payload() -> None:
    with pytest.raises(export_bilibili_cookies.ExportCookiesError):
        export_bilibili_cookies.parse_cookie_count("not-json\n")


def test_run_export_raises_when_helper_fails(tmp_path: Path) -> None:
    output_file = tmp_path / "bili.cookies.txt"

    def fake_runner(command, check=False, capture_output=True, text=True):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    with pytest.raises(export_bilibili_cookies.ExportCookiesError):
        export_bilibili_cookies.run_export(
            output_file,
            browser="chrome",
            overwrite=True,
            runner=fake_runner,
            which=lambda name: "/opt/homebrew/bin/yt-dlp",
        )
