"""Encodage ffmpeg : VideoToolbox, débit, progression."""

import os, re, subprocess, sys, tempfile, threading

from pongedit.utils import _probe_video_props


_VIDEOTOOLBOX_CACHE: bool | None = None


def _check_videotoolbox() -> bool:
    global _VIDEOTOOLBOX_CACHE
    if _VIDEOTOOLBOX_CACHE is None:
        try:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=5,
            )
            _VIDEOTOOLBOX_CACHE = "h264_videotoolbox" in r.stdout
        except Exception:
            _VIDEOTOOLBOX_CACHE = False
    return _VIDEOTOOLBOX_CACHE


# ── Choix de l'encodeur matériel (Mac, NVIDIA, Intel, AMD, sinon processeur) ──
# On ne se fie pas à la liste `ffmpeg -encoders` : un encodeur peut y figurer sans que la
# carte correspondante existe. On tente donc un VRAI mini-encodage de 0,1 s, dans l'ordre
# de préférence de chaque système, et on garde le premier qui marche.

_HW_CACHE: dict = {}


def _hw_candidates() -> list[str]:
    if sys.platform == "darwin":
        return ["hevc_videotoolbox"]
    return ["hevc_nvenc", "hevc_qsv", "hevc_amf"]


def _probe_encoder(name: str, ten_bit: bool) -> bool:
    pix = "p010le" if ten_bit else "nv12"
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi",
           "-i", "color=c=black:s=256x144:r=30:d=0.1", "-vf", f"format={pix}",
           "-c:v", name, "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=20,
                           creationflags=0x08000000 if sys.platform.startswith("win") else 0)
        return r.returncode == 0
    except Exception:
        return False


def _hw_encoder(ten_bit: bool) -> str | None:
    """Nom de l'encodeur matériel HEVC utilisable ici (ou None → logiciel)."""
    key = bool(ten_bit)
    if key not in _HW_CACHE:
        _HW_CACHE[key] = next((n for n in _hw_candidates() if _probe_encoder(n, ten_bit)), None)
        print(f"Encodeur vidéo ({'10' if ten_bit else '8'} bits) : {_HW_CACHE[key] or 'logiciel (libx265)'}")
    return _HW_CACHE[key]


def _hwaccel_args() -> list[str]:
    """Décodage matériel de la source : puce Apple sur Mac, sinon le meilleur disponible
    (D3D11 sur Windows, etc.). `auto` retombe tout seul sur le logiciel si besoin."""
    return ["-hwaccel", "videotoolbox" if sys.platform == "darwin" else "auto"]


# ── Commande trop longue (Windows : WinError 206) ─────────────────────────────
# Windows refuse de lancer un programme dont la ligne de commande dépasse 32 767 caractères.
# Un montage avec beaucoup de points et de coupes produit un graphe de filtres bien plus
# long : on le dépose dans un fichier temporaire que ffmpeg lit lui-même.

CMD_LIMIT = 24000        # marge sous les 32 767 de CreateProcess
_FFMPEG_MAJOR: int | None = None


def _ffmpeg_major() -> int:
    """Version majeure de ffmpeg (builds « git / master » = très récents → 99)."""
    global _FFMPEG_MAJOR
    if _FFMPEG_MAJOR is None:
        try:
            out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True,
                                 timeout=8,
                                 creationflags=0x08000000 if sys.platform.startswith("win") else 0).stdout
            m = re.search(r"version n?(\d+)\.", out.splitlines()[0])
            _FFMPEG_MAJOR = int(m.group(1)) if m else 99
        except Exception:
            _FFMPEG_MAJOR = 99
    return _FFMPEG_MAJOR


