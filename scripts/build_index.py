"""Build a local SQLite FTS5 index for generated knowledge files."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence


DEFAULT_CLEAN_DIR = Path("data/transcripts/clean")
DEFAULT_VIDEO_SUMMARY_DIR = Path("data/summaries/videos")
DEFAULT_CREATOR_SUMMARY_DIR = Path("data/summaries/creators")
DEFAULT_DB_PATH = Path("db/knowledge.db")
LATIN_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
CJK_CHUNK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")


class BuildIndexError(RuntimeError):
    """Raised when index inputs are invalid."""


@dataclass
class DocumentRecord:
    doc_id: str
    doc_type: str
    bvid: Optional[str]
    creator_mid: Optional[str]
    title: str
    path: str
    content: str
    search_text: str


@dataclass
class BuildIndexSummary:
    documents_indexed: int
    query_results: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or refresh the local SQLite search index from generated text files."
    )
    parser.add_argument(
        "--clean-dir",
        type=Path,
        default=DEFAULT_CLEAN_DIR,
        help="Directory containing cleaned transcript markdown files.",
    )
    parser.add_argument(
        "--video-summary-dir",
        type=Path,
        default=DEFAULT_VIDEO_SUMMARY_DIR,
        help="Directory containing per-video summaries.",
    )
    parser.add_argument(
        "--creator-summary-dir",
        type=Path,
        default=DEFAULT_CREATOR_SUMMARY_DIR,
        help="Directory containing per-creator summaries.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path for the knowledge index.",
    )
    parser.add_argument(
        "--query",
        help="Optional FTS query to run after rebuilding the index.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of query results to print.",
    )
    return parser


def parse_frontmatter(content: str) -> tuple[Dict[str, str], str]:
    if not content.startswith("---\n"):
        return {}, content.strip()

    marker = "\n---\n"
    end_index = content.find(marker, 4)
    if end_index == -1:
        return {}, content.strip()

    frontmatter_lines = content[4:end_index].splitlines()
    metadata: Dict[str, str] = {}
    for line in frontmatter_lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    body = content[end_index + len(marker) :].strip()
    return metadata, body


def tokenize_search_text(text: str) -> List[str]:
    tokens: List[str] = []
    seen = set()

    for token in LATIN_TOKEN_PATTERN.findall(text.lower()):
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)

    for chunk in CJK_CHUNK_PATTERN.findall(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        if len(chunk) == 1:
            if chunk not in seen:
                seen.add(chunk)
                tokens.append(chunk)
            continue
        for index in range(len(chunk) - 1):
            bigram = chunk[index : index + 2]
            if bigram not in seen:
                seen.add(bigram)
                tokens.append(bigram)
    return tokens


def build_search_text(
    title: str,
    content: str,
    *,
    bvid: Optional[str] = None,
    creator_mid: Optional[str] = None,
) -> str:
    base_parts = [title, content]
    if bvid:
        base_parts.append(bvid)
    if creator_mid:
        base_parts.append(creator_mid)
    return " ".join(tokenize_search_text(" ".join(base_parts)))


def build_match_query(query: str) -> str:
    tokens = tokenize_search_text(query)
    if not tokens:
        raise BuildIndexError("Query is empty after tokenization.")
    escaped = ['"{}"'.format(token.replace('"', '""')) for token in tokens]
    return " AND ".join(escaped)


def load_markdown_record(path: Path, doc_type: str) -> DocumentRecord:
    content = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(content)
    title = metadata.get("title") or body.splitlines()[0].lstrip("# ").strip() or path.stem
    bvid = metadata.get("bvid")
    creator_mid = metadata.get("creator_mid")
    doc_id = "{}:{}".format(doc_type, path.stem)
    search_text = build_search_text(title, body, bvid=bvid, creator_mid=creator_mid)
    return DocumentRecord(
        doc_id=doc_id,
        doc_type=doc_type,
        bvid=bvid or None,
        creator_mid=creator_mid or None,
        title=title,
        path=str(path),
        content=body,
        search_text=search_text,
    )


def collect_markdown_records(directory: Path, doc_type: str) -> List[DocumentRecord]:
    if not directory.is_dir():
        return []
    return [load_markdown_record(path, doc_type) for path in sorted(directory.glob("*.md"))]


def collect_documents(
    clean_dir: Path,
    video_summary_dir: Path,
    creator_summary_dir: Path,
) -> List[DocumentRecord]:
    documents = []
    documents.extend(collect_markdown_records(clean_dir, "clean_transcript"))
    documents.extend(collect_markdown_records(video_summary_dir, "video_summary"))
    documents.extend(collect_markdown_records(creator_summary_dir, "creator_summary"))
    if not documents:
        raise BuildIndexError("No markdown documents found to index.")
    return documents


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;

        DROP TABLE IF EXISTS documents_fts;
        DROP TABLE IF EXISTS documents;

        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY,
            doc_type TEXT NOT NULL,
            bvid TEXT,
            creator_mid TEXT,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            content TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE documents_fts USING fts5(
            doc_id UNINDEXED,
            title,
            content,
            search_text,
            doc_type UNINDEXED,
            creator_mid UNINDEXED,
            bvid UNINDEXED
        );
        """
    )


