"""Fusion de clips (montage multi-vidéos) : canevas, commande ffmpeg, MergeWorker."""

import subprocess, re, json
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import QThread, Signal

from pongedit.utils import EXPORTS_DIR, _reserve_output_path
from pongedit.export.encoding import _max_quality_video_args, _run_ffmpeg_with_progress
from pongedit.export.filters import _rotation_filter


# ── Fusion & rotation de clips ────────────────────────────────────────────────
#
# Coller plusieurs vidéos bout à bout revient à recoller une liste de segments
# (fichier + intervalle) sur un canevas de sortie commun, puisque les sources
# n'ont ni les mêmes dimensions ni forcément la même orientation. Le redressement
# d'un passage filmé de travers, lui, est un effet de l'éditeur (RotateAction) :
# il se pose sur la timeline comme une coupe.

CANVAS_CHOICES = [
    ("Auto (comme le 1er segment)", "auto"),
    ("Paysage 16:9", "16:9"),
    ("Portrait 9:16", "9:16"),
    ("Carré 1:1", "1:1"),
]

FIT_CHOICES = [
    ("Bandes noires (tout visible)", "contain"),
    ("Fond flou (tout visible)", "blur"),
    ("Remplir le cadre (recadre)", "cover"),
]

MERGE_AUDIO_ARGS = ["-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2"]


