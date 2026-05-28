from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "download_audio.py"
SPEC = importlib.util.spec_from_file_location("download_audio", SCRIPT_PATH)
download_audio = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = download_audio
SPEC.loader.exec_module(download_audio)
bilibili_auth = download_audio


def write_manifest(path: Path, creator_mid: str, videos: list[dict]) -> None:
    payload = {"creator_mid": creator_mid, "videos": videos}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_collect_download_tasks_deduplicates_and_skips_existing(tmp_path: Path) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    output_dir = tmp_path / "audio"
    output_dir.mkdir()

    write_manifest(
        manifests_dir / "creator_1.json",
        "1",
        [
            {"bvid": "BV1xx411c7mD", "title": "one", "url": "https://www.bilibili.com/video/BV1xx411c7mD"},
            {"bvid": "BV2xx411c7mD", "title": "two", "url": "https://www.bilibili.com/video/BV2xx411c7mD"},
        ],
    )
    write_manifest(
        manifests_dir / "creator_2.json",
        "2",
        [
            {"bvid": "BV2xx411c7mD", "title": "two duplicate", "url": "https://www.bilibili.com/video/BV2xx411c7mD"},
            {"bvid": "BV3xx411c7mD", "title": "three", "url": "https://www.bilibili.com/video/BV3xx411c7mD"},
        ],
    )
    (output_dir / "BV2xx411c7mD.m4a").write_text("existing", encoding="utf-8")
    (output_dir / "BV2xx411c7mD.info.json").write_text("{}", encoding="utf-8")

    tasks, skipped_existing = download_audio.collect_download_tasks(manifests_dir, output_dir)

    assert skipped_existing == 1
    assert [task.bvid for task in tasks] == ["BV1xx411c7mD", "BV3xx411c7mD"]


def test_build_download_command_includes_archive_output_and_cookies(tmp_path: Path) -> None:
    task = download_audio.DownloadTask(
        bvid="BV1xx411c7mD",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        title="video",
        creator_mid="123",
    )
    output_dir = tmp_path / "audio"
    archive_file = tmp_path / "downloaded.txt"
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("", encoding="utf-8")
    auth_config = download_audio.resolve_auth_config(cookies_file=cookies_file)

    command = download_audio.build_download_command(
        task,
        output_dir,
        archive_file,
        auth_config=auth_config,
    )

    assert command[:7] == [
        "yt-dlp",
        "--ignore-config",
        "--extract-audio",
        "--write-info-json",
        "--audio-format",
        "m4a",
        "--audio-quality",
    ]
    assert "--download-archive" in command
    assert str(archive_file) in command
    assert "--output" in command
    assert str(output_dir / "%(id)s.%(ext)s") in command
    assert "--cookies" in command
    assert str(cookies_file) in command
    assert command[-1] == task.url


def test_build_download_command_supports_browser_auth() -> None:
    task = download_audio.DownloadTask(
        bvid="BV1xx411c7mD",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        title="video",
        creator_mid="123",
    )
    auth_config = download_audio.resolve_auth_config(auth_mode="browser", browser="chrome")

    command = download_audio.build_download_command(
        task,
        Path("data/audio"),
        Path("data/manifests/downloaded.txt"),
        auth_config=auth_config,
    )

    assert "--cookies-from-browser" in command
    assert "chrome" in command
    assert command[-1] == task.url


def test_resolve_auth_config_prefers_explicit_cli_values_over_env(monkeypatch, tmp_path: Path) -> None:
    env_cookies_file = tmp_path / "env.cookies.txt"
    cli_cookies_file = tmp_path / "cli.cookies.txt"
    env_cookies_file.write_text("", encoding="utf-8")
    cli_cookies_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("BILIBILI_AUTH_MODE", "browser")
    monkeypatch.setenv("BILIBILI_BROWSER", "chrome")

    config = download_audio.resolve_auth_config(cookies_file=cli_cookies_file)

    assert config.mode == "cookies_file"
    assert config.cookies_file == cli_cookies_file


def test_resolve_auth_config_requires_existing_cookies_file(tmp_path: Path) -> None:
    with pytest.raises(download_audio.BilibiliAuthError):
        download_audio.resolve_auth_config(cookies_file=tmp_path / "missing.cookies.txt")


def test_run_downloads_invokes_runner_for_pending_tasks(tmp_path: Path) -> None:
    manifest_path = tmp_path / "creator_123.json"
    output_dir = tmp_path / "audio"
    archive_file = tmp_path / "downloaded.txt"
    write_manifest(
        manifest_path,
        "123",
        [
            {"bvid": "BV1xx411c7mD", "title": "one", "url": "https://www.bilibili.com/video/BV1xx411c7mD"},
            {"bvid": "BV2xx411c7mD", "title": "two", "url": "https://www.bilibili.com/video/BV2xx411c7mD"},
        ],
    )
    output_dir.mkdir()
    (output_dir / "BV2xx411c7mD.m4a").write_text("existing", encoding="utf-8")
    (output_dir / "BV2xx411c7mD.info.json").write_text("{}", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_runner(command, check=False, capture_output=True, text=True):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def fake_which(name: str) -> str:
        return "/usr/bin/{}".format(name)

    summary = download_audio.run_downloads(
        manifest_path,
        output_dir,
        archive_file,
        runner=fake_runner,
        which=fake_which,
    )

    assert summary.total_tasks == 1
    assert summary.skipped_existing == 1
    assert summary.downloaded == 1
    assert summary.failed == 0
    assert len(calls) == 1
    assert calls[0][-1] == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_collect_download_tasks_requires_info_json_for_skip(tmp_path: Path) -> None:
    manifest_path = tmp_path / "creator_123.json"
    output_dir = tmp_path / "audio"
    output_dir.mkdir()
    write_manifest(
        manifest_path,
        "123",
        [
            {
                "bvid": "BV1xx411c7mD",
                "title": "one",
                "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            }
        ],
    )
    (output_dir / "BV1xx411c7mD.m4a").write_text("existing", encoding="utf-8")

    tasks, skipped_existing = download_audio.collect_download_tasks(manifest_path, output_dir)

    assert skipped_existing == 0
    assert len(tasks) == 1


def test_run_downloads_raises_when_required_binaries_are_missing(tmp_path: Path) -> None:
    manifest_path = tmp_path / "creator_123.json"
    write_manifest(manifest_path, "123", [])

    def fake_which(name: str):
        if name == "yt-dlp":
            return None
        return "/usr/bin/{}".format(name)

    with pytest.raises(download_audio.DownloadAudioError):
        download_audio.run_downloads(
            manifest_path,
            tmp_path / "audio",
            tmp_path / "downloaded.txt",
            which=fake_which,
        )
