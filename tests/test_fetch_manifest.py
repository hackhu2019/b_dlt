from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "fetch_manifest.py"
SPEC = importlib.util.spec_from_file_location("fetch_manifest", SCRIPT_PATH)
fetch_manifest = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = fetch_manifest
SPEC.loader.exec_module(fetch_manifest)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class QueueOpener:
    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.urls: list[str] = []
        self.requests = []

    def __call__(self, request, timeout: float = 0) -> FakeResponse:
        self.urls.append(request.full_url)
        self.requests.append(request)
        if not self._payloads:
            raise AssertionError("No payload left for request: {}".format(request.full_url))
        return FakeResponse(self._payloads.pop(0))


def test_sign_wbi_params_matches_reference_example() -> None:
    signed = fetch_manifest.sign_wbi_params(
        {"foo": "114", "bar": "514", "zab": 1919810},
        "7cd084941338484aae1ad9425b84077c",
        "4932caff0ff746eab6f01bf08b70ac45",
        now=1702204169,
    )

    assert signed["wts"] == 1702204169
    assert signed["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"


def test_build_manifest_fetches_all_pages_and_deduplicates() -> None:
    opener = QueueOpener(
        [
            {
                "data": {
                    "wbi_img": {
                        "img_url": (
                            "https://i0.hdslb.com/bfs/wbi/"
                            "7cd084941338484aae1ad9425b84077c.png"
                        ),
                        "sub_url": (
                            "https://i0.hdslb.com/bfs/wbi/"
                            "4932caff0ff746eab6f01bf08b70ac45.png"
                        ),
                    }
                }
            },
            {
                "code": 0,
                "message": "0",
                "data": {
                    "list": {
                        "tlist": {"188": {"tid": 188, "count": 3, "name": "科技"}},
                        "vlist": [
                            {
                                "aid": 1,
                                "bvid": "BV1xx411c7mD",
                                "title": "第一条视频",
                                "description": "desc 1",
                                "author": "creator",
                                "mid": 123,
                                "created": 1700000000,
                                "duration": 120,
                                "length": "02:00",
                                "videos": 1,
                                "typeid": 188,
                                "pic": "https://i0.hdslb.com/video1.jpg",
                                "play": 100,
                                "video_review": 10,
                                "comment": 3,
                                "is_union_video": 0,
                                "is_charging_arc": False,
                                "is_live_playback": 0,
                            },
                            {
                                "aid": 2,
                                "bvid": "BV2xx411c7mD",
                                "title": "第二条视频",
                                "description": "desc 2",
                                "author": "creator",
                                "mid": 123,
                                "created": 1700000100,
                                "duration": 90,
                                "length": "01:30",
                                "videos": 1,
                                "typeid": 188,
                                "pic": "https://i0.hdslb.com/video2.jpg",
                                "play": 200,
                                "video_review": 20,
                                "comment": 4,
                                "is_union_video": 0,
                                "is_charging_arc": False,
                                "is_live_playback": 0,
                            },
                        ],
                    },
                    "page": {"count": 3, "pn": 1, "ps": 2},
                },
            },
            {
                "code": 0,
                "message": "0",
                "data": {
                    "list": {
                        "tlist": {"188": {"tid": 188, "count": 3, "name": "科技"}},
                        "vlist": [
                            {
                                "aid": 2,
                                "bvid": "BV2xx411c7mD",
                                "title": "第二条视频",
                                "description": "desc 2",
                                "author": "creator",
                                "mid": 123,
                                "created": 1700000100,
                                "duration": 90,
                                "length": "01:30",
                                "videos": 1,
                                "typeid": 188,
                                "pic": "https://i0.hdslb.com/video2.jpg",
                                "play": 200,
                                "video_review": 20,
                                "comment": 4,
                                "is_union_video": 0,
                                "is_charging_arc": False,
                                "is_live_playback": 0,
                            },
                            {
                                "aid": 3,
                                "bvid": "BV3xx411c7mD",
                                "title": "第三条视频",
                                "description": "desc 3",
                                "author": "creator",
                                "mid": 123,
                                "created": 1700000200,
                                "duration": 60,
                                "length": "01:00",
                                "videos": 1,
                                "typeid": 188,
                                "pic": "https://i0.hdslb.com/video3.jpg",
                                "play": 300,
                                "video_review": 30,
                                "comment": 5,
                                "is_union_video": 1,
                                "is_charging_arc": True,
                                "is_live_playback": 0,
                            },
                        ],
                    },
                    "page": {"count": 3, "pn": 2, "ps": 2},
                },
            },
        ]
    )

    manifest = fetch_manifest.build_manifest(
        "123",
        page_size=2,
        order="pubdate",
        timeout=1.0,
        opener=opener,
        now=1702204169,
    )

    assert opener.urls[0] == fetch_manifest.NAV_ENDPOINT
    assert opener.urls[1].startswith(fetch_manifest.VIDEO_LIST_ENDPOINT)
    assert "pn=1" in opener.urls[1]
    assert "ps=2" in opener.urls[1]
    assert "w_rid=" in opener.urls[1]
    assert opener.urls[2].startswith(fetch_manifest.VIDEO_LIST_ENDPOINT)
    assert "pn=2" in opener.urls[2]
    assert manifest["creator_mid"] == "123"
    assert manifest["total_count"] == 3
    assert manifest["video_count"] == 3
    assert manifest["pages_fetched"] == 2
    assert manifest["page_count"] == 2
    assert manifest["categories"] == [{"tid": 188, "name": "科技", "count": 3}]
    assert [video["bvid"] for video in manifest["videos"]] == [
        "BV1xx411c7mD",
        "BV2xx411c7mD",
        "BV3xx411c7mD",
    ]
    assert manifest["videos"][0]["url"] == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert manifest["videos"][2]["is_union_video"] is True
    assert manifest["videos"][2]["is_charging_arc"] is True
    assert manifest["source"]["auth_mode"] == "none"


def test_build_manifest_injects_cookie_header_from_cookies_file(tmp_path: Path) -> None:
    cookies_file = tmp_path / "bili.cookies.txt"
    cookies_file.write_text(
        "\n".join(
            [
                "# Netscape HTTP Cookie File",
                ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tsess-value",
                ".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\tcsrf-value",
                ".example.com\tTRUE\t/\tFALSE\t0\tignored\tvalue",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    auth_config = fetch_manifest.resolve_auth_config(
        cookies_file=cookies_file,
        supported_modes=fetch_manifest.SUPPORTED_AUTH_MODES,
    )
    opener = QueueOpener(
        [
            {
                "data": {
                    "wbi_img": {
                        "img_url": (
                            "https://i0.hdslb.com/bfs/wbi/"
                            "7cd084941338484aae1ad9425b84077c.png"
                        ),
                        "sub_url": (
                            "https://i0.hdslb.com/bfs/wbi/"
                            "4932caff0ff746eab6f01bf08b70ac45.png"
                        ),
                    }
                }
            },
            {
                "code": 0,
                "message": "0",
                "data": {"list": {"tlist": {}, "vlist": []}, "page": {"count": 0, "pn": 1, "ps": 30}},
            },
        ]
    )

    manifest = fetch_manifest.build_manifest(
        "123",
        page_size=30,
        order="pubdate",
        timeout=1.0,
        auth_config=auth_config,
        opener=opener,
        now=1702204169,
    )

    assert opener.requests[0].headers["Cookie"] == "SESSDATA=sess-value; bili_jct=csrf-value"
    assert opener.requests[1].headers["Cookie"] == "SESSDATA=sess-value; bili_jct=csrf-value"
    assert manifest["source"]["auth_mode"] == "cookies_file"


def test_resolve_auth_config_ignores_unsupported_env_browser_mode(monkeypatch) -> None:
    monkeypatch.setenv("BILIBILI_AUTH_MODE", "browser")
    monkeypatch.setenv("BILIBILI_BROWSER", "chrome")

    config = fetch_manifest.resolve_auth_config(
        supported_modes=fetch_manifest.SUPPORTED_AUTH_MODES,
    )

    assert config.mode == "none"


def test_fetch_video_page_raises_on_api_error() -> None:
    opener = QueueOpener(
        [
                {
                    "data": {
                        "wbi_img": {
                            "img_url": (
                                "https://i0.hdslb.com/bfs/wbi/"
                                "7cd084941338484aae1ad9425b84077c.png"
                            ),
                            "sub_url": (
                                "https://i0.hdslb.com/bfs/wbi/"
                                "4932caff0ff746eab6f01bf08b70ac45.png"
                            ),
                        }
                    }
                },
            {"code": -412, "message": "请求被拦截", "data": {}},
        ]
    )

    with pytest.raises(fetch_manifest.BilibiliAPIError):
        fetch_manifest.build_manifest(
            "123",
            page_size=30,
            order="pubdate",
            timeout=1.0,
            opener=opener,
            now=1702204169,
        )
