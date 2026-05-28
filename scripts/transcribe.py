"""Create raw transcripts by preferring subtitles and falling back to OpenAI ASR."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_AUDIO_DIR = Path("data/audio")
DEFAULT_SUBTITLE_DIR = Path("data/subtitles")
DEFAULT_TRANSCRIPT_DIR = Path("data/transcripts/raw")
DEFAULT_PROVIDER = "openai"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-transcribe"
DEFAULT_LANGUAGE = "zh"
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_OPENAI_UPLOAD_BYTES = 25 * 1024 * 1024
SUPPORTED_AUDIO_EXTENSIONS = {
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".wav",
    ".webm",
}
SUBTITLE_EXTENSION_PRIORITY = {
    ".json": 0,
    ".srt": 1,
    ".vtt": 2,
    ".txt": 3,
}
BVID_PATTERN = re.compile(r"(BV[0-9A-Za-z]+)")
JSONDict = Dict[str, Any]
Opener = Callable[..., Any]


class TranscribeError(RuntimeError):
    """Raised when subtitle parsing or ASR transcription fails."""


@dataclass
class TranscriptionTask:
    bvid: str
    audio_path: Optional[Path]
    subtitle_path: Optional[Path]
    info_path: Optional[Path]
    output_path: Path


@dataclass
class TranscriptionSummary:
    total_tasks: int
    skipped_existing: int
    subtitle_success: int
    asr_success: int
    failed: int


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create raw transcripts by preferring subtitles and falling back to ASR."
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=DEFAULT_AUDIO_DIR,
        help="Directory containing source audio files.",
    )
    parser.add_argument(
        "--subtitle-dir",
        type=Path,
        default=DEFAULT_SUBTITLE_DIR,
        help="Directory containing downloaded subtitles.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_TRANSCRIPT_DIR,
        help="Directory used to store raw transcripts.",
    )
    parser.add_argument(
        "--provider",
        choices=("openai",),
        default=DEFAULT_PROVIDER,
        help="ASR provider identifier used when subtitles are unavailable.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_TRANSCRIBE_MODEL", DEFAULT_OPENAI_MODEL),
        help="OpenAI transcription model used for ASR fallback.",
    )
    parser.add_argument(
        "--language",
        default=os.getenv("DEFAULT_LANGUAGE", DEFAULT_LANGUAGE),
        help="Hint passed to the ASR provider when language is known.",
    )
    parser.add_argument(
        "--prompt",
        help="Optional prompt passed to the ASR provider.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds for ASR requests.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing raw transcript files.",
    )
    return parser


def extract_bvid_from_filename(path: Path) -> str:
    match = BVID_PATTERN.search(path.stem)
    if match:
        return match.group(1)
    return path.name.split(".", 1)[0]


def choose_preferred_file(current: Optional[Path], candidate: Path) -> Path:
    if current is None:
        return candidate
    current_priority = SUBTITLE_EXTENSION_PRIORITY.get(current.suffix.lower(), 99)
    candidate_priority = SUBTITLE_EXTENSION_PRIORITY.get(candidate.suffix.lower(), 99)
    if candidate_priority < current_priority:
        return candidate
    if candidate_priority == current_priority and candidate.name < current.name:
        return candidate
    return current


def collect_audio_files(audio_dir: Path) -> Dict[str, Path]:
    audio_files: Dict[str, Path] = {}
    if not audio_dir.is_dir():
        return audio_files

    for path in sorted(audio_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            continue
        audio_files.setdefault(extract_bvid_from_filename(path), path)
    return audio_files


def collect_subtitle_files(subtitle_dir: Path) -> Dict[str, Path]:
    subtitle_files: Dict[str, Path] = {}
    if not subtitle_dir.is_dir():
        return subtitle_files

    for path in sorted(subtitle_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUBTITLE_EXTENSION_PRIORITY:
            continue
        bvid = extract_bvid_from_filename(path)
        subtitle_files[bvid] = choose_preferred_file(subtitle_files.get(bvid), path)
    return subtitle_files


def collect_info_files(*directories: Path) -> Dict[str, Path]:
    info_files: Dict[str, Path] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.info.json")):
            if not path.is_file():
                continue
            bvid = extract_bvid_from_filename(path)
            info_files.setdefault(bvid, path)
    return info_files


def discover_transcription_tasks(
    audio_dir: Path,
    subtitle_dir: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> tuple[List[TranscriptionTask], int]:
    audio_files = collect_audio_files(audio_dir)
    subtitle_files = collect_subtitle_files(subtitle_dir)
    info_files = collect_info_files(audio_dir, subtitle_dir)
    all_bvids = sorted(set(audio_files) | set(subtitle_files))
    tasks: List[TranscriptionTask] = []
    skipped_existing = 0

    for bvid in all_bvids:
        output_path = output_dir / "{}.json".format(bvid)
        if output_path.exists() and not overwrite:
            skipped_existing += 1
            continue
        tasks.append(
            TranscriptionTask(
                bvid=bvid,
                audio_path=audio_files.get(bvid),
                subtitle_path=subtitle_files.get(bvid),
                info_path=info_files.get(bvid),
                output_path=output_path,
            )
        )

    return tasks, skipped_existing


def parse_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_timestamp_iso(timestamp: Any) -> Optional[str]:
    try:
        timestamp_value = int(timestamp)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp_value, tz=timezone.utc).isoformat()


def parse_upload_date_iso(upload_date: Any) -> Optional[str]:
    raw = str(upload_date or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    return "{}-{}-{}T00:00:00+00:00".format(raw[0:4], raw[4:6], raw[6:8])


def load_info_metadata(path: Optional[Path]) -> JSONDict:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise TranscribeError("Invalid info JSON {}: {}".format(path, exc)) from exc
    if not isinstance(payload, dict):
        return {}

    published_at_iso = parse_timestamp_iso(payload.get("timestamp")) or parse_upload_date_iso(
        payload.get("upload_date")
    )
    creator_mid = payload.get("uploader_id") or payload.get("channel_id") or ""
    return {
        "title": str(payload.get("title") or "").strip(),
        "url": str(
            payload.get("webpage_url")
            or payload.get("original_url")
            or payload.get("url")
            or ""
        ).strip(),
        "creator_mid": str(creator_mid).strip(),
        "creator_name": str(payload.get("uploader") or payload.get("channel") or "").strip(),
        "published_at_iso": published_at_iso or "",
        "duration_seconds": payload.get("duration"),
    }


def parse_bilibili_subtitle_json(path: Path) -> JSONDict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TranscribeError("Invalid subtitle JSON {}: {}".format(path, exc)) from exc
    if not isinstance(payload, dict):
        raise TranscribeError("Subtitle file {} must contain a JSON object.".format(path))

    body = payload.get("body")
    if not isinstance(body, list):
        raise TranscribeError("Subtitle JSON {} is missing body[] segments.".format(path))

    segments = []
    lines = []
    for index, item in enumerate(body):
        if not isinstance(item, dict):
            continue
        text = str(item.get("content", "")).strip()
        if not text:
            continue
        start = parse_float(item.get("from"))
        end = parse_float(item.get("to"))
        segments.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "text": text,
            }
        )
        lines.append(text)

    language = payload.get("lan") or payload.get("language")
    return {
        "text": "\n".join(lines),
        "segments": segments,
        "language": language,
    }


def parse_srt_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d+):(\d+),(\d+)", value.strip())
    if not match:
        raise TranscribeError("Invalid SRT timestamp: {}".format(value))
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_vtt_timestamp(value: str) -> float:
    match = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+)\.(\d+)", value.strip())
    if not match:
        raise TranscribeError("Invalid VTT timestamp: {}".format(value))
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    milliseconds = int(match.group(4))
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_srt_subtitle(path: Path) -> JSONDict:
    content = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", content.strip())
    segments = []
    for index, block in enumerate(blocks):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if re.fullmatch(r"\d+", lines[0]):
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_raw, end_raw = [part.strip() for part in lines[0].split("-->", 1)]
        text = "\n".join(lines[1:]).strip()
        if not text:
            continue
        segments.append(
            {
                "index": index,
                "start": parse_srt_timestamp(start_raw),
                "end": parse_srt_timestamp(end_raw),
                "text": text,
            }
        )
    return {"text": "\n".join(segment["text"] for segment in segments), "segments": segments}


def parse_vtt_subtitle(path: Path) -> JSONDict:
    lines = path.read_text(encoding="utf-8").splitlines()
    segments = []
    index = 0
    pointer = 0
    while pointer < len(lines):
        line = lines[pointer].strip()
        pointer += 1
        if not line or line == "WEBVTT":
            continue
        if "-->" not in line and pointer < len(lines) and "-->" in lines[pointer]:
            line = lines[pointer].strip()
            pointer += 1
        if "-->" not in line:
            continue

        start_raw, end_raw = [part.strip().split(" ", 1)[0] for part in line.split("-->", 1)]
        text_lines = []
        while pointer < len(lines) and lines[pointer].strip():
            text_lines.append(lines[pointer].rstrip())
            pointer += 1
        text = "\n".join(text_lines).strip()
        if not text:
            continue
        segments.append(
            {
                "index": index,
                "start": parse_vtt_timestamp(start_raw),
                "end": parse_vtt_timestamp(end_raw),
                "text": text,
            }
        )
        index += 1
    return {"text": "\n".join(segment["text"] for segment in segments), "segments": segments}


def parse_text_subtitle(path: Path) -> JSONDict:
    text = path.read_text(encoding="utf-8").strip()
    return {"text": text, "segments": []}


def parse_subtitle_file(path: Path) -> JSONDict:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_bilibili_subtitle_json(path)
    if suffix == ".srt":
        return parse_srt_subtitle(path)
    if suffix == ".vtt":
        return parse_vtt_subtitle(path)
    if suffix == ".txt":
        return parse_text_subtitle(path)
    raise TranscribeError("Unsupported subtitle file format: {}".format(path))


def build_raw_transcript(
    task: TranscriptionTask,
    *,
    source_type: str,
    provider: str,
    text: str,
    segments: Optional[List[JSONDict]] = None,
    model: Optional[str] = None,
    language: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> JSONDict:
    return {
        "schema_version": 1,
        "bvid": task.bvid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type,
        "provider": provider,
        "model": model,
        "language": language,
        "audio_path": str(task.audio_path) if task.audio_path else None,
        "subtitle_path": str(task.subtitle_path) if task.subtitle_path else None,
        "text": text.strip(),
        "segments": segments or [],
        "metadata": dict(metadata or {}),
    }


def build_subtitle_transcript(task: TranscriptionTask) -> JSONDict:
    if task.subtitle_path is None:
        raise TranscribeError("No subtitle file available for {}.".format(task.bvid))
    parsed = parse_subtitle_file(task.subtitle_path)
    metadata = load_info_metadata(task.info_path)
    return build_raw_transcript(
        task,
        source_type="subtitle",
        provider="local-subtitle",
        text=str(parsed.get("text", "")),
        segments=list(parsed.get("segments", [])),
        language=parsed.get("language"),
        metadata={
            **metadata,
            "subtitle_format": task.subtitle_path.suffix.lower(),
        },
    )


def join_openai_endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/audio/transcriptions"


def build_multipart_request(
    *,
    audio_path: Path,
    model: str,
    api_key: str,
    base_url: str,
    language: Optional[str],
    prompt: Optional[str],
) -> Request:
    boundary = "----b_dlt_{}".format(uuid.uuid4().hex)
    mime_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    parts: List[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.extend(
            [
                "--{}\r\n".format(boundary).encode("utf-8"),
                'Content-Disposition: form-data; name="{}"\r\n\r\n'.format(name).encode(
                    "utf-8"
                ),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    add_field("model", model)
    add_field("response_format", "json")
    if language:
        add_field("language", language)
    if prompt:
        add_field("prompt", prompt)

    parts.extend(
        [
            "--{}\r\n".format(boundary).encode("utf-8"),
            (
                'Content-Disposition: form-data; name="file"; filename="{}"\r\n'.format(
                    audio_path.name
                )
            ).encode("utf-8"),
            "Content-Type: {}\r\n\r\n".format(mime_type).encode("utf-8"),
            audio_path.read_bytes(),
            b"\r\n",
            "--{}--\r\n".format(boundary).encode("utf-8"),
        ]
    )

    body = b"".join(parts)
    headers = {
        "Authorization": "Bearer {}".format(api_key),
        "Content-Type": "multipart/form-data; boundary={}".format(boundary),
    }
    return Request(join_openai_endpoint(base_url), data=body, headers=headers, method="POST")


def transcribe_audio_with_openai(
    task: TranscriptionTask,
    *,
    model: str,
    language: Optional[str],
    prompt: Optional[str],
    timeout: float,
    opener: Opener = urlopen,
) -> JSONDict:
    if task.audio_path is None:
        raise TranscribeError("No audio file available for {}.".format(task.bvid))

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise TranscribeError("OPENAI_API_KEY is required for ASR fallback.")

    audio_size = task.audio_path.stat().st_size
    if audio_size > MAX_OPENAI_UPLOAD_BYTES:
        raise TranscribeError(
            "Audio file {} exceeds the OpenAI upload limit of 25 MB.".format(task.audio_path)
        )

    request = build_multipart_request(
        audio_path=task.audio_path,
        model=model,
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        language=language,
        prompt=prompt,
    )

    try:
        with opener(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        raise TranscribeError(
            "OpenAI transcription request failed with HTTP {}: {}".format(
                exc.code,
                error_payload,
            )
        ) from exc
    except URLError as exc:
        raise TranscribeError("OpenAI network error: {}".format(exc.reason)) from exc
    except OSError as exc:
        raise TranscribeError("Failed to call OpenAI transcription API: {}".format(exc)) from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TranscribeError("Invalid OpenAI transcription response: {}".format(exc)) from exc

    text = str(data.get("text", "")).strip()
    if not text:
        raise TranscribeError("OpenAI transcription response did not include text.")

    metadata = load_info_metadata(task.info_path)

    return build_raw_transcript(
        task,
        source_type="asr",
        provider="openai",
        model=model,
        language=language,
        text=text,
        segments=[],
        metadata={
            **metadata,
            "response_format": "json",
            "audio_size_bytes": audio_size,
        },
    )


def write_transcript(path: Path, transcript: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def run_transcription(
    audio_dir: Path,
    subtitle_dir: Path,
    output_dir: Path,
    *,
    provider: str,
    model: str,
    language: Optional[str],
    prompt: Optional[str],
    timeout: float,
    overwrite: bool = False,
    opener: Opener = urlopen,
) -> TranscriptionSummary:
    tasks, skipped_existing = discover_transcription_tasks(
        audio_dir,
        subtitle_dir,
        output_dir,
        overwrite=overwrite,
    )
    if not tasks:
        raise TranscribeError("No subtitle or audio files found to transcribe.")

    subtitle_success = 0
    asr_success = 0
    failed = 0

    for task in tasks:
        try:
            if task.subtitle_path is not None:
                try:
                    transcript = build_subtitle_transcript(task)
                    subtitle_success += 1
                except TranscribeError:
                    if task.audio_path is None:
                        raise
                    transcript = transcribe_audio_with_openai(
                        task,
                        model=model,
                        language=language,
                        prompt=prompt,
                        timeout=timeout,
                        opener=opener,
                    )
                    asr_success += 1
            else:
                transcript = transcribe_audio_with_openai(
                    task,
                    model=model,
                    language=language,
                    prompt=prompt,
                    timeout=timeout,
                    opener=opener,
                )
                asr_success += 1

            write_transcript(task.output_path, transcript)
        except TranscribeError as exc:
            failed += 1
            print("Failed {}: {}".format(task.bvid, exc), file=sys.stderr)

    return TranscriptionSummary(
        total_tasks=len(tasks),
        skipped_existing=skipped_existing,
        subtitle_success=subtitle_success,
        asr_success=asr_success,
        failed=failed,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0.")

    try:
        summary = run_transcription(
            args.audio_dir,
            args.subtitle_dir,
            args.output_dir,
            provider=args.provider,
            model=args.model,
            language=args.language,
            prompt=args.prompt,
            timeout=args.timeout,
            overwrite=args.overwrite,
        )
    except TranscribeError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    print(
        "Summary: {} subtitle, {} asr, {} skipped, {} failed.".format(
            summary.subtitle_success,
            summary.asr_success,
            summary.skipped_existing,
            summary.failed,
        ),
        file=sys.stderr,
    )
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
