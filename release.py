#!/usr/bin/env python3
"""Publie une version : zip du package + empreinte SHA-256 + release GitHub.

Usage : python3 release.py 1.1.0 "notes de version"

L'app installée télécharge `pongedit-<version>.zip` (et vérifie `.sha256`) au prochain
lancement. Incrémente automatiquement pongedit/version.py.
"""
import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "Eliotgourdoux/ping-pong-edit"


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("usage: release.py X.Y.Z [notes]")
    version, notes = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else f"Version {sys.argv[1]}")

    vfile = ROOT / "pongedit" / "version.py"
    vfile.write_text(re.sub(r'VERSION = "[^"]*"', f'VERSION = "{version}"', vfile.read_text()))

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    zpath = dist / f"pongedit-{version}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((ROOT / "pongedit").rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and ".bak" not in f.name:
                z.write(f, f.relative_to(ROOT))
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    sha = dist / (zpath.name + ".sha256")
    sha.write_text(f"{digest}  {zpath.name}\n")

    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0:
        subprocess.run(["git", "commit", "-m", f"Version {version}"], cwd=ROOT, check=True)
    subprocess.run(["git", "tag", f"v{version}"], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "origin", "HEAD", "--tags"], cwd=ROOT, check=True)
    subprocess.run(["gh", "release", "create", f"v{version}", str(zpath), str(sha),
                    "--repo", REPO, "--title", f"v{version}", "--notes", notes], check=True)
    print(f"Release v{version} publiée.")


if __name__ == "__main__":
    main()
