"""Environnement Python du module de traînée (réseau BlurBall : torch, OpenCV…).

L'app installée n'embarque pas torch (plusieurs Go) : à la première traînée, on crée un
environnement isolé dans le dossier de données de l'utilisateur, avec `uv` (un seul .exe
qui télécharge aussi son propre Python) — aucune installation préalable requise.
Sur le Mac d'Eliot, l'environnement historique ~/Documents/Playground/.venv reste utilisé.
"""

import io, os, shutil, subprocess, sys, urllib.request, zipfile
from pathlib import Path

from pongedit.updater import data_dir

IS_WIN = sys.platform.startswith("win")
BASE = data_dir() / "trail"
ENV = BASE / "env"
UV = BASE / ("uv.exe" if IS_WIN else "uv")
PYTHON_VERSION = "3.12"
UV_URL = {
    "win32": "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
    "darwin": "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz",
    "linux": "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz",
}
# Dépendances réellement importées à l'inférence (la formation/hydra/wandb ne servent pas).
PACKAGES = ["numpy", "opencv-python-headless", "scipy", "tqdm", "pillow", "pyyaml", "attrs", "cattrs"]
NOWIN = 0x08000000 if IS_WIN else 0


def legacy_python() -> Path | None:
    """Environnement historique du Mac d'Eliot (~/Documents/Playground)."""
    p = Path.home() / "Documents" / "Playground" / ".venv" / "bin" / "python"
    return p if p.exists() else None


def env_python() -> Path:
    return ENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def trail_python() -> Path:
    """Python à utiliser pour la traînée (peut ne pas exister encore → installer())."""
    return legacy_python() or env_python()


def ready() -> bool:
    return trail_python().exists()


def _has_nvidia() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-L"], capture_output=True, timeout=10, creationflags=NOWIN).returncode == 0
    except Exception:
        return False


def _torch_index() -> str | None:
    if IS_WIN or sys.platform.startswith("linux"):
        return "https://download.pytorch.org/whl/cu128" if _has_nvidia() else "https://download.pytorch.org/whl/cpu"
    return None   # Mac : la roue standard utilise déjà la puce Apple (MPS)


def _download_uv(log) -> None:
    key = "win32" if IS_WIN else sys.platform if sys.platform in UV_URL else "linux"
    log("Téléchargement de l'outil d'installation (uv)…")
    BASE.mkdir(parents=True, exist_ok=True)
    blob = urllib.request.urlopen(UV_URL[key], timeout=60).read()
    if IS_WIN:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            member = next(n for n in z.namelist() if n.lower().endswith("uv.exe"))
            UV.write_bytes(z.read(member))
    else:
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(blob)) as t:
            member = next(m for m in t.getmembers() if m.name.endswith("/uv"))
            UV.write_bytes(t.extractfile(member).read())
        UV.chmod(0o755)


def _run(cmd, log, env=None) -> int:
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", env=env, creationflags=NOWIN)
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log(line)
    return p.wait()


def install(log=print) -> tuple[bool, str]:
    """Crée l'environnement (une seule fois). Retourne (succès, message)."""
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        if not UV.exists():
            _download_uv(log)
        env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(BASE / "python"), "UV_NO_PROGRESS": "1"}
        log(f"Création de l'environnement Python {PYTHON_VERSION}…")
        if _run([UV, "venv", "--python", PYTHON_VERSION, ENV], log, env):
            raise RuntimeError("création de l'environnement impossible")
        py = env_python()
        idx = _torch_index()
        log("Installation de torch (téléchargement de plusieurs Go, une seule fois)…")
        cmd = [UV, "pip", "install", "--python", py, "torch", "torchvision"]
        if idx:
            cmd += ["--index-url", idx]
        if _run(cmd, log, env):
            raise RuntimeError("installation de torch impossible")
        log("Installation des autres modules…")
        if _run([UV, "pip", "install", "--python", py, *PACKAGES], log, env):
            raise RuntimeError("installation des modules impossible")
        out = subprocess.run([str(py), "-c",
                              "import torch, cv2, scipy, yaml; print('cuda' if torch.cuda.is_available() else "
                              "'mps' if getattr(torch.backends,'mps',None) and torch.backends.mps.is_available() else 'cpu')"],
                             capture_output=True, text=True, creationflags=NOWIN)
        if out.returncode:
            raise RuntimeError("vérification échouée : " + out.stderr[-400:])
        log(f"Module de traînée prêt (calcul sur : {out.stdout.strip()}).")
        return True, out.stdout.strip()
    except Exception as e:
        shutil.rmtree(ENV, ignore_errors=True)   # pas d'environnement à moitié installé
        return False, str(e)
