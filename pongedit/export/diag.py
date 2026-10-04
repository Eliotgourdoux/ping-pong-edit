"""Journal de performance de l'export : quel matériel est utilisé, à quelle vitesse.

Tout est écrit dans la console ET dans un fichier (`<données>/logs/export.log`, relu par
« Aide → Journal d'export » si on veut le transmettre). Ne lève jamais : un diagnostic ne
doit pas pouvoir casser un export.
"""

import ctypes, os, platform, re, shutil, subprocess, sys, threading, time
from pathlib import Path

NOWIN = 0x08000000 if sys.platform.startswith("win") else 0
LIVE: dict = {}          # dernières stats ffmpeg (fps, speed, out_time) alimentées par la boucle de progression
_t0 = time.time()


def reset() -> None:
    """Début d'un export : remet le chronomètre du journal à zéro."""
    global _t0
    _t0 = time.time()


def log_path() -> Path:
    from pongedit.updater import data_dir
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p / "export.log"


def log(msg: str) -> None:
    line = f"[perf {time.time() - _t0:6.1f}s] {msg}"
    try:
        print(line.encode("ascii", "replace").decode() if not sys.stdout.encoding else line, flush=True)
    except Exception:
        pass
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _run(cmd, timeout=10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=NOWIN, errors="replace").stdout or ""
    except Exception:
        return ""


def _gpus() -> list[str]:
    if sys.platform.startswith("win"):
        out = _run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' | pilote ' + $_.DriverVersion }"], 15)
        return [l.strip() for l in out.splitlines() if l.strip()]
    if sys.platform == "darwin":
        out = _run(["system_profiler", "SPDisplaysDataType"], 15)
        return [l.split(":", 1)[1].strip() for l in out.splitlines() if "Chipset Model" in l]
    return [l.strip() for l in _run(["lspci"]).splitlines() if "VGA" in l or "3D" in l]


def _cpu_name() -> str:
    if sys.platform.startswith("win"):
        out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"], 15)
        return out.strip() or platform.processor()
    if sys.platform == "darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.processor()
    return platform.processor()


def start_session(video_path: str, kept_duration: float) -> None:
    """Instantané du matériel et du logiciel au début d'un export."""
    try:
        log(f"Preparation du montage (intro, scores, stats) terminee apres {time.time() - _t0:.0f} s")
        from pongedit.version import VERSION
        log("=" * 60)
        log(f"Export - Ping Pong Edit {VERSION} - {time.strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"Systeme : {platform.platform()} - Python {platform.python_version()}")
        log(f"Processeur : {_cpu_name()} ({os.cpu_count()} threads)")
        for g in _gpus():
            log(f"Carte graphique : {g}")
        smi = shutil.which("nvidia-smi")
        if smi:
            log("nvidia-smi : " + _run([smi, "--query-gpu=name,driver_version,utilization.gpu,clocks.max.sm",
                                        "--format=csv,noheader"]).strip())
        else:
            log("nvidia-smi : absent (pas de carte NVIDIA detectee par le pilote)")
        ff = shutil.which("ffmpeg") or "ffmpeg"
        log(f"ffmpeg : {ff} - {(_run(['ffmpeg', '-version']).splitlines() or ['?'])[0]}")
        hw = [l.strip() for l in _run(["ffmpeg", "-hide_banner", "-hwaccels"]).splitlines()[1:] if l.strip()]
        log("Decodage materiel dispo dans ffmpeg : " + (", ".join(hw) or "aucun"))
        enc = _run(["ffmpeg", "-hide_banner", "-encoders"])
        found = [n for n in ("hevc_nvenc", "hevc_qsv", "hevc_amf", "hevc_videotoolbox", "libx265") if n in enc]
        log("Encodeurs HEVC dans ffmpeg : " + ", ".join(found))
        log(f"Video source : {Path(video_path).name} - duree montee {kept_duration:.0f} s")
    except Exception as e:
        log(f"(diagnostic partiel : {e})")


def log_probe(name: str, ok: bool, ten_bit: bool) -> None:
    log(f"Test encodeur {name} ({'10' if ten_bit else '8'} bits) : {'OK' if ok else 'INDISPONIBLE'}")


# ── échantillonneur pendant l'encodage ────────────────────────────────────────

class _CpuMeter:
    def __init__(self):
        self.prev = self._read()

    @staticmethod
    def _read():
        if sys.platform.startswith("win"):
            class FT(ctypes.Structure):
                _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
            i, k, u = FT(), FT(), FT()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
            v = lambda t: (t.hi << 32) | t.lo
            return v(i), v(k) + v(u)          # idle, total (le noyau inclut l'idle)
        return None

    def percent(self) -> str:
        try:
            cur = self._read()
            if cur is None:
                return f"charge {os.getloadavg()[0]:.1f}"
            di, dt = cur[0] - self.prev[0], cur[1] - self.prev[1]
            self.prev = cur
            return f"{100 * (1 - di / dt):.0f} %" if dt else "?"
        except Exception:
            return "?"


def _gpu_usage() -> str:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return ""
    out = _run([smi, "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder,clocks.sm",
                "--format=csv,noheader,nounits"], 4).strip()
    p = [x.strip() for x in out.split(",")]
    return f" | GPU {p[0]} % (encodage {p[1]} %, decodage {p[2]} %, {p[3]} MHz)" if len(p) == 4 else ""


class Sampler:
    """Toutes les 5 s : charge processeur, charge carte NVIDIA, fps et vitesse ffmpeg."""

    def __init__(self, label: str):
        self.label, self._stop = label, threading.Event()
        self._th = threading.Thread(target=self._loop, daemon=True)
        self.t_start = time.time()

    def __enter__(self):
        LIVE.clear()
        log(f"Encodage ({self.label}) demarre")
        self._th.start()
        return self

    def _loop(self):
        cpu = _CpuMeter()
        while not self._stop.wait(5):
            log(f"CPU {cpu.percent()}{_gpu_usage()} | ffmpeg {LIVE.get('fps', '?')} img/s, "
                f"vitesse {LIVE.get('speed', '?')}, position {LIVE.get('out_time', '?')}")

    def __exit__(self, *exc):
        self._stop.set()
        dt = time.time() - self.t_start
        log(f"Encodage ({self.label}) fini en {dt:.0f} s - derniere vitesse {LIVE.get('speed', '?')}")
        return False


def log_decoder_summary(stderr_text: str) -> None:
    """Ce que ffmpeg dit avoir réellement utilisé (décodeur matériel ou non)."""
    try:
        keep = [l.strip() for l in stderr_text.splitlines()
                if re.search(r"hwaccel|d3d11|dxva|cuda|nvenc|qsv|amf|videotoolbox|Stream #\d+:\d+.*->|Video: hevc|Video: h264|encoder\s*:", l, re.I)]
        for l in keep[:12]:
            log("ffmpeg> " + l[:200])
        if not any(re.search(r"hwaccel|d3d11|dxva|cuda|qsv|videotoolbox", l, re.I) for l in keep):
            log("Note : ffmpeg ne mentionne pas de decodage materiel (a confirmer avec le decodage % ci-dessus).")
    except Exception:
        pass
