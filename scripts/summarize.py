"""Generate clean transcripts and deterministic Markdown summaries."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


DEFAULT_RAW_DIR = Path("data/transcripts/raw")
DEFAULT_CLEAN_DIR = Path("data/transcripts/clean")
DEFAULT_VIDEO_SUMMARY_DIR = Path("data/summaries/videos")
DEFAULT_CREATOR_SUMMARY_DIR = Path("data/summaries/creators")
DEFAULT_SECTION_WORD_LIMIT = 6
DEFAULT_BULLET_LIMIT = 8
JSONDict = Dict[str, Any]


class SummarizeError(RuntimeError):
    """Raised when raw transcript inputs are invalid."""


@dataclass
class TranscriptRecord:
    bvid: str
    creator_mid: str
    title: str
    text: str
    source_type: str
    provider: str
    language: Optional[str]
    published_at_iso: Optional[str]
    duration_seconds: Optional[int]
    url: Optional[str]
    segments: List[JSONDict]
    audio_path: Optional[str]
    subtitle_path: Optional[str]
    raw_path: Path


@dataclass
class SummarizeSummary:
    transcript_count: int
    clean_written: int
    video_summary_written: int
    creator_summary_written: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Turn raw transcripts into clean text and structured summaries."
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
        help="Directory containing raw transcript files.",
    )
    parser.add_argument(
        "--clean-dir",
        type=Path,
        default=DEFAULT_CLEAN_DIR,
        help="Directory used to store cleaned transcript markdown files.",
    )
    parser.add_argument(
        "--video-summary-dir",
        type=Path,
        default=DEFAULT_VIDEO_SUMMARY_DIR,
        help="Directory used to store per-video summaries.",
    )
    parser.add_argument(
        "--creator-summary-dir",
        type=Path,
        default=DEFAULT_CREATOR_SUMMARY_DIR,
        help="Directory used to store per-creator summaries.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing markdown outputs.",
    )
    return parser


def load_json_file(path: Path) -> JSONDict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SummarizeError("Raw transcript not found: {}".format(path)) from exc
    except json.JSONDecodeError as exc:
        raise SummarizeError("Invalid JSON in {}: {}".format(path, exc)) from exc
    if not isinstance(payload, dict):
        raise SummarizeError("Transcript {} must be a JSON object.".format(path))
    return payload


def safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def load_transcript_records(raw_dir: Path) -> List[TranscriptRecord]:
    if not raw_dir.is_dir():
        raise SummarizeError("Raw transcript directory does not exist: {}".format(raw_dir))

    records: List[TranscriptRecord] = []
    for path in sorted(raw_dir.glob("*.json")):
        payload = load_json_file(path)
        bvid = normalize_text(payload.get("bvid")) or path.stem
        text = normalize_text(payload.get("text"))
        if not text:
            raise SummarizeError("Transcript {} has empty text.".format(path))
        metadata = payload.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise SummarizeError("Transcript {} has invalid metadata.".format(path))

        records.append(
            TranscriptRecord(
                bvid=bvid,
                creator_mid=normalize_text(
                    metadata.get("creator_mid")
                    or payload.get("creator_mid")
                    or "unknown"
                ),
                title=normalize_text(metadata.get("title")) or bvid,
                text=text,
                source_type=normalize_text(payload.get("source_type")) or "unknown",
                provider=normalize_text(payload.get("provider")) or "unknown",
                language=normalize_text(payload.get("language")) or None,
                published_at_iso=normalize_text(
                    metadata.get("published_at_iso") or payload.get("published_at_iso")
                )
                or None,
                duration_seconds=safe_int(
                    metadata.get("duration_seconds") or payload.get("duration_seconds")
                ),
                url=normalize_text(metadata.get("url") or payload.get("url")) or None,
                segments=list(payload.get("segments") or []),
                audio_path=normalize_text(payload.get("audio_path")) or None,
                subtitle_path=normalize_text(payload.get("subtitle_path")) or None,
                raw_path=path,
            )
        )
    if not records:
        raise SummarizeError("No raw transcript files found under {}.".format(raw_dir))
    return records


def split_paragraphs(text: str) -> List[str]:
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if not normalized:
        return []
    blocks = [block.strip() for block in re.split(r"\n{2,}", normalized) if block.strip()]
    if blocks:
        return blocks
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def split_sentences(text: str) -> List[str]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return []
    sentences = re.split(r"(?<=[。！？!?\.])\s*", normalized)
    cleaned = [sentence.strip() for sentence in sentences if sentence.strip()]
    return cleaned or [normalized]


def estimate_keywords(text: str, limit: int = 8) -> List[str]:
    candidates = re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,8}", text)
    stopwords = {
        "这是",
        "这里",
        "讨论",
        "介绍",
        "说明",
        "分享",
        "通过",
        "对于",
        "关于",
        "这个",
        "那个",
        "我们",
        "你们",
        "他们",
        "以及",
        "如果",
        "因为",
        "所以",
        "就是",
        "一个",
        "一些",
        "进行",
        "使用",
        "内容",
        "视频",
        "可以",
        "还是",
        "然后",
        "没有",
        "不是",
        "自己",
    }
    scores: Dict[str, int] = {}
    for candidate in candidates:
        token = candidate.strip().lower()
        if len(token) < 2 or token in stopwords:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{5,8}", token):
            for stopword in sorted(stopwords, key=len, reverse=True):
                if token.startswith(stopword) and len(token) > len(stopword) + 1:
                    token = token[len(stopword) :]
                    break
        scores[token] = scores.get(token, 0) + 1
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _count in ordered[:limit]]


def extract_topic_phrases(text: str, limit: int = 8) -> List[str]:
    sentences = split_sentences(text)
    stop_prefixes = ("这是", "这里", "讨论", "介绍", "说明", "分享", "关于", "通过")
    phrases: List[str] = []
    seen = set()

    for sentence in sentences:
        compact = re.sub(r"[，,；;：:\s]+", "", sentence)
        if not compact:
            continue
        for prefix in stop_prefixes:
            if compact.startswith(prefix) and len(compact) > len(prefix) + 1:
                compact = compact[len(prefix) :]
                break
        compact = compact.rstrip("。！？!?")
        if len(compact) < 2:
            continue

        candidates = []
        if len(compact) <= 8:
            candidates.append(compact)
        if len(compact) >= 4:
            candidates.append(compact[-4:])
        if len(compact) >= 5:
            candidates.append(compact[-5:])
        if compact not in candidates:
            candidates.append(compact)

        for candidate in candidates:
            if candidate in seen or len(candidate) < 2:
                continue
            seen.add(candidate)
            phrases.append(candidate)
            break
        if len(phrases) >= limit:
            break
    return phrases


def extract_outline_points(text: str, limit: int = DEFAULT_BULLET_LIMIT) -> List[str]:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []
    points = []
    for paragraph in paragraphs:
        sentence = split_sentences(paragraph)[0]
        cleaned = re.sub(r"\s+", " ", sentence)
        if cleaned:
            points.append(cleaned)
        if len(points) >= limit:
            break
    return points


def build_section_title(sentence: str, index: int) -> str:
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", " ", sentence).strip()
    if not compact:
        return "Section {}".format(index)
    words = compact.split()
    title = " ".join(words[:DEFAULT_SECTION_WORD_LIMIT]).strip()
    return title or "Section {}".format(index)


def build_clean_transcript_markdown(record: TranscriptRecord) -> str:
    paragraphs = split_paragraphs(record.text)
    sections = []
    for index, paragraph in enumerate(paragraphs, start=1):
        section_title = build_section_title(split_sentences(paragraph)[0], index)
        sections.append("## {}\n\n{}".format(section_title, paragraph.strip()))

    frontmatter = [
        "---",
        'type: "clean_transcript"',
        'bvid: "{}"'.format(record.bvid),
        'creator_mid: "{}"'.format(record.creator_mid),
        'title: "{}"'.format(record.title.replace('"', '\\"')),
        'source_type: "{}"'.format(record.source_type),
        'provider: "{}"'.format(record.provider),
        'language: "{}"'.format(record.language or ""),
        'published_at: "{}"'.format(record.published_at_iso or ""),
        'duration_seconds: {}'.format(record.duration_seconds or 0),
        'url: "{}"'.format(record.url or ""),
        "---",
        "",
        "# {}".format(record.title),
        "",
        "## Overview",
        "",
        "- BVID: `{}`".format(record.bvid),
        "- Creator MID: `{}`".format(record.creator_mid),
        "- Source: `{}` via `{}`".format(record.source_type, record.provider),
    ]
    if record.url:
        frontmatter.append("- Video URL: {}".format(record.url))

    body = "\n\n".join(sections) if sections else record.text
    return "\n".join(frontmatter) + "\n\n" + body.strip() + "\n"


def build_video_summary_markdown(record: TranscriptRecord) -> str:
    outline_points = extract_outline_points(record.text)
    sentences = split_sentences(record.text)
    summary_sentence = " ".join(sentences[:2]).strip() if sentences else record.text
    keywords = extract_topic_phrases(record.text) or estimate_keywords(record.text)

    lines = [
        "---",
        'type: "video_summary"',
        'bvid: "{}"'.format(record.bvid),
        'creator_mid: "{}"'.format(record.creator_mid),
        'title: "{}"'.format(record.title.replace('"', '\\"')),
        'source_type: "{}"'.format(record.source_type),
        'provider: "{}"'.format(record.provider),
        "---",
        "",
        "# {}".format(record.title),
        "",
        "## Summary",
        "",
        summary_sentence or record.text,
        "",
        "## Outline",
        "",
    ]
    if outline_points:
        lines.extend("- {}".format(point) for point in outline_points)
    else:
        lines.append("- {}".format(record.text))

    lines.extend(["", "## Keywords", ""])
    if keywords:
        lines.extend("- `{}`".format(keyword) for keyword in keywords)
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Metadata",
            "",
            "- BVID: `{}`".format(record.bvid),
            "- Creator MID: `{}`".format(record.creator_mid),
            "- Language: `{}`".format(record.language or ""),
        ]
    )
    if record.url:
        lines.append("- Video URL: {}".format(record.url))
    return "\n".join(lines).strip() + "\n"


def build_creator_summary_markdown(creator_mid: str, records: List[TranscriptRecord]) -> str:
    sorted_records = sorted(records, key=lambda record: record.title.lower())
    combined_text = "\n".join(record.text for record in sorted_records)
    keywords = estimate_keywords(combined_text)

    lines = [
        "---",
        'type: "creator_summary"',
        'creator_mid: "{}"'.format(creator_mid),
        'video_count: {}'.format(len(sorted_records)),
        "---",
        "",
        "# Creator {}".format(creator_mid),
        "",
        "## Overview",
        "",
        "- Video count: {}".format(len(sorted_records)),
        "- Generated at: {}".format(datetime.now(timezone.utc).isoformat()),
        "",
        "## Videos",
        "",
    ]
    for record in sorted_records:
        lines.append("- `{}` {}".format(record.bvid, record.title))

    lines.extend(["", "## Repeated Topics", ""])
    if keywords:
        lines.extend("- `{}`".format(keyword) for keyword in keywords)
    else:
        lines.append("- None")

    lines.extend(["", "## Combined Outline", ""])
    combined_outline = []
    for record in sorted_records:
        points = extract_outline_points(record.text, limit=2)
        if not points:
            continue
        combined_outline.append("### {}".format(record.title))
        combined_outline.extend("- {}".format(point) for point in points)
        combined_outline.append("")
    if combined_outline:
        lines.extend(combined_outline[:-1] if combined_outline[-1] == "" else combined_outline)
    else:
        lines.append("- None")
    return "\n".join(lines).strip() + "\n"


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def should_skip(path: Path, overwrite: bool) -> bool:
    return path.exists() and not overwrite


def run_summarization(
    raw_dir: Path,
    clean_dir: Path,
    video_summary_dir: Path,
    creator_summary_dir: Path,
    *,
    overwrite: bool = False,
) -> SummarizeSummary:
    records = load_transcript_records(raw_dir)
    clean_written = 0
    video_summary_written = 0

    records_by_creator: Dict[str, List[TranscriptRecord]] = defaultdict(list)
    for record in records:
        records_by_creator[record.creator_mid].append(record)

        clean_path = clean_dir / "{}.md".format(record.bvid)
        if not should_skip(clean_path, overwrite):
            write_markdown(clean_path, build_clean_transcript_markdown(record))
            clean_written += 1

        video_summary_path = video_summary_dir / "{}.md".format(record.bvid)
        if not should_skip(video_summary_path, overwrite):
            write_markdown(video_summary_path, build_video_summary_markdown(record))
            video_summary_written += 1

    creator_summary_written = 0
    for creator_mid, creator_records in records_by_creator.items():
        creator_summary_path = creator_summary_dir / "creator_{}.md".format(creator_mid)
        if should_skip(creator_summary_path, overwrite):
            continue
        write_markdown(
            creator_summary_path,
            build_creator_summary_markdown(creator_mid, creator_records),
        )
        creator_summary_written += 1

    return SummarizeSummary(
        transcript_count=len(records),
        clean_written=clean_written,
        video_summary_written=video_summary_written,
        creator_summary_written=creator_summary_written,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        summary = run_summarization(
            args.raw_dir,
            args.clean_dir,
            args.video_summary_dir,
            args.creator_summary_dir,
            overwrite=args.overwrite,
        )
    except SummarizeError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    print(
        "Summary: {} transcripts, {} clean, {} video summaries, {} creator summaries.".format(
            summary.transcript_count,
            summary.clean_written,
            summary.video_summary_written,
            summary.creator_summary_written,
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
