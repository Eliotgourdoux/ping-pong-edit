"""Traînée de balle : lancement du pipeline externe (TrailWorker)."""

import os, subprocess, sys, threading, re, time, signal
from pathlib import Path
from PySide6.QtCore import QThread, Signal

from pongedit.utils import EXPORTS_DIR, _reserve_output_path
from pongedit.ball import envsetup


# ── Traînée de balle ──────────────────────────────────────────────────────────
# Le calcul (réseau BlurBall + trajectoire) est livré avec l'app dans `engine/`
# (ball_trail2.py, blurball_infer.py, poids du réseau). On le lance en sous-processus,
# avec l'environnement Python de `envsetup` (torch n'est pas dans l'exécutable), et on lit
# ses lignes « PROGRESS <phase> <fait> <total> » pour alimenter la barre.
TRAIL_DIR    = Path(__file__).resolve().parent / "engine"
TRAIL_SCRIPT = TRAIL_DIR / "ball_trail2.py"
TRAIL_PYTHON = envsetup.trail_python()
IS_MAC       = sys.platform == "darwin"
TRAIL_CACHE  = EXPORTS_DIR / ".trail_cache"
# Part de chaque passe dans la barre : la détection neuronale domine largement
# (~0,18 s/frame), le suivi est rapide. Le rendu garde sa tranche pour le
# dessin de la traînée image par image — l'encodage, lui, est passé au matériel
# et ne coûte plus grand-chose.
TRAIL_PHASES = {"det": (0.0, 0.50), "track": (0.50, 0.58),
                "validate": (0.58, 0.62), "det2": (0.62, 0.78),
                "render": (0.78, 1.0)}
# Silence prolongé du sous-processus = quelque chose cloche (iCloud le plus souvent).
TRAIL_STALL_SEC = 45.0
TRAIL_LABELS = {"det":      "🎾  Détection de la balle (réseau)…",
                "track":    "📐  Calcul de la trajectoire…",
                "validate": "🧮  Validation des trajectoires…",
                "det2":     "🔍  Détection fine sur les trous…",
                "render":   "🎬  Rendu de la traînée…"}


