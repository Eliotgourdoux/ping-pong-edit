#!/usr/bin/env python3
"""Publie une version : zip du package + empreinte SHA-256 + release GitHub.

Usage : python3 release.py 1.1.3   (notes lues dans pongedit/assets/CHANGELOG.md)

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
REPO = "Eliotgourdoux/ping-pong-edit"                    # PRIVÉ : code source + construction
PUBLIC_REPO = "Eliotgourdoux/ping-pong-edit-releases"    # PUBLIC : uniquement les téléchargements


def _changelog_notes(version: str) -> str:
    """Section `## <version> — …` de CHANGELOG.md : c'est le texte de la release GitHub."""
    out, take = [], False
    for line in (ROOT / "pongedit" / "assets" / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if take:
                break
            take = line[3:].split("—")[0].strip() == version
            continue
        if take:
            out.append(line)
    return "\n".join(out).strip()


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("usage: release.py X.Y.Z")
    version = sys.argv[1]
    notes = _changelog_notes(version)
    if not notes:
        sys.exit(f"Écris d'abord les notes de {version} dans pongedit/assets/CHANGELOG.md "
                 f"(section « ## {version} — date »).")

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
    # Le workflow GitHub (installeur .exe) démarre dès que le tag est poussé : il peut avoir
    # déjà créé la release. On crée si absente, sinon on y ajoute le zip.
    exists = subprocess.run(["gh", "release", "view", f"v{version}", "--repo", REPO],
                            capture_output=True).returncode == 0
    if exists:
        subprocess.run(["gh", "release", "upload", f"v{version}", str(zpath), str(sha),
                        "--repo", REPO, "--clobber"], check=True)
    else:
        subprocess.run(["gh", "release", "create", f"v{version}", str(zpath), str(sha),
                        "--repo", REPO, "--title", f"v{version}", "--notes", notes], check=True)
    print(f"Release v{version} publiée.")


def mirror_to_public(version: str, notes: str, dist: Path, zpath: Path, sha: Path):
    """Attend l'installeur construit par GitHub Actions, puis publie les 3 fichiers dans le
    dépôt public (le dépôt du code reste privé : aucune archive « Source code » exposée)."""
    import time
    exe_name = f"PingPongEdit-Setup-{version}.exe"
    exe = dist / exe_name
    for _ in range(90):                                   # ≤ 15 min
        names = subprocess.run(["gh", "release", "view", f"v{version}", "--repo", REPO, "--json",
                                "assets", "-q", ".assets[].name"], capture_output=True, text=True).stdout.split()
        if exe_name in names:
            break
        time.sleep(10)
    else:
        sys.exit(f"L'installeur {exe_name} n'est pas apparu : relance la copie à la main.")
    exe.unlink(missing_ok=True)
    subprocess.run(["gh", "release", "download", f"v{version}", "--repo", REPO, "--pattern", exe_name,
                    "--dir", str(dist)], check=True)
    subprocess.run(["gh", "release", "delete", f"v{version}", "--repo", PUBLIC_REPO, "--yes",
                    "--cleanup-tag"], capture_output=True)
    subprocess.run(["gh", "release", "create", f"v{version}", str(exe), str(zpath), str(sha),
                    "--repo", PUBLIC_REPO, "--title", f"v{version}", "--notes", notes], check=True)
    print(f"Release v{version} publiée sur {PUBLIC_REPO}.")


if __name__ == "__main__":
    main()
