"""Fetch a Bilibili creator video manifest into the local workspace."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bilibili_auth import (
    AUTH_MODE_COOKIE_HEADER,
    AUTH_MODE_COOKIES_FILE,
    AUTH_MODE_NONE,
    BilibiliAuthConfig,
    BilibiliAuthError,
    add_auth_arguments,
    build_request_headers,
    load_dotenv,
    resolve_auth_config,
)


DEFAULT_OUTPUT_DIR = Path("data/manifests")
DEFAULT_TIMEOUT_SECONDS = 20.0
NAV_ENDPOINT = "https://api.bilibili.com/x/web-interface/nav"
VIDEO_LIST_ENDPOINT = "https://api.bilibili.com/x/space/wbi/arc/search"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}
MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]
FILTERED_WBI_CHARS = "!'()*"
JSONDict = Dict[str, Any]
Opener = Callable[..., Any]
SUPPORTED_AUTH_MODES = (
    AUTH_MODE_NONE,
    AUTH_MODE_COOKIES_FILE,
    AUTH_MODE_COOKIE_HEADER,
)


class BilibiliAPIError(RuntimeError):
    """Raised when Bilibili returns a non-zero API code."""


def parse_creator_mid(value: str) -> str:
    creator_mid = value.strip()
    if not creator_mid.isdigit():
        raise argparse.ArgumentTypeError("creator MID must be a numeric string.")
    return creator_mid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a Bilibili creator video manifest into the local workspace."
    )
    parser.add_argument(
        "--creator-mid",
        required=True,
        type=parse_creator_mid,
        help="Bilibili creator MID.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory used to store manifest files.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=30,
        help="Number of videos requested per page.",
    )
    parser.add_argument(
        "--order",
        choices=("pubdate", "click", "stow"),
        default="pubdate",
        help="Bilibili sort order for the creator video list.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds.",
    )
    add_auth_arguments(parser, supported_modes=SUPPORTED_AUTH_MODES)
    return parser


def build_query(params: Mapping[str, Any]) -> str:
    normalized = {}
    for key, value in sorted(params.items()):
        sanitized = "".join(
            ch for ch in str(value) if ch not in FILTERED_WBI_CHARS
        )
        normalized[key] = sanitized
    return urlencode(normalized, quote_via=quote, safe="")


def get_mixin_key(raw_key: str) -> str:
    if len(raw_key) < len(MIXIN_KEY_ENC_TAB):
        raise RuntimeError("Invalid WBI key payload: expected at least 64 characters.")
    return "".join(raw_key[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def sign_wbi_params(
    params: Mapping[str, Any],
    img_key: str,
    sub_key: str,
    *,
    now: Optional[int] = None,
) -> JSONDict:
    current_time = int(time.time() if now is None else now)
    signed_params = dict(params)
    signed_params["wts"] = current_time
    query = build_query(signed_params)
    mixin_key = get_mixin_key(img_key + sub_key)
    signed_params["w_rid"] = md5((query + mixin_key).encode("utf-8")).hexdigest()
    return signed_params


def request_json(
    url: str,
    timeout: float,
    *,
    auth_config: Optional[BilibiliAuthConfig] = None,
    opener: Opener = urlopen,
) -> JSONDict:
    headers = DEFAULT_HEADERS
    if auth_config is not None:
        headers = build_request_headers(DEFAULT_HEADERS, auth_config)
    request = Request(url, headers=headers)
    try:
        with opener(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while requesting {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while requesting {url}: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to request {url}: {exc}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to decode JSON from {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected JSON payload from {url}: expected object.")
    return data


def get_wbi_keys(
    timeout: float,
    *,
    auth_config: Optional[BilibiliAuthConfig] = None,
    opener: Opener = urlopen,
) -> Tuple[str, str]:
    response = request_json(
        NAV_ENDPOINT,
        timeout=timeout,
        auth_config=auth_config,
        opener=opener,
    )
    data = response.get("data") or {}
    wbi_img = data.get("wbi_img") or {}
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")
    if not img_url or not sub_url:
        raise RuntimeError("Failed to retrieve WBI keys from Bilibili nav endpoint.")
    img_key = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
    return img_key, sub_key


def fetch_video_page(
    creator_mid: str,
    page_number: int,
    page_size: int,
    order: str,
    img_key: str,
    sub_key: str,
    *,
    timeout: float,
    auth_config: Optional[BilibiliAuthConfig] = None,
    opener: Opener = urlopen,
    now: Optional[int] = None,
) -> JSONDict:
    params = sign_wbi_params(
        {
            "mid": creator_mid,
            "order": order,
            "pn": page_number,
            "ps": page_size,
        },
        img_key,
        sub_key,
        now=now,
    )
    url = "{}?{}".format(VIDEO_LIST_ENDPOINT, build_query(params))
    response = request_json(
        url,
        timeout=timeout,
        auth_config=auth_config,
        opener=opener,
    )
    code = response.get("code")
    if code != 0:
        raise BilibiliAPIError(
            "Bilibili API returned code {} for creator {} page {}: {}".format(
                code,
                creator_mid,
                page_number,
                response.get("message", ""),
            )
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Bilibili API returned an invalid data payload.")
    return data


def normalize_timestamp(value: Any) -> Optional[str]:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def normalize_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_categories(tlist: Any) -> list[JSONDict]:
    if not isinstance(tlist, dict):
        return []
    categories = []
    for item in sorted(tlist.values(), key=lambda entry: int(entry.get("tid", 0))):
        categories.append(
            {
                "tid": normalize_int(item.get("tid")),
                "name": item.get("name", ""),
                "count": normalize_int(item.get("count")),
            }
        )
    return categories


def normalize_video_record(item: Mapping[str, Any], creator_mid: str) -> JSONDict:
    aid = normalize_int(item.get("aid"))
    bvid = item.get("bvid", "") or ""
    created_at = normalize_int(item.get("created"))
    return {
        "aid": aid,
        "bvid": bvid,
        "title": item.get("title", ""),
        "description": item.get("description", ""),
        "url": "https://www.bilibili.com/video/{}".format(bvid) if bvid else "",
        "creator_mid": creator_mid,
        "author_mid": normalize_int(item.get("mid")),
        "author_name": item.get("author", ""),
        "published_at": created_at,
        "published_at_iso": normalize_timestamp(created_at),
        "duration_seconds": normalize_int(item.get("duration")),
        "duration_text": item.get("length", ""),
        "page_count": normalize_int(item.get("videos")),
        "category_id": normalize_int(item.get("typeid")),
        "cover_url": item.get("pic", ""),
        "play_count": normalize_int(item.get("play")),
        "danmaku_count": normalize_int(item.get("video_review")),
        "comment_count": normalize_int(item.get("comment")),
        "is_union_video": bool(item.get("is_union_video")),
        "is_charging_arc": bool(item.get("is_charging_arc")),
        "is_live_playback": bool(item.get("is_live_playback")),
    }


def build_manifest(
    creator_mid: str,
    *,
    page_size: int,
    order: str,
    timeout: float,
    auth_config: Optional[BilibiliAuthConfig] = None,
    opener: Opener = urlopen,
    now: Optional[int] = None,
) -> JSONDict:
    resolved_auth = auth_config or BilibiliAuthConfig()
    img_key, sub_key = get_wbi_keys(
        timeout=timeout,
        auth_config=resolved_auth,
        opener=opener,
    )
    page_number = 1
    total_count = 0
    pages_fetched = 0
    categories: list[JSONDict] = []
    videos: list[JSONDict] = []
    seen_ids = set()

    while True:
        page_data = fetch_video_page(
            creator_mid,
            page_number,
            page_size,
            order,
            img_key,
            sub_key,
            timeout=timeout,
            auth_config=resolved_auth,
            opener=opener,
            now=now,
        )
        pages_fetched += 1

        page_info = page_data.get("page") or {}
        list_info = page_data.get("list") or {}
        total_count = max(total_count, normalize_int(page_info.get("count")) or 0)
        categories = normalize_categories(list_info.get("tlist"))
        vlist = list_info.get("vlist") or []

        for item in vlist:
            if not isinstance(item, dict):
                continue
            video = normalize_video_record(item, creator_mid)
            unique_id = video["bvid"] or "aid:{}".format(video["aid"])
            if unique_id in seen_ids:
                continue
            seen_ids.add(unique_id)
            videos.append(video)

        if not vlist:
            break

        current_page = normalize_int(page_info.get("pn")) or page_number
        current_page_size = normalize_int(page_info.get("ps")) or page_size
        if total_count and current_page * current_page_size >= total_count:
            break
        page_number += 1

    return {
        "schema_version": 1,
        "platform": "bilibili",
        "creator_mid": creator_mid,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "endpoint": VIDEO_LIST_ENDPOINT,
            "order": order,
            "auth": "wbi",
            "auth_mode": resolved_auth.mode,
        },
        "page_size": page_size,
        "pages_fetched": pages_fetched,
        "total_count": total_count,
        "video_count": len(videos),
        "page_count": math.ceil(total_count / page_size) if total_count else 0,
        "categories": categories,
        "videos": videos,
    }


def write_manifest(output_path: Path, manifest: Mapping[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(output_path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.page_size <= 0:
        parser.error("--page-size must be greater than 0.")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0.")

    output_path = args.output_dir / "creator_{}.json".format(args.creator_mid)
    print(
        "Fetching creator {} video manifest...".format(args.creator_mid),
        file=sys.stderr,
    )
    try:
        auth_config = resolve_auth_config(
            auth_mode=args.auth_mode,
            cookies_file=args.cookies_file,
            cookie_header=getattr(args, "cookie_header", None),
            supported_modes=SUPPORTED_AUTH_MODES,
        )
        manifest = build_manifest(
            args.creator_mid,
            page_size=args.page_size,
            order=args.order,
            timeout=args.timeout,
            auth_config=auth_config,
        )
        write_manifest(output_path, manifest)
    except (BilibiliAPIError, BilibiliAuthError, RuntimeError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    print(
        "Wrote {} videos to {}".format(manifest["video_count"], output_path),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
