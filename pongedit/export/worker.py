"""ExportWorker : export du montage."""

import os, subprocess, tempfile, shutil, threading, re, time
from pathlib import Path
from PySide6.QtCore import QThread, Signal

from pongedit.utils import EXPORTS_DIR, _get_fps, _get_video_dimensions, _reserve_output_path
from pongedit.match.scoring import _compute_stats_from_dicts
from pongedit.export.encoding import _max_quality_video_args, _run_ffmpeg_with_progress, _hwaccel_args
from pongedit.export.segments import _adjusted_time, _build_kept_segments, _kept_duration
from pongedit.export.cards import _is_hlg_source, _make_stats_card_png, _scorecard_margin
from pongedit.export.filters import _build_filter
from pongedit.export import cards as _cards
from pongedit.export.momentum import _render_momentum_anim
from pongedit.export import intro as _intro
from pongedit.export import score_anim as _score_anim


def _fmt_mmss(seconds: float) -> str:
    """Formate des secondes en m:ss (ou h:mm:ss si ≥ 1 h)."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class ExportWorker(QThread):
    progress = Signal(float)   # pourcentage précis (affiché en xx.x %)
    # émis au 1ᵉʳ tick d'encodage (pour basculer la barre en mode pourcentage)
    encode_started = Signal()
    # texte d'étape affiché pendant la préparation (avant l'encodage)
    stage    = Signal(str)
    done     = Signal(str)
    error    = Signal(str)
    canceled = Signal()

    def __init__(self, video_path, actions, p1n, p2n, overlay_dur, font_size, duration,
                 export_name="montage", first_server=1, p1_rank="", p2_rank="",
                 stats_card=True, stats_anim=True, intro_card=None, match_format=5):
        super().__init__()
        # Au meilleur des N sets : distingue balle de set et balle de match.
        self.match_format = match_format
        # Carte d'intro « duel » (valeurs, cf. export/intro) ou None : l'intro
        # ajoute une image figée au début, tout le montage est décalé d'autant.
        self.intro_card   = intro_card
        self.stats_card   = stats_card   # False : pas de tableau récap en fin de vidéo
        self.stats_anim   = stats_anim   # False : courbe de dynamique fixe
        self.video_path   = video_path
        self.actions      = actions
        self.p1n, self.p2n = p1n, p2n
        self.p1_rank, self.p2_rank = p1_rank or "", p2_rank or ""
        self.overlay_dur  = overlay_dur
        self.font_size    = font_size
        self.duration     = duration
        self.export_name  = export_name
        self.first_server = first_server
        self._cancelled   = False
        self._proc        = None  # process ffmpeg courant, pour l'annulation

    def cancel(self):
        """Interrompt l'export : coupe ffmpeg. Appelé depuis le thread UI."""
        self._cancelled = True
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    def run(self):
        tmp_dir = tempfile.mkdtemp(prefix="pong_export_")
        # Incrustations converties en HLG si la source l'est (cf. _ov_save).
        _cards._OV_HLG = _is_hlg_source(self.video_path)
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w\-]", "_", self.export_name) or "montage"
            # Nom LIBRE + réservé : deux exports en parallèle n'écrasent jamais le même
            # fichier (cause des échecs quand deux fenêtres exportaient "Eliot_vs_Yuri").
            output = str(_reserve_output_path(EXPORTS_DIR / f"{safe}.mp4"))
            prep_progress = 5
            self.progress.emit(1)

            has_cuts = any(a["type"] == "cut"    for a in self.actions)
            has_pts  = any(a["type"] == "point"  for a in self.actions)
            rotations = sorted(
                [a for a in self.actions if a["type"] == "rotate"],
                key=lambda x: x["start"],
            )
            cuts = sorted(
                [a for a in self.actions if a["type"] == "cut"],
                key=lambda x: x["start"],
            )
            kept_duration = _kept_duration(cuts, self.duration) if has_cuts else self.duration
            stats_show_start: float | None = None
            # Intro : `lead` s d'image figée (nombre entier d'images) avant le
            # montage. lead = 0 sans intro : tout le reste est alors inchangé.
            intro_on = bool(self.intro_card)
            lead_frames, lead = 0, 0.0
            if intro_on:
                vid_fps = _get_fps(self.video_path)
                lead_frames, lead = _intro.intro_lead(vid_fps)
            if not has_cuts and not has_pts and not rotations and not intro_on:
                r = subprocess.run(
                    ["ffmpeg", "-y", "-i", self.video_path, "-c", "copy", output],
                    capture_output=True, text=True,
                )
                if r.returncode != 0:
                    self.error.emit(f"FFmpeg failed:\n{r.stderr[-2000:]}")
                    return
                self.progress.emit(100)
                self.done.emit(output)
                return

            points_seq = None
            if has_pts and self.stats_card:
                stats = _compute_stats_from_dicts(self.actions, self.first_server)
                if stats:
                    points = [a for a in self.actions if a["type"] == "point"]
                    # Vainqueur de chaque point, dans l'ordre du match (courbe de dynamique).
                    points_seq = [p["player"] for p in sorted(points, key=lambda p: p["timecode"])]
                    # Un point enregistre par erreur alors que le lecteur etait
                    # revenu a 0 donnait last_pt_adj = 0, donc show_start = 2.5 s
                    # et la carte de stats affichee par dessus TOUT le match
                    # (bug du 18/09/2026). La liste arrive desormais triee par
                    # timecode, mais on garde le maximum comme ceinture.
                    last_pt_adj = max(
                        (_adjusted_time(p["timecode"], cuts, self.duration) for p in points),
                        default=0.0,
                    )
                    stats_show_start = min(last_pt_adj + 2.5, max(0.0, kept_duration - 1.0)) + lead
                else:
                    stats = {}
            else:
                stats = {}

            self.stage.emit("🎬  Préparation du montage…")
            prep_t0 = time.monotonic()

            # Toutes les incrustations (vignette de score, carte de fin) sont cotées
            # en fraction du plus petit côté de la vidéo (cf. _overlay_unit) : même
            # rendu en 1080p et en 4K.
            W_full, H_full = _get_video_dimensions(self.video_path)
            W_full = W_full or 1920
            H_full = H_full or 1080
            font_size_scaled = self.font_size          # conservé pour la signature
            margin_scaled = _scorecard_margin(W_full, H_full)

            def _on_prep(done: int, total: int):
                if self._cancelled:
                    return
                elapsed = time.monotonic() - prep_t0
                eta = (elapsed / done * (total - done)) if done else 0.0
                suffix = f"  ·  reste ~{eta:.0f}s" if eta >= 1 else ""
                self.stage.emit(f"🎨  Incrustation des scores…  {done}/{total}{suffix}")

            # La carte de stats (très grosse en 4K : jusqu'à 82 % de la résolution)
            # ne dépend que de données déjà connues ici. On la génère EN PARALLÈLE des
            # vignettes de score au lieu d'attendre la fin de _build_filter (~250 ms de
            # gain en 4K). Le thread est rejoint plus bas, avant le lancement de ffmpeg.
            stats_png = os.path.join(tmp_dir, "stats_card.tga")
            stats_thread = None
            card_fade_in = 0.8
            # Courbe de dynamique animée (cf. momentum) : le tableau est écrit
            # avec un trou à sa place, et ce rectangle est rendu en séquence
            # d'images dans le même thread.
            # `anim_ctx` reste None si les animations sont coupées.
            anim_ctx = None
            if stats and self.stats_anim:
                anim_ctx = {}
                anim_fps = _get_fps(self.video_path)

            def _stats_job():
                if anim_ctx is None:
                    _make_stats_card_png(stats, self.p1n, self.p2n, stats_png, W_full, H_full,
                                         self.p1_rank, self.p2_rank,
                                         points_seq=points_seq)
                    return
                try:
                    _make_stats_card_png(stats, self.p1n, self.p2n, stats_png, W_full, H_full,
                                         self.p1_rank, self.p2_rank,
                                         anim=anim_ctx, points_seq=points_seq)
                    mom = anim_ctx.get("momentum")
                    if mom:
                        mom["pattern"], mom["frames"] = _render_momentum_anim(
                            mom, tmp_dir, anim_fps, lead=card_fade_in + 0.2)
                except Exception:
                    # Repli : tableau fixe (sans trou).
                    import traceback; traceback.print_exc()
                    anim_ctx.clear()
                    _make_stats_card_png(stats, self.p1n, self.p2n, stats_png, W_full, H_full,
                                         self.p1_rank, self.p2_rank,
                                         points_seq=points_seq)

            if stats:
                stats_thread = threading.Thread(target=_stats_job, daemon=True)
                stats_thread.start()

            # Carte d'intro : séquence d'images rendue en parallèle des vignettes.
            intro_res: dict = {}
            intro_thread = None
            if intro_on:
                def _intro_job():
                    t0 = time.monotonic()
                    try:
                        intro_res.update(_intro.render_intro_sequence(
                            self.intro_card, W_full, H_full, tmp_dir, vid_fps,
                            t_gone=lead + _intro.EXIT_DUR))
                    except Exception:
                        import traceback; traceback.print_exc()
                        intro_res.clear()
                    intro_res["secs"] = time.monotonic() - t0
                intro_thread = threading.Thread(target=_intro_job, daemon=True)
                intro_thread.start()

            _single_input = (not has_cuts) and len(_build_kept_segments(cuts, self.duration)) == 1
            fc, _ = _build_filter(
                self.actions,
                self.duration,
                self.overlay_dur,
                font_size_scaled,
                self.p1n,
                self.p2n,
                tmp_dir=tmp_dir if has_pts else None,
                first_server=self.first_server,
                score_hide_at=stats_show_start,
                on_prep=_on_prep,
                margin=margin_scaled,
                rotations=rotations,
                width=W_full,
                height=H_full,
                src_v="[0:v]" if _single_input else "[vcat]",
                src_a="[0:a]" if _single_input else "[acat]",
                p1_rank=self.p1_rank,
                p2_rank=self.p2_rank,
                lead_frames=lead_frames,
                lead=lead,
                fps=vid_fps if intro_on else 30.0,
                # Le score n'apparaît qu'une fois la vidéo revenue à 100 % (re-zoom
                # plus long que le retrait des encarts).
                score_show_from=(lead + max(_intro.EXIT_DUR, _intro.REZOOM_DUR)) if intro_on else None,
                # Chiffres du score animés (cf. score_anim) : à la cadence de la
                # vidéo. 0 = vignettes fixes, graphe inchangé.
                anim_fps=((vid_fps if intro_on else _get_fps(self.video_path))
                          if (has_pts and _score_anim.SCORE_ANIM_STYLE) else 0.0),
                match_format=self.match_format,
            )

            # UN INPUT PAR SEGMENT (`-ss/-to` avant `-i`) puis filtre `concat`.
            #
            # Pourquoi pas le demuxer concat (`inpoint/outpoint`) : sur les sources
            # iPhone 4K60 HEVC (image-clé toutes les ~0,93 s), il ne redécode PAS
            # depuis l'image-clé précédente : les images entre `inpoint` et la
            # prochaine image-clé sont perdues (« Could not find ref with POC »),
            # d'où une image FIGÉE pendant ~1 s à chaque coupe. Reproduit et mesuré :
            # 191 images au lieu de 360 sur deux segments de 3 s. Avec `-ss` par
            # input, ffmpeg seek à l'image-clé, décode, et livre exactement les
            # images demandées (360/360, intervalles 1/60 exacts).
            # Le coût est un reparse de l'index par input (quelques secondes sur une
            # source de plusieurs Go) — accepté : la correction prime.
            kept_segments = _build_kept_segments(cuts, self.duration)
            n_segs = len(kept_segments)   # la carte de stats devient l'input n°n_segs
            seg_inputs: list[str] = []
            if n_segs == 1 and not has_cuts:
                seg_inputs = [*_hwaccel_args(), "-i", self.video_path]
            else:
                for _ss, _se in kept_segments:
                    seg_inputs += [*_hwaccel_args(),
                                   "-ss", f"{_ss:.4f}", "-to", f"{_se:.4f}",
                                   "-i", self.video_path]
                _cat = "".join(
                    f"[{i}:v]setpts=PTS-STARTPTS[sv{i}];[{i}:a]asetpts=PTS-STARTPTS[sa{i}];"
                    for i in range(n_segs)
                )
                _cat += "".join(f"[sv{i}][sa{i}]" for i in range(n_segs))
                _cat += f"concat=n={n_segs}:v=1:a=1[vcat][acat];"
                fc = _cat + fc

            extra_inputs: list[str] = []
            if stats:
                # La carte a été lancée en parallèle plus haut : on attend juste sa fin.
                self.stage.emit("🖼️  Finalisation de la carte de stats…")
                if stats_thread is not None:
                    stats_thread.join()

                card_dur = 8.0
                fade_in = card_fade_in
                show_start = (stats_show_start if stats_show_start is not None
                              else max(0.0, kept_duration - 1.0) + lead)
                stats_loop_dur = card_dur + fade_in + 1.0

                fc = fc.replace("[vout]", "[vmain]")
                fc += (
                    # La carte de stats vient APRÈS les n_segs inputs vidéo.
                    f";[{n_segs}:v]format=rgba,"
                    f"fade=t=in:st=0:d={fade_in}:alpha=1,"
                    f"setpts=PTS+{show_start:.3f}/TB[vcard];"
                    f"[vmain][vcard]overlay=x=(W-w)/2:y=(H-h)/2"
                    f":enable='gte(t,{show_start:.3f})'[vout]"
                )
                # Même cadence que la vidéo : sinon l'image bouclée sort à 25 fps et
                # la fin du fichier devient à cadence variable (20-30 fps).
                _card_fps = _get_fps(self.video_path)
                extra_inputs = ["-framerate", f"{_card_fps:.3f}", "-loop", "1",
                                "-t", f"{stats_loop_dur:.3f}", "-i", stats_png]
                # Rectangle animé (courbe de dynamique), posé
                # dans le trou du tableau : même fondu, même décalage, même
                # cadence ; x/y relatifs au tableau (décalages pairs, cf.
                # _make_stats_card_png). Après sa dernière image (= rendu fixe),
                # overlay la répète jusqu'au bout.
                layers = []
                if anim_ctx and (anim_ctx.get("momentum") or {}).get("pattern"):
                    layers.append(anim_ctx["momentum"])
                for li, lay in enumerate(layers):
                    cw_, ch_ = lay["card_size"]
                    rx, ry = lay["rect"][0], lay["rect"][1]
                    fc = fc[: -len("[vout]")] + f"[vcard{li}]"
                    fc += (
                        f";[{n_segs + 1 + li}:v]format=rgba,"
                        f"fade=t=in:st=0:d={fade_in}:alpha=1,"
                        f"setpts=PTS+{show_start:.3f}/TB[vanim{li}];"
                        f"[vcard{li}][vanim{li}]overlay=x=(W-{cw_})/2+{rx}:y=(H-{ch_})/2+{ry}"
                        f":enable='gte(t,{show_start:.3f})':eof_action=repeat[vout]"
                    )
                    extra_inputs += ["-framerate", f"{_card_fps:.3f}", "-start_number", "0",
                                     "-i", lay["pattern"]]

            if intro_thread is not None:
                # Encarts de l'intro, par-dessus tout le reste (zone pleine
                # largeur, position paire) ; transparents après le retrait.
                self.stage.emit("🎬  Finalisation de la carte d'intro…")
                intro_thread.join()
                print(f"Intro : {intro_res.get('frames', 0)} images "
                      f"({intro_res.get('unique', 0)} uniques) en {intro_res.get('secs', 0):.2f} s")
                if intro_res.get("pattern"):
                    n_in = n_segs + extra_inputs.count("-i")
                    fc = fc.replace("[vout]", "[vpreintro]", 1)
                    fc += (f";[{n_in}:v]format=rgba[vintro];"
                           f"[vpreintro][vintro]overlay=x={intro_res['x']}:y={intro_res['y']}"
                           f":eof_action=pass[vout]")
                    # Cadence RATIONNELLE exacte (60000/1001…) : chaque image de
                    # l'intro tombe pile sur l'image vidéo de même rang (avec
                    # « 59.940 », la dernière image arrivait une image trop tard).
                    from fractions import Fraction
                    _ifps = Fraction(vid_fps).limit_denominator(1001)
                    extra_inputs += ["-framerate", f"{_ifps}", "-start_number", "0",
                                     "-i", intro_res["pattern"]]

            self.stage.emit("⚙️  Initialisation du rendu vidéo…")
            self.progress.emit(prep_progress)

            cmd = [
                "ffmpeg", "-y",
                "-threads", "0",
                "-progress", "pipe:1", "-nostats",
                *seg_inputs,
                *extra_inputs,
                "-filter_complex", fc,
                "-map", "[vout]",
                "-map", "[aout]",
                *_max_quality_video_args(self.video_path),
                "-c:a", "aac",
                "-b:a", "320k",
                output,
            ]

            progress_start = prep_progress
            progress_span = 94
            self.progress.emit(progress_start)

            if self._cancelled:  # annulé pendant la préparation
                self.canceled.emit()
                return

            def _cb(ratio: float):
                # On ne montre que le pourcentage. `encode_started` signale à l'UI de
                # basculer la barre en mode déterminé au 1ᵉʳ tick d'encodage.
                self.progress.emit(min(99.9, progress_start + ratio * progress_span))
                self.encode_started.emit()

            rc, stderr_text = _run_ffmpeg_with_progress(
                cmd,
                _cb,
                total_seconds=kept_duration + lead,
                on_start=lambda p: setattr(self, "_proc", p),
            )

            if self._cancelled:
                Path(output).unlink(missing_ok=True)  # retire le fichier partiel
                self.canceled.emit()
                return

            if rc != 0:
                # Retire le fichier réservé s'il est resté vide/partiel (échec).
                try:
                    if os.path.getsize(output) == 0:
                        Path(output).unlink(missing_ok=True)
                except OSError:
                    pass
                stderr_text = "".join(stderr_text.splitlines(True)[-200:])
                print("── FFmpeg stderr ──\n", stderr_text)
                self.error.emit(f"FFmpeg failed:\n{stderr_text[-2000:]}")
                return

            self.progress.emit(100)
            self.done.emit(output)
        except Exception as e:
            import traceback; traceback.print_exc()
            self.error.emit(str(e))
        finally:
            _cards._OV_HLG = False
            shutil.rmtree(tmp_dir, ignore_errors=True)