class TrailWorker(QThread):
    progress = Signal(float)          # 0 → 100
    stage    = Signal(str)
    machine  = Signal(str)            # machine qui calcule, pour l'UI
    done     = Signal(str)
    error    = Signal(str)
    canceled = Signal()

    def __init__(self, video_path: str, out_name: str):
        super().__init__()
        self.video_path = video_path
        self.out_name   = out_name
        self._cancelled = False
        self._proc      = None
        self._last_out  = time.monotonic()

    def cancel(self):
        self._cancelled = True
        p = self._proc
        if p is not None and p.poll() is None:
            try:                       # tout l'arbre : ball_trail2 + blurball_infer + ffmpeg
                if sys.platform.startswith("win"):
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                                   capture_output=True, creationflags=0x08000000)
                else:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except OSError:
                pass

    def _cache_key(self) -> str:
        """Clé de cache : nom + taille du source, pour relancer sans recalculer."""
        src = Path(self.video_path)
        try:
            return f"{re.sub(r'[^A-Za-z0-9_-]+', '_', src.stem)}_{src.stat().st_size}"
        except OSError:
            return re.sub(r"[^A-Za-z0-9_-]+", "_", src.stem)

    def _watch_stall(self):
        """Prévient quand le sous-processus ne dit plus rien pendant longtemps.

        Cas vécu : le dossier du réseau est sur iCloud ; s'il a été évincé,
        `import torch` attend le retéléchargement de ~26 000 fichiers et la barre
        reste à 0 sans la moindre explication.
        """
        warned = False
        while True:
            p = self._proc
            if self._cancelled or p is None or p.poll() is not None:
                return
            idle = time.monotonic() - self._last_out
            if idle > TRAIL_STALL_SEC and not warned:
                warned = True
                self.stage.emit(
                    "⏳  Rien depuis 45 s — fichiers iCloud sans doute en cours de "
                    "retéléchargement (dossier du réseau évincé)…")
            elif idle <= TRAIL_STALL_SEC and warned:
                warned = False
            time.sleep(2.0)

    def _pump(self, cmd, phases, cwd=None):
        """Lance `cmd` et traduit ses lignes PROGRESS en avancement de barre.

        Renvoie (code de retour, 200 dernières lignes) — la queue sert au message
        d'erreur, une trace complète serait illisible dans l'UI.
        """
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=cwd,
            # Windows : la console est en cp1252, or le script écrit des « → » / accents.
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            **({"creationflags": 0x08000000} if sys.platform.startswith("win")
               else {"start_new_session": True}))
        tail = []
        self._last_out = time.monotonic()
        watchdog = threading.Thread(target=self._watch_stall, daemon=True)
        watchdog.start()
        for line in self._proc.stdout:
            self._last_out = time.monotonic()
            tail.append(line)
            if len(tail) > 200:
                tail.pop(0)
            if line.startswith("PROGRESS "):
                parts = line.split()
                if len(parts) != 4:
                    continue
                _, phase, i, n = parts
                lo, hi = phases.get(phase, (0.0, 1.0))
                try:
                    frac = min(1.0, float(i) / max(1.0, float(n)))
                except ValueError:
                    continue
                self.progress.emit(100.0 * (lo + (hi - lo) * frac))
                self.stage.emit(TRAIL_LABELS.get(phase, ""))
        return self._proc.wait(), tail

    def run(self):
        output = None
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            TRAIL_CACHE.mkdir(parents=True, exist_ok=True)
            output = str(_reserve_output_path(EXPORTS_DIR / f"{self.out_name}.mp4"))
            key = self._cache_key()

            self.machine.emit("Mac · GPU Apple" if IS_MAC else "PC · Windows")
            if not TRAIL_SCRIPT.exists():
                self.error.emit(f"Traînée : module introuvable ({TRAIL_SCRIPT}). "
                                "Réinstalle l'app (le dossier du module est manquant).")
                return
            py = envsetup.trail_python()
            if not py.exists():
                # Première utilisation : environnement Python + torch, une seule fois.
                self.stage.emit("📦  Première traînée : installation du module (plusieurs Go, une seule fois)…")
                self.progress.emit(0)
                setup_log = []
                def _log(line: str):
                    setup_log.append(line)
                    print("[module traînée]", line, flush=True)
                    self.stage.emit("📦  Installation du module de traînée… " + line[:70])
                ok, msg = envsetup.install(_log)
                if not ok:
                    self.error.emit("Installation du module de traînée impossible : " + msg
                                    + "\n" + "\n".join(setup_log[-8:]))
                    return
                py = envsetup.trail_python()
            # `videotoolbox` : l'encodeur matériel du M4. L'encodage logiciel
            # (libx264) rajoutait des dizaines de secondes de CPU par minute de
            # 4K, pendant lesquelles la puce chauffait et ralentissait la passe
            # suivante — le matériel le fait à froid.
            cmd = [str(py), "-u", str(TRAIL_SCRIPT),
                   "--input", self.video_path, "--output", output,
                   "--encoder", "videotoolbox" if IS_MAC else "x264", "--style", "comet",
                   "--dets", str(TRAIL_CACHE / f"{key}_dets.json"),
                   "--cache", str(TRAIL_CACHE / f"{key}_traj.json")]
            self.stage.emit(TRAIL_LABELS["det"])
            rc, tail = self._pump(cmd, TRAIL_PHASES)

            if self._cancelled:
                Path(output).unlink(missing_ok=True)
                self.canceled.emit()
                return
            if rc != 0:
                Path(output).unlink(missing_ok=True)
                print("-- Trainee --\n", "".join(tail).encode("ascii", "replace").decode())
                self.error.emit("Traînée : échec.\n" + "".join(tail)[-1200:])
                return
            self.progress.emit(100.0)
            self.done.emit(output)
        except Exception as e:
            import traceback; traceback.print_exc()
            if output:
                Path(output).unlink(missing_ok=True)
            self.error.emit(str(e))
