"""Encodage ffmpeg : VideoToolbox, débit, progression."""

import os, subprocess, threading

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


def _run_ffmpeg_with_progress(cmd: list[str], progress_cb, total_seconds: float | None = None,
                              on_start=None) -> tuple[int, str]:
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

    if _check_videotoolbox():
        return [
            "-c:v", "hevc_videotoolbox",
            *color_tags,
            # Mesuré sur 4K60 10 bits : encodeur ~30 % plus rapide pour un SSIM
            # identique (0.99123 vs 0.99126) — le débit fixe la qualité, pas ce flag.
            "-prio_speed", "1",
            "-b:v", str(target),
            "-maxrate", str(int(target * 1.5)),
            "-bufsize", str(int(target * 3)),
            "-profile:v", "main10" if ten_bit else "main",
            "-pix_fmt", "yuv420p10le" if ten_bit else "yuv420p",
            "-tag:v", "hvc1",        # sans ça QuickTime/Finder refusent de lire le fichier
            "-movflags", "+faststart",
        ]
    return [
        "-c:v", "libx265",
        *color_tags,
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p10le" if ten_bit else "yuv420p",
        "-tag:v", "hvc1",
        "-movflags", "+faststart",
    ]
