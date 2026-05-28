"""Download or extract audio files for videos listed in manifest files."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bilibili_auth import (
    AUTH_MODES,
    BilibiliAuthConfig,
    BilibiliAuthError,
    add_auth_arguments,
    build_yt_dlp_auth_args,
    load_dotenv,
    resolve_auth_config,
)


DEFAULT_MANIFEST = Path("data/manifests")
DEFAULT_AUDIO_DIR = Path("data/audio")
DEFAULT_ARCHIVE_FILE = Path("data/manifests/downloaded.txt")
DEFAULT_AUDIO_FORMAT = "m4a"
OUTPUT_TEMPLATE = "%(id)s.%(ext)s"
JSONDict = Dict[str, Any]
Runner = Callable[..., subprocess.CompletedProcess]
Which = Callable[[str], Optional[str]]


class DownloadAudioError(RuntimeError):
    """Raised when manifest loading or command preparation fails."""


@dataclass
class DownloadTask:
    bvid: str
    url: str
    title: str
    creator_mid: str


@dataclass
class DownloadSummary:
    total_tasks: int
    skipped_existing: int
    downloaded: int
    failed: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download or extract audio files for videos listed in a manifest."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Manifest file or directory used as the download source.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_AUDIO_DIR,
        help="Directory used to store audio files.",
    )
    parser.add_argument(
        "--archive-file",
        type=Path,
        default=DEFAULT_ARCHIVE_FILE,
        help="Archive file used to skip already-downloaded items.",
    )
    add_auth_arguments(parser, supported_modes=AUTH_MODES)
    return parser


def iter_manifest_files(manifest_path: Path) -> List[Path]:
    if not manifest_path.exists():
        raise DownloadAudioError("Manifest path does not exist: {}".format(manifest_path))
    if manifest_path.is_file():
        return [manifest_path]

    manifest_files = sorted(path for path in manifest_path.glob("*.json") if path.is_file())
    if not manifest_files:
        raise DownloadAudioError("No manifest files found under {}.".format(manifest_path))
    return manifest_files


def load_json_file(path: Path) -> JSONDict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DownloadAudioError("Manifest file not found: {}".format(path)) from exc
    except json.JSONDecodeError as exc:
        raise DownloadAudioError("Invalid JSON in manifest {}: {}".format(path, exc)) from exc
    if not isinstance(payload, dict):
        raise DownloadAudioError("Manifest {} must contain a JSON object.".format(path))
    return payload


def get_video_output_path(output_dir: Path, bvid: str) -> Path:
    return output_dir / "{}.{}".format(bvid, DEFAULT_AUDIO_FORMAT)


def get_info_output_path(output_dir: Path, bvid: str) -> Path:
    return output_dir / "{}.info.json".format(bvid)


def build_task(video: Mapping[str, Any], creator_mid: str, manifest_path: Path) -> DownloadTask:
    bvid = str(video.get("bvid", "")).strip()
    if not bvid:
        raise DownloadAudioError(
            "Manifest {} contains a video without bvid.".format(manifest_path)
        )

    url = str(video.get("url", "")).strip()
    if not url:
        url = "https://www.bilibili.com/video/{}".format(bvid)

    return DownloadTask(
        bvid=bvid,
        url=url,
        title=str(video.get("title", "")).strip(),
        creator_mid=creator_mid,
    )


def collect_download_tasks(manifest_path: Path, output_dir: Path) -> tuple[List[DownloadTask], int]:
    tasks: List[DownloadTask] = []
    seen_bvids = set()
    skipped_existing = 0

    for path in iter_manifest_files(manifest_path):
        manifest = load_json_file(path)
        creator_mid = str(manifest.get("creator_mid", "")).strip()
        videos = manifest.get("videos")
        if not creator_mid:
            raise DownloadAudioError("Manifest {} is missing creator_mid.".format(path))
        if not isinstance(videos, list):
            raise DownloadAudioError("Manifest {} is missing a valid videos list.".format(path))

        for video in videos:
            if not isinstance(video, dict):
                raise DownloadAudioError(
                    "Manifest {} contains an invalid video record.".format(path)
                )
            task = build_task(video, creator_mid, path)
            if task.bvid in seen_bvids:
                continue
            seen_bvids.add(task.bvid)
            if (
                get_video_output_path(output_dir, task.bvid).exists()
                and get_info_output_path(output_dir, task.bvid).exists()
            ):
                skipped_existing += 1
                continue
            tasks.append(task)

    return tasks, skipped_existing


def ensure_required_binaries(which: Which = shutil.which) -> None:
    missing = [name for name in ("yt-dlp", "ffmpeg") if which(name) is None]
    if missing:
        raise DownloadAudioError(
            "Missing required executable(s): {}.".format(", ".join(sorted(missing)))
        )


def build_download_command(
    task: DownloadTask,
    output_dir: Path,
    archive_file: Path,
    *,
    auth_config: Optional[BilibiliAuthConfig] = None,
) -> List[str]:
    command = [
        "yt-dlp",
        "--ignore-config",
        "--extract-audio",
        "--write-info-json",
        "--audio-format",
        DEFAULT_AUDIO_FORMAT,
        "--audio-quality",
        "0",
        "--no-progress",
        "--download-archive",
        str(archive_file),
        "--output",
        str(output_dir / OUTPUT_TEMPLATE),
    ]
    if auth_config is not None:
        command.extend(build_yt_dlp_auth_args(auth_config))
    command.append(task.url)
    return command


def run_downloads(
    manifest_path: Path,
    output_dir: Path,
    archive_file: Path,
    *,
    auth_config: Optional[BilibiliAuthConfig] = None,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
) -> DownloadSummary:
    ensure_required_binaries(which=which)
    if auth_config is None:
        auth_config = BilibiliAuthConfig()

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    tasks, skipped_existing = collect_download_tasks(manifest_path, output_dir)

    downloaded = 0
    failed = 0
    for task in tasks:
        command = build_download_command(
            task,
            output_dir,
            archive_file,
            auth_config=auth_config,
        )
        print("Downloading {} {}".format(task.bvid, task.title or ""), file=sys.stderr)
        result = runner(command, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            downloaded += 1
            continue

        failed += 1
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or "yt-dlp exited with code {}".format(result.returncode)
        print("Failed {}: {}".format(task.bvid, details), file=sys.stderr)

    return DownloadSummary(
        total_tasks=len(tasks),
        skipped_existing=skipped_existing,
        downloaded=downloaded,
        failed=failed,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        auth_config = resolve_auth_config(
            auth_mode=args.auth_mode,
            browser=getattr(args, "browser", None),
            cookies_file=args.cookies_file,
            cookie_header=getattr(args, "cookie_header", None),
        )
        summary = run_downloads(
            args.manifest,
            args.output_dir,
            args.archive_file,
            auth_config=auth_config,
        )
    except (DownloadAudioError, BilibiliAuthError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    print(
        "Summary: {} downloaded, {} skipped, {} failed.".format(
            summary.downloaded,
            summary.skipped_existing,
            summary.failed,
        ),
        file=sys.stderr,
    )
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