@lru_cache(maxsize=64)
def _probe_clip_info(path: str) -> dict:
    """Durée, dimensions AFFICHÉES, fps et présence d'audio d'un fichier.

    « Affichées » : une vidéo iPhone stocke souvent l'image couchée plus une
    rotation en métadonnée, que ffmpeg applique tout seul au décodage. On fait
    donc la même bascule ici, sinon le cadrage serait calculé à l'envers.
    """
    info = {"duration": 0.0, "width": 1920, "height": 1080,
            "fps": 30.0, "has_audio": False, "ten_bit": False, "ok": False}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True, text=True, timeout=25,
        )
        data = json.loads(r.stdout or "{}")
    except Exception:
        return info

    try:
        info["duration"] = float(data.get("format", {}).get("duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        pass

    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind == "audio":
            info["has_audio"] = True
        elif kind == "video" and not info["ok"]:
            w = int(st.get("width") or 0) or 1920
            h = int(st.get("height") or 0) or 1080
            rot = 0
            for sd in st.get("side_data_list", []) or []:
                if "rotation" in sd:
                    try:
                        rot = int(round(float(sd["rotation"])))
                    except (TypeError, ValueError):
                        rot = 0
            if rot == 0:
                try:
                    rot = int(float(st.get("tags", {}).get("rotate", 0)))
                except (TypeError, ValueError):
                    rot = 0
            if abs(rot) % 180 == 90:
                w, h = h, w
            info["width"], info["height"] = w, h
            info["ten_bit"] = "10" in str(st.get("pix_fmt", ""))
            rate = str(st.get("r_frame_rate", ""))
            if "/" in rate:
                num, den = rate.split("/")
                try:
                    if float(den) > 0:
                        info["fps"] = float(num) / float(den)
                except ValueError:
                    pass
            if info["duration"] <= 0:
                try:
                    info["duration"] = float(st.get("duration", 0.0) or 0.0)
                except (TypeError, ValueError):
                    pass
            info["ok"] = True
    return info


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def _segment_display_size(seg: dict) -> tuple[int, int]:
    """Taille du segment une fois SA rotation manuelle appliquée."""
    info = _probe_clip_info(seg["path"])
    w, h = info["width"], info["height"]
    if seg["rotation"] in (90, 270):
        w, h = h, w
    return w, h


def _merge_canvas(segments: list[dict], mode: str) -> tuple[int, int]:
    """Dimensions du canevas commun sur lequel tous les segments sont recollés."""
    sizes = [_segment_display_size(s) for s in segments]
    long_side = max(max(w, h) for w, h in sizes)
    if mode == "16:9":
        w, h = long_side, long_side * 9 / 16
    elif mode == "9:16":
        w, h = long_side * 9 / 16, long_side
    elif mode == "1:1":
        w = h = long_side
    else:
        w, h = sizes[0]
    return _even(w), _even(h)


def _fit_filters(fit: str, w: int, h: int, tag: str) -> tuple[str, list[str]]:
    """Chaîne(s) de cadrage vers un canevas w×h. Renvoie (suffixe, chaînes en plus).

    Le fond flou passe par une réduction avant le flou : flouter directement une
    image 4K coûte des secondes par image pour un rendu identique une fois réétiré.
    """
    if fit == "cover":
        return (f",scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}"), []
    if fit == "blur":
        extra = [
            f"[bg{tag}]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},scale=iw/8:-2,gblur=sigma=6,scale={w}:{h}[bgb{tag}]",
            f"[fg{tag}]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs{tag}]",
        ]
        return ",split=2[bg%s][fg%s]" % (tag, tag), extra
    return (f",scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"), []


def _build_merge_command(segments: list[dict], out_path: str, canvas_mode: str,
                         fit: str) -> tuple[list[str], float]:
    """Commande ffmpeg complète : un seul encodage pour toute la fusion."""
    w, h = _merge_canvas(segments, canvas_mode)
    fps = min(60.0, max(_probe_clip_info(s["path"])["fps"] for s in segments))
    ten_bit = _probe_clip_info(segments[0]["path"])["ten_bit"]
    pix = "yuv420p10le" if ten_bit else "yuv420p"

    cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-nostats"]
    chains: list[str] = []
    concat_in: list[str] = []
    idx = 0
    total = 0.0

    for n, seg in enumerate(segments):
        dur = max(0.05, seg["end"] - seg["start"])
        total += dur
        cmd += ["-ss", f"{seg['start']:.3f}", "-t", f"{dur:.3f}", "-i", seg["path"]]
        vi = idx
        idx += 1
        if _probe_clip_info(seg["path"])["has_audio"]:
            ai = f"{vi}:a"
        else:
            # Sans piste muette, concat refuserait de mélanger des clips qui ont
            # du son avec des clips qui n'en ont pas.
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            ai = f"{idx}:a"
            idx += 1

        rot = _rotation_filter(seg["rotation"])
        head = f"[{vi}:v]" + (rot + "," if rot else "") + f"fps={fps:.6f},format={pix}"
        suffix, extra = _fit_filters(fit, w, h, str(n))
        if fit == "blur":
            chains.append(head + suffix)
            chains.extend(extra)
            chains.append(f"[bgb{n}][fgs{n}]overlay=(W-w)/2:(H-h)/2,setsar=1[v{n}]")
        else:
            chains.append(head + suffix + f",setsar=1[v{n}]")
        chains.append(
            f"[{ai}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS[a{n}]"
        )
        concat_in.append(f"[v{n}][a{n}]")

    chains.append("".join(concat_in)
                  + f"concat=n={len(segments)}:v=1:a=1[vout][aout]")

    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", "[vout]", "-map", "[aout]",
        *_max_quality_video_args(segments[0]["path"], width=w, height=h, fps=fps),
        *MERGE_AUDIO_ARGS,
        out_path,
    ]
    return cmd, total


class MergeWorker(QThread):
    progress = Signal(float)          # 0 → 100
    stage    = Signal(str)
    done     = Signal(str)
    error    = Signal(str)
    canceled = Signal()

    def __init__(self, segments: list[dict], canvas_mode: str, fit: str, out_name: str):
        super().__init__()
        self.segments   = [dict(s) for s in segments]
        self.canvas_mode = canvas_mode
        self.fit        = fit
        self.out_name   = out_name
        self._cancelled = False
        self._proc      = None

    def cancel(self):
        self._cancelled = True
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def run(self):
        output = None
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_\-.]+", "_", self.out_name).strip("_") or "fusion"
            output = str(_reserve_output_path(EXPORTS_DIR / f"{safe}.mp4"))
            cmd, total = _build_merge_command(self.segments, output,
                                              self.canvas_mode, self.fit)
            self.stage.emit("🔗  Fusion et encodage…")
            rc, err = _run_ffmpeg_with_progress(
                cmd, lambda f: self.progress.emit(100.0 * f),
                total_seconds=total,
                on_start=lambda p: setattr(self, "_proc", p),
            )
            if self._cancelled:
                Path(output).unlink(missing_ok=True)
                self.canceled.emit()
                return
            if rc != 0:
                Path(output).unlink(missing_ok=True)
                print("── Fusion ──\n", err)
                self.error.emit("Fusion : échec ffmpeg.\n" + err[-1200:])
                return
            self.progress.emit(100.0)
            self.done.emit(output)
        except Exception as e:
            import traceback; traceback.print_exc()
            if output:
                Path(output).unlink(missing_ok=True)
            self.error.emit(str(e))
