#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_NAME = "Ping Pong Edit"
PYTHON = Path(sys.executable).resolve()
TARGET = ROOT / "pong_edit.py"
BUNDLE_ID = "com.local.pingpongedit"


def write_file(path: Path, content: str, executable: bool = False) -> None:
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def shell_path(path: Path) -> str:
    return str(path).replace('"', '\\"')


def make_macos() -> list[Path]:
    app_dir = ROOT / f"{APP_NAME}.app"
    if app_dir.exists():
        shutil.rmtree(app_dir)

    contents_dir = app_dir / "Contents"
    macos_dir = contents_dir / "MacOS"
    contents_dir.mkdir(parents=True, exist_ok=True)
    macos_dir.mkdir(parents=True, exist_ok=True)

    info_plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Ping Pong Edit</string>
    <key>CFBundleDisplayName</key>
    <string>Ping Pong Edit</string>
    <key>CFBundleIdentifier</key>
    <string>com.local.pingpongedit</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>PingPongEdit</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
"""
    write_file(contents_dir / "Info.plist", info_plist)

    launcher = f"""#!/bin/bash
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
ROOT="{shell_path(ROOT)}"
exec "{shell_path(PYTHON)}" "$ROOT/{TARGET.name}"
"""
    write_file(macos_dir / "PingPongEdit", launcher, executable=True)
    return [app_dir]


def make_linux() -> list[Path]:
    shell_launcher = ROOT / "ping-pong-edit.sh"
    shell_content = f"""#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
exec "{shell_path(PYTHON)}" "$ROOT/{TARGET.name}"
"""
    write_file(shell_launcher, shell_content, executable=True)

    desktop_file = ROOT / f"{APP_NAME}.desktop"
    desktop_content = f"""[Desktop Entry]
Type=Application
Version=1.0
Name={APP_NAME}
Comment=Ping pong match editor
Exec=/usr/bin/env bash "{shell_launcher}"
Path={ROOT}
Terminal=false
Categories=AudioVideo;Video;
"""
    write_file(desktop_file, desktop_content, executable=True)
    return [shell_launcher, desktop_file]


def make_windows() -> list[Path]:
    launcher = ROOT / f"{APP_NAME}.bat"
    content = f"""@echo off
set ROOT=%~dp0
"{PYTHON}" "%ROOT%{TARGET.name}"
"""
    write_file(launcher, content)
    return [launcher]


def build_targets(targets: list[str]) -> list[Path]:
    created: list[Path] = []
    if "mac" in targets:
        created.extend(make_macos())
    if "linux" in targets:
        created.extend(make_linux())
    if "windows" in targets:
        created.extend(make_windows())
    return created


def default_targets() -> list[str]:
    if sys.platform == "darwin":
        return ["mac"]
    if sys.platform.startswith("linux"):
        return ["linux"]
    if os.name == "nt":
        return ["windows"]
    raise SystemExit(f"Unsupported platform: {sys.platform}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create desktop launchers for Ping Pong Edit."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate launchers for macOS, Linux, and Windows.",
    )
    args = parser.parse_args()

    targets = ["mac", "linux", "windows"] if args.all else default_targets()
    created = build_targets(targets)

    print("Created:")
    for path in created:
        print(f"  {path}")
    print("")
    print("Build each launcher on its target OS so it captures the correct Python path.")


if __name__ == "__main__":
    main()