def _fit_command_line(cmd: list[str]) -> tuple[list[str], list[str]]:
    """Retourne (commande utilisable, fichiers temporaires à supprimer ensuite).

    Sans effet tant que la ligne reste courte (toujours le cas sur Mac/Linux, où la limite
    est de plusieurs Mo). Sinon, chaque `-filter_complex <graphe>` devient un fichier :
    `-/filter_complex` (ffmpeg ≥ 7) ou `-filter_complex_script` (plus ancien).
    """
    if len(subprocess.list2cmdline(cmd)) <= CMD_LIMIT:
        return cmd, []
    flag = "-/filter_complex" if _ffmpeg_major() >= 7 else "-filter_complex_script"
    out: list[str] = []
    tmp: list[str] = []
    i = 0
    while i < len(cmd):
        if cmd[i] == "-filter_complex" and i + 1 < len(cmd):
            f = tempfile.NamedTemporaryFile("w", suffix=".ffgraph", delete=False, encoding="utf-8")
            f.write(cmd[i + 1])
            f.close()
            out += [flag, f.name]
            tmp.append(f.name)
            i += 2
        else:
            out.append(cmd[i])
            i += 1
    n = len(subprocess.list2cmdline(out))
    print(f"Commande ffmpeg raccourcie : {len(subprocess.list2cmdline(cmd))} -> {n} caracteres")
    return out, tmp


def _run_ffmpeg_with_progress(cmd: list[str], progress_cb, total_seconds: float | None = None,
                              on_start=None) -> tuple[int, str]:
    cmd, _tmp_files = _fit_command_line(cmd)
    try:
        return _run_ffmpeg_with_progress_inner(cmd, progress_cb, total_seconds, on_start)
    finally:
        for _f in _tmp_files:
            try:
                os.unlink(_f)
            except OSError:
                pass


def _run_ffmpeg_with_progress_inner(cmd, progress_cb, total_seconds, on_start):
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Permet à l'appelant (ExportWorker) de garder une référence pour pouvoir annuler.
    if on_start is not None:
        on_start(proc)

    stderr_lines: list[str] = []

    def _drain_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()

    for raw_line in proc.stdout:
        line = raw_line.strip()
        if line.startswith("out_time_us=") and total_seconds and total_seconds > 0:
            try:
                secs = int(line.split("=", 1)[1]) / 1_000_000
                progress_cb(min(secs / total_seconds, 1.0))
            except ValueError:
                pass
        elif line.startswith("progress=") and line.endswith("end"):
            progress_cb(1.0)

    proc.wait()
    t.join(timeout=3)
    return proc.returncode, "".join(stderr_lines)


def _stream_copy_kept_segments(video_path: str, kept: list[tuple[float, float]], output_path: str, tmp_dir: str, progress_cb) -> tuple[int, str]:
    base_cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-nostats"]

    if len(kept) == 1:
        start, end = kept[0]
        if start > 0.01:
            cmd = base_cmd + ["-ss", f"{start:.4f}", "-i", video_path, "-c", "copy", output_path]
        else:
            cmd = base_cmd + ["-i", video_path, "-t", f"{end:.4f}", "-c", "copy", output_path]
    else:
        concat_list = os.path.join(tmp_dir, "concat.txt")
        with open(concat_list, "w") as f:
            for start, end in kept:
                f.write(f"file '{video_path}'\n")
                f.write(f"inpoint {start:.4f}\n")
                f.write(f"outpoint {end:.4f}\n")
        cmd = base_cmd + [
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c", "copy", output_path,
        ]

    total_seconds = sum(end - start for start, end in kept)
    return _run_ffmpeg_with_progress(cmd, progress_cb, total_seconds=total_seconds)


# Débit cible, en bits par pixel et par image. Les bornes encadrent le débit repris
# de la source : la borne basse évite qu'une source déjà très compressée produise une
# sortie dégradée, la borne haute évite un fichier absurde sur une source quasi-brute.
BITRATE_BPP_MIN = 0.040
BITRATE_BPP_MAX = 0.300
BITRATE_BPP_FALLBACK = 0.100

# Marge au-dessus du débit source. Un ré-encodage perd toujours un peu ; +25 % compense
# cette perte de génération. Mesuré sur 8 s de 4K60 HEVC 10 bits (SSIM vs source
# lossless) : sans marge 0.9905, avec marge 0.9927 — soit exactement le SSIM de
# l'ancien réglage h264 -q:v 80, mais pour un fichier 2.9× plus léger.
BITRATE_HEADROOM = 1.25


