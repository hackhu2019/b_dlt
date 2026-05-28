from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "summarize.py"
SPEC = importlib.util.spec_from_file_location("summarize", SCRIPT_PATH)
summarize = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = summarize
SPEC.loader.exec_module(summarize)


def write_transcript(
    path: Path,
    *,
    bvid: str,
    creator_mid: str,
    title: str,
    text: str,
) -> None:
    payload = {
        "bvid": bvid,
        "source_type": "subtitle",
        "provider": "local-subtitle",
        "language": "zh-CN",
        "text": text,
        "segments": [{"index": 0, "start": 0.0, "end": 1.0, "text": text.splitlines()[0]}],
        "metadata": {
            "creator_mid": creator_mid,
            "title": title,
            "url": "https://www.bilibili.com/video/{}".format(bvid),
            "published_at_iso": "2026-05-27T00:00:00+00:00",
            "duration_seconds": 120,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_load_transcript_records_reads_metadata(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    write_transcript(
        raw_dir / "BV1xx411c7mD.json",
        bvid="BV1xx411c7mD",
        creator_mid="123",
        title="第一条视频",
        text="第一段内容。\n\n第二段内容。",
    )

    records = summarize.load_transcript_records(raw_dir)

    assert len(records) == 1
    assert records[0].bvid == "BV1xx411c7mD"
    assert records[0].creator_mid == "123"
    assert records[0].title == "第一条视频"
    assert records[0].url == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_build_clean_transcript_markdown_contains_frontmatter() -> None:
    record = summarize.TranscriptRecord(
        bvid="BV1xx411c7mD",
        creator_mid="123",
        title="第一条视频",
        text="第一段内容。\n\n第二段内容。",
        source_type="subtitle",
        provider="local-subtitle",
        language="zh-CN",
        published_at_iso="2026-05-27T00:00:00+00:00",
        duration_seconds=120,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        segments=[],
        audio_path=None,
        subtitle_path="data/subtitles/BV1xx411c7mD.json",
        raw_path=Path("data/transcripts/raw/BV1xx411c7mD.json"),
    )

    markdown = summarize.build_clean_transcript_markdown(record)

    assert 'type: "clean_transcript"' in markdown
    assert "# 第一条视频" in markdown
    assert "## Overview" in markdown
    assert "## 第一段内容" in markdown or "## 第一段内容。" in markdown


def test_build_video_summary_markdown_contains_outline_and_keywords() -> None:
    record = summarize.TranscriptRecord(
        bvid="BV1xx411c7mD",
        creator_mid="123",
        title="第一条视频",
        text="这是第一句。这是第二句。这里讨论知识管理工具。",
        source_type="asr",
        provider="openai",
        language="zh-CN",
        published_at_iso=None,
        duration_seconds=120,
        url=None,
        segments=[],
        audio_path=None,
        subtitle_path=None,
        raw_path=Path("data/transcripts/raw/BV1xx411c7mD.json"),
    )

    markdown = summarize.build_video_summary_markdown(record)

    assert 'type: "video_summary"' in markdown
    assert "## Outline" in markdown
    assert "## Keywords" in markdown
    assert "知识管理工具" in markdown


def test_run_summarization_writes_all_outputs(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "clean"
    video_summary_dir = tmp_path / "videos"
    creator_summary_dir = tmp_path / "creators"
    raw_dir.mkdir()

    write_transcript(
        raw_dir / "BV1xx411c7mD.json",
        bvid="BV1xx411c7mD",
        creator_mid="123",
        title="第一条视频",
        text="第一条视频讲知识管理。\n\n然后介绍归档方法。",
    )
    write_transcript(
        raw_dir / "BV2xx411c7mD.json",
        bvid="BV2xx411c7mD",
        creator_mid="123",
        title="第二条视频",
        text="第二条视频讲工作流自动化。\n\n然后介绍脚本串联。",
    )

    summary = summarize.run_summarization(
        raw_dir,
        clean_dir,
        video_summary_dir,
        creator_summary_dir,
    )

    assert summary.transcript_count == 2
    assert summary.clean_written == 2
    assert summary.video_summary_written == 2
    assert summary.creator_summary_written == 1
    assert (clean_dir / "BV1xx411c7mD.md").is_file()
    assert (video_summary_dir / "BV2xx411c7mD.md").is_file()
    assert (creator_summary_dir / "creator_123.md").is_file()

    creator_markdown = (creator_summary_dir / "creator_123.md").read_text(encoding="utf-8")
    assert "## Videos" in creator_markdown
    assert "`BV1xx411c7mD` 第一条视频" in creator_markdown
    assert "`BV2xx411c7mD` 第二条视频" in creator_markdown


def test_run_summarization_raises_when_raw_dir_is_empty(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    with pytest.raises(summarize.SummarizeError):
        summarize.run_summarization(
            raw_dir,
            tmp_path / "clean",
            tmp_path / "videos",
            tmp_path / "creators",
        )