def replace_documents(connection: sqlite3.Connection, documents: Iterable[DocumentRecord]) -> int:
    rows = list(documents)
    connection.executemany(
        """
        INSERT INTO documents (doc_id, doc_type, bvid, creator_mid, title, path, content)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.doc_id,
                row.doc_type,
                row.bvid,
                row.creator_mid,
                row.title,
                row.path,
                row.content,
            )
            for row in rows
        ],
    )
    connection.executemany(
        """
        INSERT INTO documents_fts (doc_id, title, content, search_text, doc_type, creator_mid, bvid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.doc_id,
                row.title,
                row.content,
                row.search_text,
                row.doc_type,
                row.creator_mid,
                row.bvid,
            )
            for row in rows
        ],
    )
    connection.commit()
    return len(rows)


def query_documents(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
) -> List[Dict[str, str]]:
    match_query = build_match_query(query)
    cursor = connection.execute(
        """
        SELECT documents.doc_id, documents.doc_type, documents.title, documents.path
        FROM documents_fts
        JOIN documents ON documents.doc_id = documents_fts.doc_id
        WHERE documents_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (match_query, limit),
    )
    return [
        {
            "doc_id": row[0],
            "doc_type": row[1],
            "title": row[2],
            "path": row[3],
        }
        for row in cursor.fetchall()
    ]


def rebuild_index(
    clean_dir: Path,
    video_summary_dir: Path,
    creator_summary_dir: Path,
    db_path: Path,
) -> int:
    documents = collect_documents(clean_dir, video_summary_dir, creator_summary_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        initialize_database(connection)
        return replace_documents(connection, documents)
    finally:
        connection.close()


def run_build_index(
    clean_dir: Path,
    video_summary_dir: Path,
    creator_summary_dir: Path,
    db_path: Path,
    *,
    query: Optional[str] = None,
    limit: int = 10,
) -> BuildIndexSummary:
    documents_indexed = rebuild_index(
        clean_dir,
        video_summary_dir,
        creator_summary_dir,
        db_path,
    )
    query_results = 0
    if query:
        connection = sqlite3.connect(db_path)
        try:
            results = query_documents(connection, query, limit=limit)
        finally:
            connection.close()
        query_results = len(results)
        for result in results:
            print(
                "[{}] {} {}".format(
                    result["doc_type"],
                    result["title"],
                    result["path"],
                )
            )
    return BuildIndexSummary(
        documents_indexed=documents_indexed,
        query_results=query_results,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.limit <= 0:
        parser.error("--limit must be greater than 0.")

    try:
        summary = run_build_index(
            args.clean_dir,
            args.video_summary_dir,
            args.creator_summary_dir,
            args.db_path,
            query=args.query,
            limit=args.limit,
        )
    except (BuildIndexError, sqlite3.Error) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    print(
        "Indexed {} documents.".format(summary.documents_indexed),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
