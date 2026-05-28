from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "transcribe.py"
SPEC = importlib.util.spec_from_file_location("transcribe", SCRIPT_PATH)
transcribe = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = transcribe
SPEC.loader.exec_module(transcribe)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class RecordingOpener:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests = []

    def __call__(self, request, timeout: float = 0) -> FakeResponse:
        self.requests.append((request, timeout))
        return FakeResponse(self.payload)


def write_subtitle_json(path: Path) -> None:
    payload = {
        "lan": "zh-CN",
        "body": [
            {"from": 0.0, "to": 1.2, "content": "第一句"},
            {"from": 1.2, "to": 2.4, "content": "第二句"},
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_discover_transcription_tasks_prefers_subtitle_and_skips_existing(
    tmp_path: Path,
) -> None:
    audio_dir = tmp_path / "audio"
    subtitle_dir = tmp_path / "subtitles"
    output_dir = tmp_path / "transcripts"
    audio_dir.mkdir()
    subtitle_dir.mkdir()
    output_dir.mkdir()

    (audio_dir / "BV1xx411c7mD.m4a").write_text("audio", encoding="utf-8")
    (audio_dir / "BV1xx411c7mD.info.json").write_text(
        json.dumps(
            {
                "title": "第一条视频",
                "webpage_url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "uploader_id": "12345",
                "duration": 120,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (audio_dir / "BV2xx411c7mD.m4a").write_text("audio", encoding="utf-8")
    write_subtitle_json(subtitle_dir / "BV1xx411c7mD.ai-zh.srt")
    (output_dir / "BV2xx411c7mD.json").write_text("done", encoding="utf-8")

    tasks, skipped_existing = transcribe.discover_transcription_tasks(
        audio_dir,
        subtitle_dir,
        output_dir,
    )

    assert skipped_existing == 1
    assert len(tasks) == 1
    assert tasks[0].bvid == "BV1xx411c7mD"
    assert tasks[0].subtitle_path is not None
    assert tasks[0].audio_path is not None
    assert tasks[0].info_path is not None


def test_build_subtitle_transcript_from_bilibili_json(tmp_path: Path) -> None:
    subtitle_path = tmp_path / "BV1xx411c7mD.json"
    write_subtitle_json(subtitle_path)
    info_path = tmp_path / "BV1xx411c7mD.info.json"
    info_path.write_text(
        json.dumps(
            {
                "title": "第一条视频",
                "webpage_url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "uploader_id": "12345",
                "duration": 180,
                "timestamp": 1700000000,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    task = transcribe.TranscriptionTask(
        bvid="BV1xx411c7mD",
        audio_path=None,
        subtitle_path=subtitle_path,
        info_path=info_path,
        output_path=tmp_path / "BV1xx411c7mD.raw.json",
    )

    transcript = transcribe.build_subtitle_transcript(task)

    assert transcript["source_type"] == "subtitle"
    assert transcript["provider"] == "local-subtitle"
    assert transcript["language"] == "zh-CN"
    assert transcript["text"] == "第一句\n第二句"
    assert len(transcript["segments"]) == 2
    assert transcript["segments"][0]["start"] == 0.0
    assert transcript["metadata"]["title"] == "第一条视频"
    assert transcript["metadata"]["creator_mid"] == "12345"
    assert transcript["metadata"]["url"] == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_transcribe_audio_with_openai_builds_request(tmp_path: Path, monkeypatch) -> None:
    audio_path = tmp_path / "BV1xx411c7mD.m4a"
    audio_path.write_bytes(b"fake audio bytes")
    task = transcribe.TranscriptionTask(
        bvid="BV1xx411c7mD",
        audio_path=audio_path,
        subtitle_path=None,
        info_path=None,
        output_path=tmp_path / "BV1xx411c7mD.json",
    )
    opener = RecordingOpener({"text": "转写结果"})
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    transcript = transcribe.transcribe_audio_with_openai(
        task,
        model="gpt-4o-transcribe",
        language="zh",
        prompt="保持术语准确",
        timeout=12.0,
        opener=opener,
    )

    request, timeout = opener.requests[0]
    body = request.data.decode("utf-8", errors="ignore")
    assert timeout == 12.0
    assert request.full_url == "https://api.openai.com/v1/audio/transcriptions"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert 'name="model"' in body
    assert "gpt-4o-transcribe" in body
    assert 'name="language"' in body
    assert 'name="prompt"' in body
    assert transcript["source_type"] == "asr"
    assert transcript["provider"] == "openai"
    assert transcript["text"] == "转写结果"


def test_run_transcription_uses_subtitle_before_asr(tmp_path: Path, monkeypatch) -> None:
    audio_dir = tmp_path / "audio"
    subtitle_dir = tmp_path / "subtitles"
    output_dir = tmp_path / "raw"
    audio_dir.mkdir()
    subtitle_dir.mkdir()

    (audio_dir / "BV1xx411c7mD.m4a").write_bytes(b"audio1")
    (audio_dir / "BV2xx411c7mD.m4a").write_bytes(b"audio2")
    write_subtitle_json(subtitle_dir / "BV1xx411c7mD.json")

    opener = RecordingOpener({"text": "第二条音频转写"})
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    summary = transcribe.run_transcription(
        audio_dir,
        subtitle_dir,
        output_dir,
        provider="openai",
        model="gpt-4o-transcribe",
        language="zh",
        prompt=None,
        timeout=8.0,
        opener=opener,
    )

    assert summary.total_tasks == 2
    assert summary.subtitle_success == 1
    assert summary.asr_success == 1
    assert summary.failed == 0
    assert len(opener.requests) == 1

    subtitle_transcript = json.loads((output_dir / "BV1xx411c7mD.json").read_text(encoding="utf-8"))
    asr_transcript = json.loads((output_dir / "BV2xx411c7mD.json").read_text(encoding="utf-8"))
    assert subtitle_transcript["source_type"] == "subtitle"
    assert asr_transcript["source_type"] == "asr"


def test_transcribe_audio_requires_api_key(tmp_path: Path, monkeypatch) -> None:
    audio_path = tmp_path / "BV1xx411c7mD.m4a"
    audio_path.write_bytes(b"audio")
    task = transcribe.TranscriptionTask(
        bvid="BV1xx411c7mD",
        audio_path=audio_path,
        subtitle_path=None,
        info_path=None,
        output_path=tmp_path / "BV1xx411c7mD.json",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(transcribe.TranscribeError):
        transcribe.transcribe_audio_with_openai(
            task,
            model="gpt-4o-transcribe",
            language="zh",
            prompt=None,
            timeout=8.0,
        )
