"""Export Bilibili browser cookies into a reusable Netscape-format file."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bilibili_auth import ENV_BROWSER, load_dotenv


DEFAULT_BROWSER = "chrome"
Runner = Callable[..., subprocess.CompletedProcess]
Which = Callable[[str], Optional[str]]
HELPER_CODE = """
import json
import sys
from yt_dlp import cookies


def is_bilibili_domain(domain):
    normalized = (domain or "").strip().lstrip(".").lower()
    return normalized == "bilibili.com" or normalized.endswith(".bilibili.com")


browser, output_file, profile, keyring, container = sys.argv[1:6]
browser = browser or "chrome"
profile = profile or None
keyring = keyring or None
container = container or None
cookie_jar = cookies.extract_cookies_from_browser(
    browser,
    profile,
    cookies.YDLLogger(),
    keyring=keyring,
    container=container,
)
filtered_jar = cookies.YoutubeDLCookieJar(output_file)
count = 0
for cookie in cookie_jar:
    if not is_bilibili_domain(getattr(cookie, "domain", "")):
        continue
    filtered_jar.set_cookie(cookie)
    count += 1

if count == 0:
    raise SystemExit("No Bilibili cookies found in the selected browser profile.")

filtered_jar.save(ignore_discard=True, ignore_expires=True)
print(json.dumps({"count": count}))
""".strip()


class ExportCookiesError(RuntimeError):
    """Raised when browser cookies cannot be exported."""


@dataclass(frozen=True)
class ExportCookiesSummary:
    output_file: Path
    browser: str
    cookie_count: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Bilibili browser cookies into a Netscape-format file."
    )
    parser.add_argument(
        "--output-file",
        required=True,
        type=Path,
        help="Destination Netscape-format cookies file.",
    )
    parser.add_argument(
        "--browser",
        default=os.getenv(ENV_BROWSER, DEFAULT_BROWSER),
        help="Browser name used by yt-dlp cookies extractor.",
    )
    parser.add_argument(
        "--profile",
        help="Optional browser profile name or path.",
    )
    parser.add_argument(
        "--keyring",
        help="Optional browser keyring name for Chromium-based browsers on Linux.",
    )
    parser.add_argument(
        "--container",
        help="Optional Firefox container name.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing cookies file.",
    )
    return parser


def resolve_yt_dlp_python(which: Which = shutil.which) -> str:
    if importlib.util.find_spec("yt_dlp") is not None:
        return sys.executable

    executable = which("yt-dlp")
    if executable is None:
        raise ExportCookiesError("Missing required executable: yt-dlp.")

    try:
        first_line = Path(executable).read_text(encoding="utf-8").splitlines()[0].strip()
    except FileNotFoundError as exc:
        raise ExportCookiesError("yt-dlp executable not found: {}".format(executable)) from exc
    except IndexError as exc:
        raise ExportCookiesError("yt-dlp executable is empty: {}".format(executable)) from exc
    except UnicodeDecodeError as exc:
        raise ExportCookiesError(
            "Unsupported yt-dlp install format. Please use a Python-script installation."
        ) from exc

    if not first_line.startswith("#!"):
        raise ExportCookiesError(
            "Failed to resolve yt-dlp Python runtime from {}.".format(executable)
        )

    python_executable = first_line[2:].strip()
    if not python_executable:
        raise ExportCookiesError(
            "Failed to resolve yt-dlp Python runtime from {}.".format(executable)
        )
    return python_executable


def build_helper_command(
    python_executable: str,
    *,
    browser: str,
    output_file: Path,
    profile: Optional[str],
    keyring: Optional[str],
    container: Optional[str],
) -> list[str]:
    return [
        python_executable,
        "-c",
        HELPER_CODE,
        browser,
        str(output_file),
        profile or "",
        keyring or "",
        container or "",
    ]


def run_export(
    output_file: Path,
    *,
    browser: str,
    profile: Optional[str] = None,
    keyring: Optional[str] = None,
    container: Optional[str] = None,
    overwrite: bool = False,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
) -> ExportCookiesSummary:
    if not browser.strip():
        raise ExportCookiesError("--browser must not be empty.")

    destination = output_file.expanduser()
    if destination.exists() and not overwrite:
        raise ExportCookiesError(
            "Output file already exists: {}. Use --overwrite to replace it.".format(
                destination
            )
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    python_executable = resolve_yt_dlp_python(which=which)
    command = build_helper_command(
        python_executable,
        browser=browser.strip(),
        output_file=destination,
        profile=profile,
        keyring=keyring,
        container=container,
    )
    result = runner(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        details = (result.stderr or "").strip() or (result.stdout or "").strip()
        raise ExportCookiesError(details or "Failed to export browser cookies.")

    if not destination.is_file():
        raise ExportCookiesError("Cookies export did not create output file: {}".format(destination))

    try:
        destination.chmod(0o600)
    except OSError:
        pass

    cookie_count = parse_cookie_count(result.stdout)
    return ExportCookiesSummary(
        output_file=destination,
        browser=browser.strip(),
        cookie_count=cookie_count,
    )


def parse_cookie_count(stdout: str) -> int:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ExportCookiesError("Missing export summary from yt-dlp cookies helper.")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ExportCookiesError("Invalid export summary from yt-dlp cookies helper.") from exc
    try:
        return int(payload["count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExportCookiesError("Invalid cookie count returned by yt-dlp cookies helper.") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        summary = run_export(
            args.output_file,
            browser=args.browser,
            profile=args.profile,
            keyring=args.keyring,
            container=args.container,
            overwrite=args.overwrite,
        )
    except ExportCookiesError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    print(
        "Exported {} Bilibili cookies from {} to {}".format(
            summary.cookie_count,
            summary.browser,
            summary.output_file,
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
