"""Point d'entrée de la version installée (.exe Windows / .app macOS).

PyInstaller empaquette ce fichier ; le code de l'app (`pong_edit.py` + `pongedit/`) est
livré comme des FICHIERS à côté, pas dans l'exécutable. C'est ce qui permet à la mise à
jour automatique (pongedit/updater.py) de le remplacer sans réinstaller.
"""
import os
import subprocess
import sys
from pathlib import Path


def _setup_frozen():
    if not getattr(sys, "frozen", False):
        return
    exe_dir = Path(sys.executable).resolve().parent
    bundle = Path(getattr(sys, "_MEIPASS", exe_dir))
    sys.path.insert(0, str(bundle / "code"))
    # ffmpeg / ffprobe embarqués à côté de l'exécutable
    os.environ["PATH"] = str(exe_dir / "bin") + os.pathsep + os.environ.get("PATH", "")

    if sys.platform.startswith("win"):
        # Appli sans console : pas de fenêtre noire qui clignote à chaque appel ffmpeg.
        _orig = subprocess.Popen.__init__

        def _init(self, *a, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
            _orig(self, *a, **kw)

        subprocess.Popen.__init__ = _init
        base = os.environ.get("APPDATA") or str(Path.home())
    else:
        base = str(Path.home() / "Library" / "Application Support")

    # Sans console, stdout/stderr n'existent pas : on journalise dans un fichier.
    try:
        logdir = Path(base) / "PingPongEdit"
        logdir.mkdir(parents=True, exist_ok=True)
        log = open(logdir / "log.txt", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    except Exception:
        pass


_setup_frozen()

import pong_edit  # noqa: E402

if __name__ == "__main__":
    pong_edit.main()