def _max_quality_video_args(video_path: str | None = None, *,
                            width: int | None = None, height: int | None = None,
                            fps: float | None = None) -> list[str]:
    """Args vidéo de l'export, calés dynamiquement sur la source.

    Le débit de sortie reprend celui de la source (borné par le débit par pixel),
    donc la qualité suit automatiquement la résolution et les fps de l'entrée au
    lieu d'être figée. La sortie est en HEVC — même famille que les sources iPhone,
    ~2× plus efficace que H.264 à qualité égale — et la profondeur 10 bits de la
    source est conservée au lieu d'être écrasée en 8 bits.
    """
    props = _probe_video_props(video_path) if video_path else {}
    # width/height/fps forcés : la fusion encode sur un canevas qui n'est pas
    # celui de la source, le débit doit suivre la sortie et non l'entrée.
    px_rate = ((width or props.get("width", 1920))
               * (height or props.get("height", 1080))
               * max(1.0, fps or props.get("fps", 30.0)))
    src_br = props.get("bitrate", 0)
    base = src_br * BITRATE_HEADROOM if src_br > 0 else px_rate * BITRATE_BPP_FALLBACK
    target = int(max(px_rate * BITRATE_BPP_MIN,
                     min(base, px_rate * BITRATE_BPP_MAX)))
    ten_bit = "10" in props.get("pix_fmt", "")
    # Recopie des métadonnées couleur : sans elles, un fichier HLG sort « unknown »
    # et les lecteurs l'affichent comme du SDR délavé ou sursaturé.
    color_tags: list[str] = []
    if props.get("color_primaries"):
        color_tags += ["-color_primaries", props["color_primaries"]]
    if props.get("color_transfer"):
        color_tags += ["-color_trc", props["color_transfer"]]
    if props.get("color_space"):
        color_tags += ["-colorspace", props["color_space"]]

    enc = _hw_encoder(ten_bit)
    maxrate, bufsize = str(int(target * 1.5)), str(int(target * 3))
    profile = "main10" if ten_bit else "main"
    pixfmt = "yuv420p10le" if ten_bit else "yuv420p"
    common_tail = ["-tag:v", "hvc1", "-movflags", "+faststart"]   # hvc1 : QuickTime/Finder/Windows lisent

    if enc == "hevc_videotoolbox":
        return [
            "-c:v", enc, *color_tags,
            # Mesuré sur 4K60 10 bits : encodeur ~30 % plus rapide pour un SSIM
            # identique (0.99123 vs 0.99126) — le débit fixe la qualité, pas ce flag.
            "-prio_speed", "1",
            "-b:v", str(target), "-maxrate", maxrate, "-bufsize", bufsize,
            "-profile:v", profile, "-pix_fmt", pixfmt, *common_tail,
        ]
    if enc == "hevc_nvenc":      # cartes NVIDIA
        return [
            "-c:v", enc, *color_tags,
            "-preset", "p5", "-tune", "hq", "-rc", "vbr",
            "-b:v", str(target), "-maxrate", maxrate, "-bufsize", bufsize,
            "-profile:v", profile, "-pix_fmt", "p010le" if ten_bit else "yuv420p", *common_tail,
        ]
    if enc == "hevc_qsv":        # Intel (Quick Sync)
        return [
            "-c:v", enc, *color_tags,
            "-preset", "medium",
            "-b:v", str(target), "-maxrate", maxrate, "-bufsize", bufsize,
            "-profile:v", profile, "-pix_fmt", "p010le" if ten_bit else "nv12", *common_tail,
        ]
    if enc == "hevc_amf":        # cartes AMD
        return [
            "-c:v", enc, *color_tags,
            "-quality", "balanced", "-rc", "vbr_peak",
            "-b:v", str(target), "-maxrate", maxrate, "-bufsize", bufsize,
            "-profile:v", profile, "-pix_fmt", "p010le" if ten_bit else "nv12", *common_tail,
        ]
    # Aucun encodeur matériel : processeur seul. `veryfast` + débit imposé reste
    # nettement plus rapide que l'ancien `medium` crf 18, pour une qualité très proche.
    return [
        "-c:v", "libx265", *color_tags,
        "-preset", "veryfast", "-crf", "20",
        "-x265-params", "log-level=error",
        "-pix_fmt", pixfmt, *common_tail,
    ]
