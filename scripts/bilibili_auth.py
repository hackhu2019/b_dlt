"""Shared Bilibili authentication helpers for local scripts."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence


AUTH_MODE_NONE = "none"
AUTH_MODE_BROWSER = "browser"
AUTH_MODE_COOKIES_FILE = "cookies_file"
AUTH_MODE_COOKIE_HEADER = "cookie_header"
AUTH_MODES = (
    AUTH_MODE_NONE,
    AUTH_MODE_BROWSER,
    AUTH_MODE_COOKIES_FILE,
    AUTH_MODE_COOKIE_HEADER,
)
ENV_AUTH_MODE = "BILIBILI_AUTH_MODE"
ENV_BROWSER = "BILIBILI_BROWSER"
ENV_COOKIES_FILE = "BILIBILI_COOKIES_FILE"
ENV_COOKIE_HEADER = "BILIBILI_COOKIE_HEADER"


class BilibiliAuthError(RuntimeError):
    """Raised when the Bilibili authentication configuration is invalid."""


@dataclass(frozen=True)
class BilibiliAuthConfig:
    mode: str = AUTH_MODE_NONE
    browser: Optional[str] = None
    cookies_file: Optional[Path] = None
    cookie_header: Optional[str] = None


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


def add_auth_arguments(
    parser: argparse.ArgumentParser,
    *,
    supported_modes: Sequence[str] = AUTH_MODES,
) -> None:
    parser.add_argument(
        "--auth-mode",
        choices=tuple(supported_modes),
        default=None,
        help=(
            "Bilibili auth mode. Defaults to environment variables if omitted "
            "({}).".format(ENV_AUTH_MODE)
        ),
    )
    if AUTH_MODE_BROWSER in supported_modes:
        parser.add_argument(
            "--browser",
            default=None,
            help=(
                "Browser name used by yt-dlp --cookies-from-browser "
                "({}).".format(ENV_BROWSER)
            ),
        )
    parser.add_argument(
        "--cookies-file",
        type=Path,
        default=None,
        help=(
            "Optional Netscape-format cookies file "
            "({}).".format(ENV_COOKIES_FILE)
        ),
    )
    if AUTH_MODE_COOKIE_HEADER in supported_modes:
        parser.add_argument(
            "--cookie-header",
            default=None,
            help=(
                "Raw Cookie header value "
                "({}).".format(ENV_COOKIE_HEADER)
            ),
        )


def resolve_auth_config(
    *,
    auth_mode: Optional[str] = None,
    browser: Optional[str] = None,
    cookies_file: Optional[Path] = None,
    cookie_header: Optional[str] = None,
    supported_modes: Sequence[str] = AUTH_MODES,
) -> BilibiliAuthConfig:
    explicit_browser = _normalize_optional_string(browser)
    explicit_cookies_file = _normalize_path(cookies_file)
    explicit_cookie_header = normalize_cookie_header(cookie_header)
    env_browser = _read_env_string(ENV_BROWSER)
    env_cookies_file = _read_env_path(ENV_COOKIES_FILE)
    env_cookie_header = normalize_cookie_header(_read_env_string(ENV_COOKIE_HEADER))
    normalized_browser = explicit_browser or env_browser
    normalized_cookies_file = explicit_cookies_file or env_cookies_file
    normalized_cookie_header = explicit_cookie_header or env_cookie_header
    env_mode = _read_env_string(ENV_AUTH_MODE)

    has_explicit_auth_inputs = any(
        value is not None
        for value in (explicit_browser, explicit_cookies_file, explicit_cookie_header)
    )
    if auth_mode is not None:
        mode = normalize_auth_mode(auth_mode)
        validate_supported_mode(mode, supported_modes)
    elif has_explicit_auth_inputs:
        mode = infer_auth_mode(
            browser=explicit_browser,
            cookies_file=explicit_cookies_file,
            cookie_header=explicit_cookie_header,
        )
        validate_supported_mode(mode, supported_modes)
    elif env_mode is not None:
        mode = normalize_auth_mode(env_mode)
        if mode not in supported_modes:
            mode = infer_supported_auth_mode(
                browser=env_browser,
                cookies_file=env_cookies_file,
                cookie_header=env_cookie_header,
                supported_modes=supported_modes,
            )
    else:
        mode = infer_supported_auth_mode(
            browser=normalized_browser,
            cookies_file=normalized_cookies_file,
            cookie_header=normalized_cookie_header,
            supported_modes=supported_modes,
        )
    mode = normalize_auth_mode(mode)
    validate_supported_mode(mode, supported_modes)

    if mode == AUTH_MODE_BROWSER:
        if normalized_browser is None:
            raise BilibiliAuthError(
                "Bilibili auth mode 'browser' requires --browser or {}.".format(ENV_BROWSER)
            )
        return BilibiliAuthConfig(mode=mode, browser=normalized_browser)

    if mode == AUTH_MODE_COOKIES_FILE:
        if normalized_cookies_file is None:
            raise BilibiliAuthError(
                "Bilibili auth mode 'cookies_file' requires --cookies-file or {}.".format(
                    ENV_COOKIES_FILE
                )
            )
        if not normalized_cookies_file.is_file():
            raise BilibiliAuthError(
                "Cookies file does not exist: {}".format(normalized_cookies_file)
            )
        return BilibiliAuthConfig(mode=mode, cookies_file=normalized_cookies_file)

    if mode == AUTH_MODE_COOKIE_HEADER:
        if normalized_cookie_header is None:
            raise BilibiliAuthError(
                "Bilibili auth mode 'cookie_header' requires --cookie-header or {}.".format(
                    ENV_COOKIE_HEADER
                )
            )
        return BilibiliAuthConfig(mode=mode, cookie_header=normalized_cookie_header)

    return BilibiliAuthConfig(mode=AUTH_MODE_NONE)


def build_yt_dlp_auth_args(config: BilibiliAuthConfig) -> list[str]:
    if config.mode == AUTH_MODE_NONE:
        return []
    if config.mode == AUTH_MODE_BROWSER:
        return ["--cookies-from-browser", str(config.browser)]
    if config.mode == AUTH_MODE_COOKIES_FILE:
        return ["--cookies", str(config.cookies_file)]
    if config.mode == AUTH_MODE_COOKIE_HEADER:
        return ["--add-header", "Cookie: {}".format(config.cookie_header)]
    raise BilibiliAuthError("Unsupported Bilibili auth mode: {}".format(config.mode))


def build_request_headers(
    base_headers: Mapping[str, str],
    config: BilibiliAuthConfig,
) -> dict[str, str]:
    headers = dict(base_headers)
    if config.mode == AUTH_MODE_NONE:
        return headers
    if config.mode == AUTH_MODE_BROWSER:
        raise BilibiliAuthError(
            "Bilibili auth mode 'browser' is only supported by yt-dlp downloads."
        )
    if config.mode == AUTH_MODE_COOKIE_HEADER:
        headers["Cookie"] = str(config.cookie_header)
        return headers
    if config.mode == AUTH_MODE_COOKIES_FILE:
        headers["Cookie"] = load_cookie_header_from_file(config.cookies_file)
        return headers
    raise BilibiliAuthError("Unsupported Bilibili auth mode: {}".format(config.mode))


def load_cookie_header_from_file(path: Optional[Path]) -> str:
    if path is None:
        raise BilibiliAuthError("Cookies file path is required.")
    if not path.is_file():
        raise BilibiliAuthError("Cookies file does not exist: {}".format(path))

    cookies: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_netscape_cookie_line(line)
        if parsed is None:
            continue
        domain, name, value = parsed
        if not is_bilibili_domain(domain):
            continue
        cookies[name] = value

    if not cookies:
        raise BilibiliAuthError("No Bilibili cookies found in file: {}".format(path))
    return "; ".join("{}={}".format(name, value) for name, value in cookies.items())


def parse_netscape_cookie_line(line: str) -> Optional[tuple[str, str, str]]:
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("#HttpOnly_"):
        stripped = stripped[len("#HttpOnly_") :]
    elif stripped.startswith("#"):
        return None

    fields = stripped.split("\t")
    if len(fields) != 7:
        return None
    domain = fields[0].strip()
    name = fields[5].strip()
    value = fields[6].strip()
    if not domain or not name or not value:
        return None
    return domain, name, value


def is_bilibili_domain(domain: str) -> bool:
    normalized = domain.strip().lstrip(".").lower()
    return normalized == "bilibili.com" or normalized.endswith(".bilibili.com")


def infer_auth_mode(
    *,
    browser: Optional[str],
    cookies_file: Optional[Path],
    cookie_header: Optional[str],
) -> str:
    if cookie_header is not None:
        return AUTH_MODE_COOKIE_HEADER
    if cookies_file is not None:
        return AUTH_MODE_COOKIES_FILE
    if browser is not None:
        return AUTH_MODE_BROWSER
    return AUTH_MODE_NONE


def infer_supported_auth_mode(
    *,
    browser: Optional[str],
    cookies_file: Optional[Path],
    cookie_header: Optional[str],
    supported_modes: Sequence[str],
) -> str:
    filtered_browser = browser if AUTH_MODE_BROWSER in supported_modes else None
    filtered_cookies_file = (
        cookies_file if AUTH_MODE_COOKIES_FILE in supported_modes else None
    )
    filtered_cookie_header = (
        cookie_header if AUTH_MODE_COOKIE_HEADER in supported_modes else None
    )
    return infer_auth_mode(
        browser=filtered_browser,
        cookies_file=filtered_cookies_file,
        cookie_header=filtered_cookie_header,
    )


def normalize_auth_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in AUTH_MODES:
        raise BilibiliAuthError(
            "Unsupported Bilibili auth mode: {}.".format(value)
        )
    return mode


def normalize_cookie_header(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.lower().startswith("cookie:"):
        normalized = normalized.split(":", 1)[1].strip()
    return normalized or None


def validate_supported_mode(mode: str, supported_modes: Sequence[str]) -> None:
    if mode in supported_modes:
        return
    raise BilibiliAuthError(
        "Bilibili auth mode '{}' is not supported here. Allowed modes: {}.".format(
            mode,
            ", ".join(supported_modes),
        )
    )


def _normalize_optional_string(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_path(value: Optional[Path]) -> Optional[Path]:
    if value is None:
        return None
    return value.expanduser()


def _read_env_string(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _read_env_path(name: str) -> Optional[Path]:
    value = _read_env_string(name)
    if value is None:
        return None
    return Path(value).expanduser()
