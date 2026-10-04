"""Mise à jour automatique : télécharge le code de la dernière release GitHub.

Principe : l'app installée (ou lancée depuis le dossier du projet) embarque une version
de base du package `pongedit`. Une mise à jour est un zip du package, déposé dans le
dossier de données de l'utilisateur ; `pong_edit.py` le met en tête de `sys.path` au
lancement suivant s'il est plus récent que la version embarquée. Aucun droit
administrateur, aucun installeur à rejouer tant que les dépendances ne changent pas.

Dépendances : bibliothèque standard uniquement (le vérificateur ne doit jamais casser).
"""

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from .version import UPDATE_REPO, VERSION

API_LATEST = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
CHECK_EVERY_SEC = 120   # évite seulement de marteler GitHub quand on ouvre plusieurs fenêtres
HTTP_TIMEOUT = 8

# Résultat du dernier contrôle : "uptodate" | "installed" | "error" | "skipped"
LAST_STATE = "skipped"
LAST_NOTES = ""      # notes de la release qui vient d'être installée


def data_dir() -> Path:
    """Dossier de données de l'app, propre à chaque système."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PingPongEdit"
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "PingPongEdit"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "PingPongEdit"


UPDATES_DIR = data_dir() / "updates"
CURRENT_FILE = UPDATES_DIR / "current.json"      # {"version": "1.2.0", "path": "…"}
CHECK_FILE = UPDATES_DIR / "last_check.json"     # {"ts": 1234.5}


def parse_version(v: str) -> tuple:
    """'v1.2.3' → (1, 2, 3). Les suffixes non numériques sont ignorés."""
    out = []
    for part in v.strip().lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(candidate: str, base: str) -> bool:
    return parse_version(candidate) > parse_version(base)


# ── Au lancement : choisir quel code utiliser ─────────────────────────────────

def overlay_path_if_newer(embedded_version: str = VERSION):
    """Chemin du package de mise à jour à utiliser, ou None (code embarqué)."""
    try:
        info = json.loads(CURRENT_FILE.read_text())
        path = Path(info["path"])
        if (path / "pongedit" / "__init__.py").exists() and is_newer(info["version"], embedded_version):
            return path
    except Exception:
        pass
    return None


# ── En tâche de fond : vérifier et télécharger ────────────────────────────────

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "PingPongEdit-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read()


def _recently_checked() -> bool:
    try:
        return time.time() - json.loads(CHECK_FILE.read_text())["ts"] < CHECK_EVERY_SEC
    except Exception:
        return False


def check_and_download(force: bool = False):
    """Cherche une version plus récente et la prépare pour le prochain lancement.

    Retourne la nouvelle version (str) si une mise à jour vient d'être installée,
    sinon None. Ne lève jamais : une panne réseau ou GitHub ne doit pas gêner l'app.
    """
    global LAST_STATE, LAST_NOTES
    LAST_STATE = "error"
    try:
        if not force and _recently_checked():
            LAST_STATE = "skipped"
            return None
        UPDATES_DIR.mkdir(parents=True, exist_ok=True)
        release = json.loads(_get(API_LATEST))
        CHECK_FILE.write_text(json.dumps({"ts": time.time()}))

        tag = release.get("tag_name", "")
        # On compare à ce qui tourne déjà : le plus récent entre embarqué et mise à jour posée.
        running = VERSION
        try:
            running_info = json.loads(CURRENT_FILE.read_text())
            if is_newer(running_info["version"], running):
                running = running_info["version"]
        except Exception:
            pass
        if not tag or not is_newer(tag, running):
            LAST_STATE = "uptodate"
            return None

        assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
        zip_name = next((n for n in assets if n.startswith("pongedit-") and n.endswith(".zip")), None)
        if not zip_name:
            return None
        blob = _get(assets[zip_name])

        # Empreinte attendue : fichier `<zip>.sha256` joint à la release.
        want = assets.get(zip_name + ".sha256")
        if not want:
            return None   # pas d'empreinte = pas d'installation (corruption silencieuse)
        expected = _get(want).decode().split()[0].strip().lower()
        if hashlib.sha256(blob).hexdigest() != expected:
            return None

        dest = UPDATES_DIR / tag.lstrip("vV")
        tmp = UPDATES_DIR / (dest.name + ".part")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        zpath = tmp / "pkg.zip"
        zpath.write_bytes(blob)
        with zipfile.ZipFile(zpath) as z:
            for m in z.namelist():            # refuse tout chemin qui sort du dossier
                target = (tmp / m).resolve()
                if not str(target).startswith(str(tmp.resolve())):
                    raise ValueError(f"chemin suspect dans le zip: {m}")
            z.extractall(tmp)
        zpath.unlink()
        if not (tmp / "pongedit" / "__init__.py").exists():
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        shutil.rmtree(dest, ignore_errors=True)
        tmp.rename(dest)

        # Bascule atomique : current.json n'est écrit qu'une fois le dossier complet.
        cur_tmp = CURRENT_FILE.with_suffix(".tmp")
        LAST_NOTES = (release.get("body") or "").strip()
        cur_tmp.write_text(json.dumps({"version": tag.lstrip("vV"), "path": str(dest), "notes": LAST_NOTES}))
        cur_tmp.replace(CURRENT_FILE)

        # Ménage : on garde la nouvelle et la précédente (retour arrière possible).
        old = sorted([p for p in UPDATES_DIR.iterdir() if p.is_dir() and not p.name.endswith(".part")],
                     key=lambda p: parse_version(p.name), reverse=True)
        for p in old[2:]:
            shutil.rmtree(p, ignore_errors=True)
        LAST_STATE = "installed"
        return tag.lstrip("vV")
    except Exception as e:
        print(f"Mise à jour: {e}")
        return None
