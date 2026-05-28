from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "build_index.py"
SPEC = importlib.util.spec_from_file_location("build_index", SCRIPT_PATH)
build_index = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = build_index
SPEC.loader.exec_module(build_index)


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_parse_frontmatter_extracts_metadata_and_body() -> None:
    content = """---
type: "video_summary"
bvid: "BV1xx411c7mD"
creator_mid: "123"
title: "第一条视频"
---

# 第一条视频

正文内容
"""

    metadata, body = build_index.parse_frontmatter(content)

    assert metadata["type"] == "video_summary"
    assert metadata["bvid"] == "BV1xx411c7mD"
    assert metadata["creator_mid"] == "123"
    assert metadata["title"] == "第一条视频"
    assert "# 第一条视频" in body


def test_collect_documents_reads_all_markdown_types(tmp_path: Path) -> None:
    clean_dir = tmp_path / "clean"
    video_dir = tmp_path / "video"
    creator_dir = tmp_path / "creator"

    write_markdown(
        clean_dir / "BV1.md",
        """---
type: "clean_transcript"
bvid: "BV1"
creator_mid: "123"
title: "清洗稿"
---

正文
""",
    )
    write_markdown(
        video_dir / "BV1.md",
        """---
type: "video_summary"
bvid: "BV1"
creator_mid: "123"
title: "视频摘要"
---

摘要
""",
    )
    write_markdown(
        creator_dir / "creator_123.md",
        """---
type: "creator_summary"
creator_mid: "123"
---

# Creator 123
汇总
""",
    )

    documents = build_index.collect_documents(clean_dir, video_dir, creator_dir)

    assert len(documents) == 3
    assert {document.doc_type for document in documents} == {
        "clean_transcript",
        "video_summary",
        "creator_summary",
    }


def test_rebuild_index_and_query_documents(tmp_path: Path) -> None:
    clean_dir = tmp_path / "clean"
    video_dir = tmp_path / "video"
    creator_dir = tmp_path / "creator"
    db_path = tmp_path / "knowledge.db"

    write_markdown(
        clean_dir / "BV1.md",
        """---
type: "clean_transcript"
bvid: "BV1"
creator_mid: "123"
title: "清洗稿"
---

知识管理 方法论 内容
""",
    )
    write_markdown(
        video_dir / "BV1.md",
        """---
type: "video_summary"
bvid: "BV1"
creator_mid: "123"
title: "视频摘要"
---

工作流 自动化 摘要
""",
    )

    indexed_count = build_index.rebuild_index(clean_dir, video_dir, creator_dir, db_path)
    assert indexed_count == 2

    connection = sqlite3.connect(db_path)
    try:
        results = build_index.query_documents(connection, "知识管理", limit=5)
        assert len(results) == 1
        assert results[0]["doc_type"] == "clean_transcript"
        assert results[0]["title"] == "清洗稿"
        video_results = build_index.query_documents(connection, "工作流", limit=5)
        assert len(video_results) == 1
        assert video_results[0]["doc_type"] == "video_summary"
    finally:
        connection.close()


def test_collect_documents_raises_when_empty(tmp_path: Path) -> None:
    with pytest.raises(build_index.BuildIndexError):
        build_index.collect_documents(
            tmp_path / "clean",
            tmp_path / "video",
            tmp_path / "creator",
        )


def test_query_documents_supports_chinese_bigram_search(tmp_path: Path) -> None:
    clean_dir = tmp_path / "clean"
    video_dir = tmp_path / "video"
    creator_dir = tmp_path / "creator"
    db_path = tmp_path / "knowledge.db"

    write_markdown(
        video_dir / "BV1.md",
        """---
type: "video_summary"
bvid: "BV1"
creator_mid: "123"
title: "海鸥评测"
---

这条内容讨论比亚迪海鸥和主持人辛巴的看法。
""",
    )

    build_index.rebuild_index(clean_dir, video_dir, creator_dir, db_path)

    connection = sqlite3.connect(db_path)
    try:
        assert len(build_index.query_documents(connection, "海鸥", limit=5)) == 1
        assert len(build_index.query_documents(connection, "比亚迪", limit=5)) == 1
        assert len(build_index.query_documents(connection, "辛巴", limit=5)) == 1
    finally:
        connection.close()
