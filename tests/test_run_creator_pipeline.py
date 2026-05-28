from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "run_creator_pipeline.py"
SPEC = importlib.util.spec_from_file_location("run_creator_pipeline", SCRIPT_PATH)
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = pipeline
SPEC.loader.exec_module(pipeline)


def write_manifest(path: Path, creator_mid: str, videos: list[dict]) -> None:
    payload = {"creator_mid": creator_mid, "videos": videos}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_collect_subtitle_tasks_deduplicates_and_skips_existing(tmp_path: Path) -> None:
    manifest_path = tmp_path / "creator_123.json"
    subtitle_dir = tmp_path / "subtitles"
    subtitle_dir.mkdir()
    write_manifest(
        manifest_path,
        "123",
        [
            {
                "bvid": "BV1xx411c7mD",
                "title": "one",
                "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            },
            {
                "bvid": "BV2xx411c7mD",
                "title": "two",
                "url": "https://www.bilibili.com/video/BV2xx411c7mD",
            },
            {
                "bvid": "BV2xx411c7mD",
                "title": "two duplicate",
                "url": "https://www.bilibili.com/video/BV2xx411c7mD",
            },
        ],
    )
    (subtitle_dir / "BV2xx411c7mD.ai-zh.srt").write_text("done", encoding="utf-8")

    tasks, skipped_existing = pipeline.collect_subtitle_tasks(manifest_path, subtitle_dir)

    assert skipped_existing == 1
    assert [task.bvid for task in tasks] == ["BV1xx411c7mD"]


def test_build_subtitle_download_command_includes_auth(tmp_path: Path) -> None:
    task = pipeline.SubtitleTask(
        bvid="BV1xx411c7mD",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        title="video",
    )
    cookies_file = tmp_path / "bili.cookies.txt"
    cookies_file.write_text("", encoding="utf-8")
    auth_config = pipeline.resolve_auth_config(cookies_file=cookies_file)

    command = pipeline.build_subtitle_download_command(
        task,
        tmp_path / "subtitles",
        sub_langs="ai-zh",
        sub_format="srt",
        auth_config=auth_config,
    )

    assert command[:5] == [
        "yt-dlp",
        "--ignore-config",
        "--skip-download",
        "--write-subs",
        "--sub-langs",
    ]
    assert "--sub-format" in command
    assert "--cookies" in command
    assert str(cookies_file) in command
    assert command[-1] == task.url


def test_run_subtitle_downloads_invokes_runner_for_pending_tasks(tmp_path: Path) -> None:
    manifest_path = tmp_path / "creator_123.json"
    subtitle_dir = tmp_path / "subtitles"
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
    calls: list[list[str]] = []

    def fake_runner(command, check=False, capture_output=True, text=True):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def fake_which(name: str) -> str:
        return "/usr/bin/{}".format(name)

    summary = pipeline.run_subtitle_downloads(
        manifest_path,
        subtitle_dir,
        sub_langs="ai-zh",
        sub_format="srt",
        runner=fake_runner,
        which=fake_which,
    )

    assert summary.total_tasks == 1
    assert summary.downloaded == 1
    assert summary.failed == 0
    assert calls[0][-1] == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_run_pipeline_invokes_stages_in_order(monkeypatch, tmp_path: Path) -> None:
    call_order: list[str] = []

    def fake_build_manifest(creator_mid, *, page_size, order, timeout, auth_config):
        manifest_meta = {
            "creator_mid": creator_mid,
            "page_size": page_size,
            "order": order,
            "timeout": timeout,
            "auth_mode": auth_config.mode,
        }
        call_order.append("manifest")
        return {
            "creator_mid": creator_mid,
            "video_count": 1,
            "videos": [
                {
                    "bvid": "BV1xx411c7mD",
                    "title": "video",
                    "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                }
            ],
            "meta": manifest_meta,
        }

    def fake_write_manifest(path: Path, manifest: dict) -> None:
        call_order.append("write_manifest")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def fake_run_downloads(manifest_path, output_dir, archive_file, *, auth_config, runner, which):
        call_order.append("audio")
        assert manifest_path.name == "creator_123.json"
        assert output_dir == tmp_path / "workspace" / "audio"
        assert archive_file == tmp_path / "workspace" / "manifests" / "downloaded.txt"
        assert auth_config.mode == "cookies_file"
        return pipeline.download_audio.DownloadSummary(
            total_tasks=1,
            skipped_existing=0,
            downloaded=1,
            failed=0,
        )

    def fake_run_subtitle_downloads(
        manifest_path,
        output_dir,
        *,
        sub_langs,
        sub_format,
        auth_config,
        runner,
        which,
    ):
        call_order.append("subtitle")
        assert manifest_path.name == "creator_123.json"
        assert output_dir == tmp_path / "workspace" / "subtitles"
        assert sub_langs == "ai-zh"
        assert sub_format == "srt"
        assert auth_config.mode == "cookies_file"
        return pipeline.SubtitleSummary(
            total_tasks=1,
            skipped_existing=0,
            downloaded=1,
            failed=0,
        )

    def fake_run_transcription_stage(**kwargs):
        call_order.append("transcribe")
        assert kwargs["audio_dir"] == tmp_path / "workspace" / "audio"
        assert kwargs["subtitle_dir"] == tmp_path / "workspace" / "subtitles"
        assert kwargs["output_dir"] == tmp_path / "workspace" / "transcripts" / "raw"
        return pipeline.transcribe.TranscriptionSummary(
            total_tasks=1,
            skipped_existing=0,
            subtitle_success=1,
            asr_success=0,
            failed=0,
        )

    def fake_run_summarization(raw_dir, clean_dir, video_summary_dir, creator_summary_dir, *, overwrite):
        call_order.append("summarize")
        assert raw_dir == tmp_path / "workspace" / "transcripts" / "raw"
        assert clean_dir == tmp_path / "workspace" / "transcripts" / "clean"
        assert video_summary_dir == tmp_path / "workspace" / "summaries" / "videos"
        assert creator_summary_dir == tmp_path / "workspace" / "summaries" / "creators"
        assert overwrite is False
        return pipeline.summarize.SummarizeSummary(
            transcript_count=1,
            clean_written=1,
            video_summary_written=1,
            creator_summary_written=1,
        )

    def fake_run_build_index(clean_dir, video_summary_dir, creator_summary_dir, db_path, *, query, limit):
        call_order.append("index")
        assert clean_dir == tmp_path / "workspace" / "transcripts" / "clean"
        assert video_summary_dir == tmp_path / "workspace" / "summaries" / "videos"
        assert creator_summary_dir == tmp_path / "workspace" / "summaries" / "creators"
        assert db_path == tmp_path / "knowledge.db"
        assert query == "海鸥"
        assert limit == 5
        return pipeline.build_index.BuildIndexSummary(documents_indexed=3, query_results=1)

    monkeypatch.setattr(pipeline.fetch_manifest, "build_manifest", fake_build_manifest)
    monkeypatch.setattr(pipeline.fetch_manifest, "write_manifest", fake_write_manifest)
    monkeypatch.setattr(pipeline.download_audio, "run_downloads", fake_run_downloads)
    monkeypatch.setattr(pipeline, "run_subtitle_downloads", fake_run_subtitle_downloads)
    monkeypatch.setattr(pipeline, "run_transcription_stage", fake_run_transcription_stage)
    monkeypatch.setattr(pipeline.summarize, "run_summarization", fake_run_summarization)
    monkeypatch.setattr(pipeline.build_index, "run_build_index", fake_run_build_index)

    cookies_file = tmp_path / "bili.cookies.txt"
    cookies_file.write_text("", encoding="utf-8")
    args = pipeline.build_parser().parse_args(
        [
            "--creator-mid",
            "123",
            "--workspace-root",
            str(tmp_path / "workspace"),
            "--db-path",
            str(tmp_path / "knowledge.db"),
            "--cookies-file",
            str(cookies_file),
            "--query",
            "海鸥",
            "--limit",
            "5",
        ]
    )

    summary = pipeline.run_pipeline(args)

    assert summary.manifest_video_count == 1
    assert summary.audio_summary.downloaded == 1
    assert summary.subtitle_summary.downloaded == 1
    assert summary.transcription_summary.subtitle_success == 1
    assert summary.summarize_summary.video_summary_written == 1
    assert summary.index_summary.documents_indexed == 3
    assert call_order == [
        "manifest",
        "write_manifest",
        "audio",
        "subtitle",
        "transcribe",
        "summarize",
        "index",
    ]
