"""Run the full Bilibili creator pipeline into a local knowledge workspace."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_index
import download_audio
import export_bilibili_cookies
import fetch_manifest
import summarize
import transcribe
from bilibili_auth import (
    AUTH_MODE_BROWSER,
    AUTH_MODES,
    BilibiliAuthConfig,
    BilibiliAuthError,
    add_auth_arguments,
    build_yt_dlp_auth_args,
    load_dotenv,
    resolve_auth_config,
)


DEFAULT_WORKSPACE_ROOT = Path("data")
DEFAULT_DB_PATH = Path("db/knowledge.db")
DEFAULT_SUBTITLE_LANGS = "ai-zh"
DEFAULT_SUBTITLE_FORMAT = "srt"
SUPPORTED_SUBTITLE_EXTENSIONS = {".json", ".srt", ".vtt", ".txt"}
Runner = Callable[..., subprocess.CompletedProcess]
Which = Callable[[str], Optional[str]]


class CreatorPipelineError(RuntimeError):
    """Raised when the pipeline configuration or orchestration is invalid."""


@dataclass(frozen=True)
class PipelinePaths:
    workspace_root: Path
    manifests_dir: Path
    manifest_path: Path
    archive_file: Path
    subtitle_dir: Path
    audio_dir: Path
    raw_transcript_dir: Path
    clean_transcript_dir: Path
    video_summary_dir: Path
    creator_summary_dir: Path
    exports_dir: Path


@dataclass(frozen=True)
class SubtitleTask:
    bvid: str
    url: str
    title: str


@dataclass(frozen=True)
class SubtitleSummary:
    total_tasks: int
    skipped_existing: int
    downloaded: int
    failed: int


@dataclass(frozen=True)
class PipelineSummary:
    manifest_video_count: int
    audio_summary: download_audio.DownloadSummary
    subtitle_summary: SubtitleSummary
    transcription_summary: transcribe.TranscriptionSummary
    summarize_summary: summarize.SummarizeSummary
    index_summary: build_index.BuildIndexSummary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the full Bilibili creator pipeline into a local knowledge workspace."
    )
    parser.add_argument(
        "--creator-mid",
        required=True,
        type=fetch_manifest.parse_creator_mid,
        help="Bilibili creator MID.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=DEFAULT_WORKSPACE_ROOT,
        help="Workspace root for manifests, audio, subtitles, transcripts and summaries.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path for the generated local knowledge index.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=30,
        help="Number of videos requested per page when fetching the manifest.",
    )
    parser.add_argument(
        "--order",
        choices=("pubdate", "click", "stow"),
        default="pubdate",
        help="Bilibili sort order for creator videos.",
    )
    parser.add_argument(
        "--manifest-timeout",
        type=float,
        default=fetch_manifest.DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds for manifest requests.",
    )
    parser.add_argument(
        "--sub-langs",
        default=DEFAULT_SUBTITLE_LANGS,
        help="Subtitle language codes passed to yt-dlp --sub-langs.",
    )
    parser.add_argument(
        "--sub-format",
        default=DEFAULT_SUBTITLE_FORMAT,
        help="Subtitle format passed to yt-dlp --sub-format.",
    )
    parser.add_argument(
        "--transcribe-provider",
        choices=("openai",),
        default=transcribe.DEFAULT_PROVIDER,
        help="ASR provider used when subtitles are unavailable.",
    )
    parser.add_argument(
        "--transcribe-model",
        default=os.getenv("OPENAI_TRANSCRIBE_MODEL", transcribe.DEFAULT_OPENAI_MODEL),
        help="ASR model used when subtitles are unavailable.",
    )
    parser.add_argument(
        "--transcribe-language",
        default=os.getenv("DEFAULT_LANGUAGE", transcribe.DEFAULT_LANGUAGE),
        help="Language hint passed to the ASR provider.",
    )
    parser.add_argument(
        "--transcribe-prompt",
        help="Optional prompt passed to the ASR provider.",
    )
    parser.add_argument(
        "--transcribe-timeout",
        type=float,
        default=transcribe.DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds for ASR requests.",
    )
    parser.add_argument(
        "--overwrite-transcripts",
        action="store_true",
        help="Overwrite existing raw transcript files.",
    )
    parser.add_argument(
        "--overwrite-summaries",
        action="store_true",
        help="Overwrite existing clean transcript and summary markdown files.",
    )
    parser.add_argument(
        "--query",
        help="Optional search query to run after rebuilding the local index.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of search results printed for --query.",
    )
    parser.add_argument(
        "--export-cookies",
        action="store_true",
        help="Export browser cookies into a reusable file before running the pipeline.",
    )
    parser.add_argument(
        "--cookies-output-file",
        type=Path,
        help="Destination Netscape-format cookies file used by --export-cookies.",
    )
    parser.add_argument(
        "--browser-profile",
        help="Optional browser profile name or path used by --export-cookies.",
    )
    parser.add_argument(
        "--browser-keyring",
        help="Optional browser keyring name used by --export-cookies.",
    )
    parser.add_argument(
        "--browser-container",
        help="Optional Firefox container name used by --export-cookies.",
    )
    parser.add_argument(
        "--overwrite-cookies-file",
        action="store_true",
        help="Overwrite an existing cookies file when --export-cookies is enabled.",
    )
    add_auth_arguments(parser, supported_modes=AUTH_MODES)
    return parser


def build_pipeline_paths(creator_mid: str, workspace_root: Path) -> PipelinePaths:
    root = workspace_root.expanduser()
    manifests_dir = root / "manifests"
    return PipelinePaths(
        workspace_root=root,
        manifests_dir=manifests_dir,
        manifest_path=manifests_dir / "creator_{}.json".format(creator_mid),
        archive_file=manifests_dir / "downloaded.txt",
        subtitle_dir=root / "subtitles",
        audio_dir=root / "audio",
        raw_transcript_dir=root / "transcripts" / "raw",
        clean_transcript_dir=root / "transcripts" / "clean",
        video_summary_dir=root / "summaries" / "videos",
        creator_summary_dir=root / "summaries" / "creators",
        exports_dir=root / "exports",
    )


def resolve_cookies_output_file(
    configured_path: Optional[Path],
    paths: PipelinePaths,
) -> Path:
    if configured_path is not None:
        return configured_path.expanduser()
    return paths.exports_dir / "bilibili.cookies.txt"


def resolve_pipeline_auth_configs(
    *,
    auth_mode: Optional[str],
    browser: Optional[str],
    cookies_file: Optional[Path],
    cookie_header: Optional[str],
) -> tuple[BilibiliAuthConfig, BilibiliAuthConfig]:
    download_auth = resolve_auth_config(
        auth_mode=auth_mode,
        browser=browser,
        cookies_file=cookies_file,
        cookie_header=cookie_header,
        supported_modes=AUTH_MODES,
    )
    manifest_auth_mode = auth_mode
    if manifest_auth_mode == AUTH_MODE_BROWSER:
        manifest_auth_mode = None
    manifest_auth = resolve_auth_config(
        auth_mode=manifest_auth_mode,
        browser=None,
        cookies_file=cookies_file,
        cookie_header=cookie_header,
        supported_modes=fetch_manifest.SUPPORTED_AUTH_MODES,
    )
    return manifest_auth, download_auth


def has_existing_subtitle(output_dir: Path, bvid: str) -> bool:
    if not output_dir.is_dir():
        return False
    for path in output_dir.glob("{}.*".format(bvid)):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUBTITLE_EXTENSIONS:
            return True
    return False


def collect_subtitle_tasks(manifest_path: Path, output_dir: Path) -> tuple[List[SubtitleTask], int]:
    tasks: List[SubtitleTask] = []
    skipped_existing = 0
    seen_bvids = set()

    for path in download_audio.iter_manifest_files(manifest_path):
        manifest = download_audio.load_json_file(path)
        creator_mid = str(manifest.get("creator_mid", "")).strip()
        videos = manifest.get("videos")
        if not creator_mid:
            raise CreatorPipelineError("Manifest {} is missing creator_mid.".format(path))
        if not isinstance(videos, list):
            raise CreatorPipelineError(
                "Manifest {} is missing a valid videos list.".format(path)
            )

        for video in videos:
            if not isinstance(video, dict):
                raise CreatorPipelineError(
                    "Manifest {} contains an invalid video record.".format(path)
                )
            download_task = download_audio.build_task(video, creator_mid, path)
            if download_task.bvid in seen_bvids:
                continue
            seen_bvids.add(download_task.bvid)
            if has_existing_subtitle(output_dir, download_task.bvid):
                skipped_existing += 1
                continue
            tasks.append(
                SubtitleTask(
                    bvid=download_task.bvid,
                    url=download_task.url,
                    title=download_task.title,
                )
            )

    return tasks, skipped_existing


def ensure_yt_dlp_binary(which: Which = shutil.which) -> None:
    if which("yt-dlp") is None:
        raise CreatorPipelineError("Missing required executable: yt-dlp.")


def build_subtitle_download_command(
    task: SubtitleTask,
    output_dir: Path,
    *,
    sub_langs: str,
    sub_format: str,
    auth_config: Optional[BilibiliAuthConfig] = None,
) -> List[str]:
    command = [
        "yt-dlp",
        "--ignore-config",
        "--skip-download",
        "--write-subs",
        "--sub-langs",
        sub_langs,
        "--sub-format",
        sub_format,
        "--no-progress",
        "--output",
        str(output_dir / download_audio.OUTPUT_TEMPLATE),
    ]
    if auth_config is not None:
        command.extend(build_yt_dlp_auth_args(auth_config))
    command.append(task.url)
    return command


def run_subtitle_downloads(
    manifest_path: Path,
    output_dir: Path,
    *,
    sub_langs: str,
    sub_format: str,
    auth_config: Optional[BilibiliAuthConfig] = None,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
) -> SubtitleSummary:
    ensure_yt_dlp_binary(which=which)
    resolved_auth = auth_config or BilibiliAuthConfig()
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks, skipped_existing = collect_subtitle_tasks(manifest_path, output_dir)

    downloaded = 0
    failed = 0
    for task in tasks:
        command = build_subtitle_download_command(
            task,
            output_dir,
            sub_langs=sub_langs,
            sub_format=sub_format,
            auth_config=resolved_auth,
        )
        print("Downloading subtitles {} {}".format(task.bvid, task.title or ""), file=sys.stderr)
        result = runner(command, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            downloaded += 1
            continue

        failed += 1
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or "yt-dlp exited with code {}".format(result.returncode)
        print("Subtitle failed {}: {}".format(task.bvid, details), file=sys.stderr)

    return SubtitleSummary(
        total_tasks=len(tasks),
        skipped_existing=skipped_existing,
        downloaded=downloaded,
        failed=failed,
    )


def run_transcription_stage(
    *,
    audio_dir: Path,
    subtitle_dir: Path,
    output_dir: Path,
    provider: str,
    model: str,
    language: Optional[str],
    prompt: Optional[str],
    timeout: float,
    overwrite: bool,
) -> transcribe.TranscriptionSummary:
    tasks, skipped_existing = transcribe.discover_transcription_tasks(
        audio_dir,
        subtitle_dir,
        output_dir,
        overwrite=overwrite,
    )
    if not tasks:
        existing_transcripts = 0
        if output_dir.is_dir():
            existing_transcripts = len(list(output_dir.glob("*.json")))
        if skipped_existing > 0 or existing_transcripts > 0:
            return transcribe.TranscriptionSummary(
                total_tasks=0,
                skipped_existing=max(skipped_existing, existing_transcripts),
                subtitle_success=0,
                asr_success=0,
                failed=0,
            )
        raise transcribe.TranscribeError("No subtitle or audio files found to transcribe.")

    return transcribe.run_transcription(
        audio_dir,
        subtitle_dir,
        output_dir,
        provider=provider,
        model=model,
        language=language,
        prompt=prompt,
        timeout=timeout,
        overwrite=overwrite,
    )


def run_pipeline(
    args: argparse.Namespace,
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
) -> PipelineSummary:
    paths = build_pipeline_paths(args.creator_mid, args.workspace_root)
    effective_auth_mode = args.auth_mode
    effective_cookies_file = args.cookies_file
    if args.export_cookies:
        cookies_output_file = resolve_cookies_output_file(args.cookies_output_file, paths)
        browser_name = args.browser or os.getenv("BILIBILI_BROWSER") or export_bilibili_cookies.DEFAULT_BROWSER
        export_summary = export_bilibili_cookies.run_export(
            cookies_output_file,
            browser=browser_name,
            profile=args.browser_profile,
            keyring=args.browser_keyring,
            container=args.browser_container,
            overwrite=args.overwrite_cookies_file,
            runner=runner,
            which=which,
        )
        print(
            "Exported {} Bilibili cookies to {}".format(
                export_summary.cookie_count,
                export_summary.output_file,
            ),
            file=sys.stderr,
        )
        effective_cookies_file = export_summary.output_file
        if effective_auth_mode in (None, AUTH_MODE_BROWSER):
            effective_auth_mode = None

    manifest_auth, download_auth = resolve_pipeline_auth_configs(
        auth_mode=effective_auth_mode,
        browser=args.browser,
        cookies_file=effective_cookies_file,
        cookie_header=args.cookie_header,
    )

    print("Fetching creator {} video manifest...".format(args.creator_mid), file=sys.stderr)
    manifest = fetch_manifest.build_manifest(
        args.creator_mid,
        page_size=args.page_size,
        order=args.order,
        timeout=args.manifest_timeout,
        auth_config=manifest_auth,
    )
    fetch_manifest.write_manifest(paths.manifest_path, manifest)
    print(
        "Wrote {} videos to {}".format(manifest["video_count"], paths.manifest_path),
        file=sys.stderr,
    )

    audio_summary = download_audio.run_downloads(
        paths.manifest_path,
        paths.audio_dir,
        paths.archive_file,
        auth_config=download_auth,
        runner=runner,
        which=which,
    )
    print(
        "Audio summary: {} downloaded, {} skipped, {} failed.".format(
            audio_summary.downloaded,
            audio_summary.skipped_existing,
            audio_summary.failed,
        ),
        file=sys.stderr,
    )

    subtitle_summary = run_subtitle_downloads(
        paths.manifest_path,
        paths.subtitle_dir,
        sub_langs=args.sub_langs,
        sub_format=args.sub_format,
        auth_config=download_auth,
        runner=runner,
        which=which,
    )
    print(
        "Subtitle summary: {} downloaded, {} skipped, {} failed.".format(
            subtitle_summary.downloaded,
            subtitle_summary.skipped_existing,
            subtitle_summary.failed,
        ),
        file=sys.stderr,
    )

    transcription_summary = run_transcription_stage(
        audio_dir=paths.audio_dir,
        subtitle_dir=paths.subtitle_dir,
        output_dir=paths.raw_transcript_dir,
        provider=args.transcribe_provider,
        model=args.transcribe_model,
        language=args.transcribe_language,
        prompt=args.transcribe_prompt,
        timeout=args.transcribe_timeout,
        overwrite=args.overwrite_transcripts,
    )
    print(
        "Transcription summary: {} subtitle, {} asr, {} skipped, {} failed.".format(
            transcription_summary.subtitle_success,
            transcription_summary.asr_success,
            transcription_summary.skipped_existing,
            transcription_summary.failed,
        ),
        file=sys.stderr,
    )

    summarize_summary = summarize.run_summarization(
        paths.raw_transcript_dir,
        paths.clean_transcript_dir,
        paths.video_summary_dir,
        paths.creator_summary_dir,
        overwrite=args.overwrite_summaries,
    )
    print(
        "Summary files: {} transcripts, {} clean, {} video summaries, {} creator summaries.".format(
            summarize_summary.transcript_count,
            summarize_summary.clean_written,
            summarize_summary.video_summary_written,
            summarize_summary.creator_summary_written,
        ),
        file=sys.stderr,
    )

    index_summary = build_index.run_build_index(
        paths.clean_transcript_dir,
        paths.video_summary_dir,
        paths.creator_summary_dir,
        args.db_path.expanduser(),
        query=args.query,
        limit=args.limit,
    )
    print("Indexed {} documents.".format(index_summary.documents_indexed), file=sys.stderr)

    return PipelineSummary(
        manifest_video_count=int(manifest["video_count"]),
        audio_summary=audio_summary,
        subtitle_summary=subtitle_summary,
        transcription_summary=transcription_summary,
        summarize_summary=summarize_summary,
        index_summary=index_summary,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.page_size <= 0:
        parser.error("--page-size must be greater than 0.")
    if args.manifest_timeout <= 0:
        parser.error("--manifest-timeout must be greater than 0.")
    if args.transcribe_timeout <= 0:
        parser.error("--transcribe-timeout must be greater than 0.")
    if args.limit <= 0:
        parser.error("--limit must be greater than 0.")
    if not str(args.sub_langs).strip():
        parser.error("--sub-langs must not be empty.")
    if not str(args.sub_format).strip():
        parser.error("--sub-format must not be empty.")

    try:
        summary = run_pipeline(args)
    except (
        BilibiliAuthError,
        CreatorPipelineError,
        RuntimeError,
        fetch_manifest.BilibiliAPIError,
        download_audio.DownloadAudioError,
        export_bilibili_cookies.ExportCookiesError,
        summarize.SummarizeError,
        transcribe.TranscribeError,
        build_index.BuildIndexError,
        sqlite3.Error,
    ) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    has_failures = (
        summary.audio_summary.failed > 0
        or summary.subtitle_summary.failed > 0
        or summary.transcription_summary.failed > 0
    )
    return 0 if not has_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
