"""Helpers génériques : slot d'instance, dossiers, hash vidéo, ffprobe, formatage."""

import os, sys, uuid, subprocess
from functools import lru_cache
from pathlib import Path

# Racine du projet (dossier de pong_edit.py) : ce module vit dans pongedit/.
PROJECT_DIR = Path(__file__).parent.parent
ENTRY_SCRIPT = str(PROJECT_DIR / "pong_edit.py")


def _resolve_slot() -> str:
    """Instance identifier so plusieurs fenêtres tournent en parallèle sans se heurter.
    Source : --slot N  /  --slot=N  /  env PONG_SLOT. Défaut = "1" (fenêtre principale)."""
    for i, a in enumerate(sys.argv):
        if a == "--slot" and i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip() or "1"
        if a.startswith("--slot="):
            return a.split("=", 1)[1].strip() or "1"
    return os.environ.get("PONG_SLOT", "").strip() or "1"


INSTANCE_SLOT = _resolve_slot()

EXPORTS_DIR     = Path.home() / "Desktop" / "pong_exports"
# Les sessions restent partagées : elles sont indexées par hash de la vidéo, donc
# une fenêtre qui édite un autre match écrit dans un autre fichier — pas de collision.
SESSIONS_DIR    = PROJECT_DIR / "sessions"


def _atomic_write_text(path: Path, text: str) -> None:
    """Écriture atomique : évite qu'une 2ᵉ fenêtre lise/corrompe un fichier à moitié écrit."""
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


def _reserve_output_path(path: Path) -> Path:
    """Renvoie un chemin de sortie LIBRE (suffixe _2, _3… si déjà pris) et le réserve
    aussitôt via une création exclusive. Empêche deux exports simultanés (ex. deux
    fenêtres, ou même nom de joueurs) d'écrire dans le même fichier et de le corrompre.
    Le O_EXCL échoue aussi sur une variante de casse (disque macOS insensible à la casse),
    donc "Eliot_vs_Yuri" et "Eliot_vs_yuri" ne collisionnent plus."""
    stem, suffix, parent = path.stem, path.suffix, path.parent
    candidate = path
    i = 2
    while True:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            return candidate
        except FileExistsError:
            candidate = parent / f"{stem}_{i}{suffix}"
            i += 1


@lru_cache(maxsize=64)
def _video_hash_cached(path: str, size: int, mtime_ns: int) -> str:
    import hashlib
    h = hashlib.blake2b(digest_size=8)
    h.update(size.to_bytes(8, "little"))
    chunk = 65536
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, 2)
            h.update(f.read(chunk))
    return h.hexdigest()  # 16 hex chars


def _video_hash(path: str) -> str:
    """Hash d'identité d'une vidéo (clé des sessions). Mémoïsé sur (chemin, taille,
    date de modif) : le combo « vidéos récentes » le recalculait pour 5 fichiers à
    chaque rafraîchissement, ce qui coûte un vrai accès disque sur un gros fichier
    pas encore en cache (~47 ms mesurés à froid)."""
    st = Path(path).stat()
    return _video_hash_cached(path, st.st_size, st.st_mtime_ns)


def _get_video_dimensions(path: str) -> tuple[int, int]:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=s=x:p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        # Les ffprobe récents ajoutent un champ vide final ("3840x2160x") : on ne
        # garde que les deux premiers nombres, sinon on retombait sur 1920×1080
        # et toute la géométrie 4K (vignette, rotation) était calculée en HD.
        parts = [x for x in r.stdout.strip().split("x") if x.strip().isdigit()]
        w, h = int(parts[0]), int(parts[1])
        if w <= 0 or h <= 0:
            raise ValueError("dimensions nulles")
        return w, h
    except Exception:
        return 1920, 1080


def _get_fps(video_path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10,
        )
        parts = r.stdout.strip().split("/")
        return float(parts[0]) / float(parts[1]) if len(parts) == 2 else 30.0
    except Exception:
        return 30.0


def gen_id() -> str:
    return uuid.uuid4().hex[:8]

def fmt_time(s: float) -> str:
    m = int(s // 60)
    return f"{m}:{s % 60:05.2f}"

def _fmt_chapter_time(total_seconds: int) -> str:
    total_seconds = max(0, total_seconds)
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


@lru_cache(maxsize=32)
def _probe_video_props(path: str) -> dict:
    """Propriétés de la source servant à caler l'encodage de sortie."""
    props = {"bitrate": 0, "pix_fmt": "yuv420p", "width": 1920, "height": 1080,
             "fps": 30.0, "codec": ""}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=bit_rate,pix_fmt,width,height,r_frame_rate,codec_name,"
                              "color_transfer,color_primaries,color_space",
             "-show_entries", "format=bit_rate",
             "-of", "default=nw=1", path],
            capture_output=True, text=True, timeout=15,
        )
        vals: dict[str, str] = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                vals.setdefault(k.strip(), v.strip())   # le flux prime sur le conteneur
        if vals.get("bit_rate", "N/A").isdigit():
            props["bitrate"] = int(vals["bit_rate"])
        for key in ("width", "height"):
            if vals.get(key, "").isdigit():
                props[key] = int(vals[key])
        if vals.get("pix_fmt"):
            props["pix_fmt"] = vals["pix_fmt"]
        if vals.get("codec_name"):
            props["codec"] = vals["codec_name"]
        for key in ("color_transfer", "color_primaries", "color_space"):
            v = vals.get(key, "")
            props[key] = "" if v in ("", "unknown", "N/A") else v
        rate = vals.get("r_frame_rate", "")
        if "/" in rate:
            num, den = rate.split("/")
            if float(den) > 0:
                props["fps"] = float(num) / float(den)
    except Exception:
        pass
    return props
