"""Construction des filter_complex ffmpeg (score, rotations)."""

import os, threading

from pongedit.export.segments import _adjusted_time, _build_kept_segments
from pongedit.export.cards import (SETPOINT_GAP, _make_scorecard_png, _make_setpoint_png,
                                   _overlay_unit, _set_point_state)
from pongedit.export import cards as _cards
from pongedit.match.scoring import flips_before, server_at


def _set_form(points, pi: int, set_idx: int) -> list[int]:
    """Vainqueurs (1/2) des 8 derniers points du match jusqu'au point `pi` inclus,
    sets confondus (la forme ne repart pas à zéro à chaque set). `set_idx` est
    gardé pour la signature des appelants."""
    return [p["player"] for p in points[max(0, pi - 7):pi + 1]]


def _build_overlay_filter(points, duration, font_size, p1n, p2n, tmp_dir=None, first_server=1, margin: int = 28):
    if tmp_dir is None:
        raise ValueError("tmp_dir is required for overlay export")

    if not points:
        return "[0:v]copy[vout]", []

    lines = []

    first_adj = max(0.0, points[0]["timecode"])
    has_pre   = first_adj > 0.01
    pre_png   = os.path.join(tmp_dir, "score_pre.tga") if has_pre else None

    png_specs  = []
    pt_filters = []

    def _server_at(set_index, total_in_set, cp1, cp2, next_t=None):
        return server_at(first_server, set_index, cp1, cp2)

    if has_pre:
        png_specs.append((pre_png, [], 0, 0, None, []))

    for pi, pt in enumerate(points):
        adj = max(0.0, pt["timecode"])
        completed = [tuple(s) for s in pt.get("completed_sets", [])]
        cur_p1 = pt.get("cur_p1", 0)
        cur_p2 = pt.get("cur_p2", 0)
        show_until = points[pi + 1]["timecode"] if pi + 1 < len(points) else duration
        is_set_end = (cur_p1 == 0 and cur_p2 == 0 and len(completed) > 0)
        is_last_point = (pi + 1 == len(points))
        if is_set_end and is_last_point:
            sv = pt["score"].split("-")
            png_p1, png_p2 = int(sv[0]), int(sv[1])
            png_completed = completed[:-1]
        else:
            png_p1, png_p2 = cur_p1, cur_p2
            png_completed = completed

        if is_set_end:
            serving = None
        else:
            serving = _server_at(len(completed), cur_p1 + cur_p2, cur_p1, cur_p2,
                                 points[pi + 1]["timecode"] if pi + 1 < len(points) else None)

        # Forme : les 8 derniers points du set AFFICHÉ (point courant inclus). Elle
        # repart de zéro avec le set, comme le score courant qu'elle accompagne.
        last_points = _set_form(points, pi, len(png_completed))
        png = os.path.join(tmp_dir, f"score_{pi:03d}.tga")
        png_specs.append((png, png_completed, png_p1, png_p2, serving, last_points))
        pt_filters.append((pi, adj, max(adj, show_until), png))

    from concurrent.futures import ThreadPoolExecutor

    def _gen(spec):
        path, compl, pp1, pp2, srv, lastp = spec
        _make_scorecard_png(p1n, p2n, compl, pp1, pp2, path, width, height,
                            serving=srv, p1_rank=p1_rank, p2_rank=p2_rank,
                            last_points=lastp)

    with ThreadPoolExecutor(max_workers=4) as pool:  # 14 threads par défaut dégradent sous charge
        list(pool.map(_gen, png_specs))

    prev = "[0:v]"

    if has_pre:
        safe = pre_png.replace("'", "\\'")
        lines.append(f"movie='{safe}'[imgpre];")
        lines.append(
            f"{prev}[imgpre]overlay=x={margin}:y=H-h-{margin}"
            f":enable='between(t\\,0.000\\,{first_adj:.3f})'"
            f"[vpre];"
        )
        prev = "[vpre]"

    for pi, adj, show_until, png in pt_filters:
        safe = png.replace("'", "\\'")
        is_last = pi == len(pt_filters) - 1
        out = "[vout]" if is_last else f"[ov{pi}]"
        lines.append(f"movie='{safe}'[img{pi}];")
        lines.append(
            f"{prev}[img{pi}]overlay=x={margin}:y=H-h-{margin}"
            f":enable='between(t\\,{adj:.3f}\\,{show_until:.3f})'"
            f"{out};"
        )
        prev = out

    if lines[-1].endswith(";"):
        lines[-1] = lines[-1][:-1]

    return "".join(lines), [spec[0] for spec in png_specs]


def _rotation_chain(rotations, cuts, duration, width, height,
                    src: str = "[0:v]", dst: str = "[vrot]") -> str:
    """Redresse les intervalles pivotés, sur le cadre de sortie inchangé.

    La sortie garde les dimensions de la source : un morceau filmé dans l'autre
    sens est donc pivoté puis remis à l'échelle dans le même cadre, bandes noires
    comprises. Les intervalles arrivent en timecodes SOURCE et sont recalés sur la
    timeline montée, coupes retirées, comme les incrustations de score.

    Une seule branche par angle : deux passages à 90° partagent le même transpose,
    au lieu de filtrer la vidéo entière une fois de plus.
    """
    spans: dict[int, list[tuple[float, float]]] = {}
    for r in rotations:
        angle = int(r["angle"]) % 360
        if angle not in (90, 180, 270):
            continue
        a = _adjusted_time(r["start"], cuts, duration)
        b = _adjusted_time(r["end"], cuts, duration)
        if b - a > 0.02:
            spans.setdefault(angle, []).append((a, b))
    if not spans:
        return f"{src}null{dst};"

    angles = sorted(spans)
    labels = [f"[rsrc{i}]" for i in range(len(angles))]
    lines = [f"{src}split={len(angles) + 1}[rbase]" + "".join(labels) + ";"]
    prev = "[rbase]"
    for i, angle in enumerate(angles):
        lines.append(
            f"{labels[i]}{_rotation_filter(angle)},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[rdone{i}];"
        )
        enable = "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in spans[angle])
        out = dst if i == len(angles) - 1 else f"[rmix{i}]"
        lines.append(f"{prev}[rdone{i}]overlay=x=0:y=0:enable='{enable}'{out};")
        prev = out
    return "".join(lines)


def _build_filter(
    actions,
    duration,
    overlay_dur,
    font_size,
    p1n,
    p2n,
    tmp_dir=None,
    first_server=1,
    score_hide_at: float | None = None,
    score_fade_dur: float = 0.8,
    on_prep=None,
    margin: int = 28,
    rotations=None,
    width: int = 1920,
    height: int = 1080,
    src_v: str = "[0:v]",
    src_a: str = "[0:a]",
    p1_rank: str = "",
    p2_rank: str = "",
    lead_frames: int = 0,
    lead: float = 0.0,
    fps: float = 30.0,
    score_show_from: float | None = None,
    anim_fps: float = 0.0,
    match_format: int = 5,
):
    """Graphe ffmpeg du montage (rotation, score, audio).

    Carte d'intro (cf. export/intro) : `lead_frames` > 0 ajoute AVANT le
    montage `lead` s d'image figée (1re image, dézoomée sur fond flou), et
    décale d'autant l'audio (silence puis court fondu d'entrée) et toutes les
    incrustations. `score_show_from` : aucune vignette de score avant cet
    instant (fin du retrait de l'intro). Sans intro : graphe inchangé.

    `anim_fps` > 0 et score_anim.SCORE_ANIM_STYLE ≠ 0 : les changements de
    score tournent comme un compteur (cf. export/score_anim). Sinon : graphe
    strictement identique.

    Balle de set / de match (cf. cards._set_point_state, `match_format` =
    au meilleur des N sets) : un bandeau apparaît au-dessus de la vignette
    pendant tout l'échange où elle se joue, avec la vignette, et disparaît
    quand le score change (fondu court d'entrée et de sortie).
    """
    cuts   = sorted([a for a in actions if a["type"] == "cut"], key=lambda x: x["start"])
    # Ordre de la video : le calcul des sets depend de l'enchainement reel des
    # points, pas de l'ordre dans lequel ils ont ete poses.
    points = sorted([a for a in actions if a["type"] == "point"],
                    key=lambda pt: pt["timecode"])

    kept = _build_kept_segments(cuts, duration)

    n = len(kept)
    lines = []
    # Le découpage/recollage est fait par l'APPELANT (un input `-ss/-to` par segment
    # + filtre `concat`, cf. ExportWorker) : la vidéo arrive déjà montée sur
    # src_v/src_a. La rotation vient AVANT l'incrustation du score : la vignette doit
    # rester droite et à sa place, quel que soit le sens du morceau qu'elle survole.
    rot_chain = _rotation_chain(rotations or [], cuts, duration, width, height, src=src_v)
    a_chain = f"{src_a}aresample=async=1000"
    off = 0.0
    if lead_frames > 0:
        # Intro : image figée + zoom juste après la rotation (la vidéo est
        # redressée avant d'être figée) ; tout ce qui suit est décalé de `lead`.
        rot_chain = (rot_chain.replace("[vrot]", "[vrot0]", 1)
                     + _intro_zoom_chain("[vrot0]", "[vrot]", width, height,
                                         lead_frames, lead, fps))
        a_chain += (f",adelay=delays={int(round(lead * 1000))}:all=1"
                    f",afade=t=in:st={lead:.4f}:d=0.15")
        off = lead

    if not points or tmp_dir is None:
        return (rot_chain.replace("[vrot]", "[vout]", 1)
                + f"{a_chain}[aout]"), []

    lines.append(rot_chain.replace("[vrot]", "[vcombined]", 1))
    lines.append(f"{a_chain}[aout];")

    # ── Phase 1: collect all PNG specs (pure computation, no I/O) ─────
    first_adj = _adjusted_time(points[0]["timecode"], cuts, duration)
    has_pre   = first_adj > 0.01
    first_adj += off
    pre_png   = os.path.join(tmp_dir, "score_pre.tga") if has_pre else None

    png_specs  = []   # (path, completed, p1, p2) — fed to parallel generator
    pt_filters = []   # (pi, adj, show_until, path) — used for filter building
    anim_jobs  = []   # changements de score à animer (cf. score_anim)
    sp_spans   = []   # balles de set / de match : [début, fin, (joueur, texte)]

    # Inversions forcées (touche F) : comptées jusqu'au point SUIVANT, celui dont
    # la vignette annonce le serveur.
    swaps = [a["timecode"] for a in actions if a["type"] == "swap"]

    def _server_at(set_index, total_in_set, cp1, cp2, next_t=None):
        return server_at(first_server, set_index, cp1, cp2, flips_before(swaps, next_t))

    if has_pre:
        png_specs.append((pre_png, [], 0, 0, None, []))

    for pi, pt in enumerate(points):
        adj       = _adjusted_time(pt["timecode"], cuts, duration) + off
        completed = [tuple(s) for s in pt.get("completed_sets", [])]
        cur_p1    = pt.get("cur_p1", 0)
        cur_p2    = pt.get("cur_p2", 0)
        show_until = (
            _adjusted_time(points[pi + 1]["timecode"], cuts, duration) + off
            if pi + 1 < len(points) else 99999.0
        )
        is_set_end    = (cur_p1 == 0 and cur_p2 == 0 and len(completed) > 0)
        is_last_point = (pi + 1 == len(points))
        if is_set_end and is_last_point:
            sv = pt["score"].split("-")
            png_p1, png_p2 = int(sv[0]), int(sv[1])
            png_completed  = completed[:-1]
        else:
            png_p1, png_p2 = cur_p1, cur_p2
            png_completed  = completed

        if pi + 1 == len(points) and score_hide_at is not None:
            show_until = min(show_until, score_hide_at)

        # Who serves after this point (shown during overlay)
        if is_set_end:
            serving = None  # between sets, skip arrow
        else:
            serving = _server_at(len(completed), cur_p1 + cur_p2, cur_p1, cur_p2,
                                 points[pi + 1]["timecode"] if pi + 1 < len(points) else None)

        # Forme : les 8 derniers points du set AFFICHÉ (point courant inclus). Elle
        # repart de zéro avec le set, comme le score courant qu'elle accompagne.
        last_points = _set_form(points, pi, len(png_completed))
        png = os.path.join(tmp_dir, f"score_{pi:03d}.tga")
        png_specs.append((png, png_completed, png_p1, png_p2, serving, last_points))
        # Animation du changement de score : seulement si la vignette d'avant
        # était visible (pas avant la fin de l'intro, pas sur le 1er point sans
        # vignette « 0-0 » avant lui).
        if len(png_specs) >= 2 and (score_show_from is None or adj >= score_show_from):
            anim_jobs.append({"pi": pi, "t0": adj, "t_end": show_until,
                              "old": png_specs[-2], "new": png_specs[-1]})
        if score_show_from is not None:
            adj = max(adj, score_show_from)
        pt_filters.append((pi, adj, show_until, png))

        # Balle de set / de match pour l'échange qui suit ce point : même
        # fenêtre que la vignette. Deux balles consécutives (10-7 puis 10-8)
        # forment un seul bandeau, sans clignoter à la coupe entre les deux.
        sp = None if is_set_end else _set_point_state(png_completed, png_p1, png_p2,
                                                      match_format)
        if sp is not None and show_until - adj > 0.05:
            if sp_spans and sp_spans[-1][2] == sp and abs(sp_spans[-1][1] - adj) < 1e-3:
                sp_spans[-1][1] = show_until
            else:
                sp_spans.append([adj, show_until, sp])

    # ── Phase 2: generate all PNGs in parallel ─────────────────────────
    from concurrent.futures import ThreadPoolExecutor

    total_png = len(png_specs)
    _done = [0]
    _lock = threading.Lock()
    card_sizes = {}   # taille de chaque vignette (placement du bandeau de balle de set)

    def _gen(spec):
        path, compl, pp1, pp2, srv, lastp = spec
        card_sizes[path] = _make_scorecard_png(p1n, p2n, compl, pp1, pp2, path, width, height,
                                               serving=srv, p1_rank=p1_rank, p2_rank=p2_rank,
                                               last_points=lastp)
        if on_prep is not None:
            with _lock:
                _done[0] += 1
                on_prep(_done[0], total_png)

    with ThreadPoolExecutor(max_workers=4) as pool:  # 14 threads par défaut dégradent sous charge
        list(pool.map(_gen, png_specs))

    # ── Phase 2 bis : animations « compteur » des chiffres (optionnelles) ──
    anims = {}
    from pongedit.export import score_anim as _sa
    if anim_fps > 0 and _sa.SCORE_ANIM_STYLE:
        try:
            anims = _sa.render_score_anims(anim_jobs, p1n, p2n, width, height,
                                           p1_rank, p2_rank, anim_fps, tmp_dir)
        except Exception:
            # Repli : vignettes fixes, comme avant.
            import traceback; traceback.print_exc()
            anims = {}

    # ── Phase 3: chained overlay filters with enable expressions ───────
    png_paths = [s[0] for s in png_specs]
    prev = "[vcombined]"

    if has_pre:
        safe = pre_png.replace("'", "\\'")
        lines.append(f"movie='{safe}'[imgpre];")
        lines.append(
            f"{prev}[imgpre]overlay=x={margin}:y=H-h-{margin}"
            f":enable='between(t\\,{score_show_from or 0.0:.3f}\\,{first_adj:.3f})'"
            f"[vpre];"
        )
        prev = "[vpre]"

    for pi, adj, show_until, png in pt_filters:
        safe    = png.replace("'", "\\'")
        is_last = pi == len(points) - 1
        out     = ("[vsc]" if anims else "[vout]") if is_last else f"[ov{pi}]"
        img_label = f"[img{pi}]"
        an = anims.get(pi)
        if an is not None and an["kind"] == "card":
            # Fin de set : la vignette animée REMPLACE la fixe pendant l'anim.
            # Même seuil (arrondi au ms) que la fin de l'anim : ni trou ni doublon.
            adj = round(an["t_b"], 3)
        if (
            is_last and score_hide_at is not None and
            score_fade_dur > 0 and show_until - adj > 0.05
        ):
            fade_start = max(adj, show_until - score_fade_dur)
            lines.append(
                f"movie='{safe}',format=rgba,"
                f"fade=t=out:st={fade_start:.3f}:d={max(0.05, show_until - fade_start):.3f}:alpha=1"
                f"{img_label};"
            )
        else:
            lines.append(f"movie='{safe}'{img_label};")
        lines.append(
            f"{prev}{img_label}overlay=x={margin}:y=H-h-{margin}"
            f":enable='between(t\\,{adj:.3f}\\,{show_until:.3f})'"
            f"{out};"
        )
        prev = out

    if anims:
        # Séquences d'images posées par-dessus, à la position de leur case dans
        # la vignette (vignette : x=margin, y=H-h-margin). Horodatage exact à la
        # cadence de la vidéo (base de temps fine, sinon arrondi au 1/25 s).
        from fractions import Fraction
        fr = Fraction(anim_fps).limit_denominator(1001)
        items = sorted(anims.items(), key=lambda kv: kv[1]["t0"])
        for k, (pi, an) in enumerate(items):
            safe = an["pattern"].replace("'", "\\'")
            out = "[vout]" if k == len(items) - 1 else f"[sa{k}]"
            x = margin + an["x"]
            y = height - an["card_h"] - margin + an["y"]
            lines.append(
                f"movie='{safe}':f=image2,settb=AVTB,"
                f"setpts=N*{fr.denominator}/{fr.numerator}/TB+{an['t0']:.4f}/TB[san{k}];"
            )
            lines.append(
                f"{prev}[san{k}]overlay=x={x}:y={y}:eof_action=pass"
                f":enable='gte(t\\,{an['t0']:.4f})*lt(t\\,{an['t_b']:.3f})'{out};"
            )
            prev = out

    # ── Phase 4 : bandeau « balle de set / de match » ─────────────────
    if sp_spans:
        # Le dernier maillon du score sortait sur [vout] : on le rebranche.
        lines = [l.replace("[vout]", "[vbn]") for l in lines]
        prev = "[vbn]"
        from fractions import Fraction
        fr = Fraction(anim_fps if anim_fps > 0 else fps).limit_denominator(1001)
        card_h = max((s[1] for s in card_sizes.values()), default=0)
        gap = max(1, int(round(SETPOINT_GAP * _overlay_unit(width, height)
                               * _cards.SCORECARD_SCALE)))
        banners = {}
        for k, (t0, t1, (who, text)) in enumerate(sp_spans):
            key = (who, text)
            if key not in banners:
                bpath = os.path.join(tmp_dir, f"setpoint_{who}_{len(banners)}.tga")
                banners[key] = (bpath, _make_setpoint_png(text, who, bpath, width, height))
            bpath, (_bw, bh) = banners[key]
            safe = bpath.replace("'", "\\'")
            y = height - margin - card_h - gap - bh
            fd = min(0.2, (t1 - t0) / 3)
            out = "[vout]" if k == len(sp_spans) - 1 else f"[bn{k}]"
            # Seules les images des deux fondus existent : n images qui entrent
            # à t0, puis n images qui sortent jusqu'à t1. Entre les deux,
            # overlay garde la dernière image reçue (opaque). Base de temps
            # fine (AVTB) : sinon les instants sont arrondis au 1/25 s.
            n = max(2, int(round(fd * fr)) + 1)
            step = f"{fr.denominator}/{fr.numerator}"
            lines.append(
                f"movie='{safe}',loop=loop={2 * n - 1}:size=1:start=0,settb=AVTB,"
                f"setpts='if(lt(N\\,{n})\\,{t0:.4f}+N*{step}"
                f"\\,{t1 - fd:.4f}+(N-{n})*{step})/TB',format=rgba,"
                f"fade=t=in:st={t0:.4f}:d={fd:.3f}:alpha=1,"
                f"fade=t=out:st={t1 - fd:.4f}:d={fd:.3f}:alpha=1[sp{k}];"
            )
            lines.append(
                f"{prev}[sp{k}]overlay=x={margin}:y={y}:eof_action=pass"
                f":enable='between(t\\,{t0:.4f}\\,{t1:.4f})'{out};"
            )
            prev = out

    if lines[-1].endswith(";"):
        lines[-1] = lines[-1][:-1]

    return "".join(lines), png_paths


def _intro_zoom_chain(src: str, dst: str, width: int, height: int,
                      n_lead: int, lead: float, fps: float) -> str:
    """Image figée + dézoom de la carte d'intro (cf. export/intro).

    `tpad` clone la 1re image pendant `n_lead` images (durée `lead`, multiple
    exact de la période : pas de VFR). Seul le début (intro + re-zoom) passe
    par la composition : fond = même image agrandie et assombrie (flou
    optionnel) ; dessus, la vidéo à l'échelle zoom_at(t), centrée. La
    composition est ensuite posée sur le flux complet et s'arrête
    (eof_action=pass) une fois le zoom revenu à 1 : les images suivantes sont
    celles de la source, et la dernière image composée l'est à 100 % pile.

    Mouvement sous-pixel : scale/overlay ne savent faire que des tailles
    paires et des positions entières (un zoom lent avançait donc par marches
    de 0 ou 2 px : tremblement). La taille exacte (W·z, H·z) tombe dans une
    case de la grille des tailles paires (wl ou wl+2) × (hl ou hl+2), de
    fractions (fx, fy). On compose la vidéo, toujours centrée pile, à 3 des 4
    coins de la case (interpolation barycentrique, linéaire par morceaux) :
    (wl, hl), le coin intermédiaire (wl+2, hl) si fx ≥ fy sinon (wl, hl+2),
    et (wl+2, hl+2), puis on les mélange (maskedmerge) avec des poids
    1−max, |fx−fy|, min. Les poids viennent de masques uniformes calculés par
    ffmpeg lui-même (geq sur une image 2×2, agrandie) avec les mêmes
    expressions que les tailles : synchronisés à l'image près, sans sendcmd,
    et en profondeur native (8 ou 10 bits, HLG conservé : aucune conversion
    de couleurs ; overlay en format=auto, sinon il repasse en 8 bits).
    """
    import math
    from pongedit.export.intro import BG_BLUR, BG_DIM, REZOOM_DUR, zoom_expr
    # Même temps (pts de l'image) des deux côtés : t pour scale, T pour geq.
    # (Le `n` de scale est décalé d'une image : ne pas l'utiliser.)
    zs = zoom_expr(lead, "t")
    zg = zoom_expr(lead, "T")
    m = n_lead + int(math.ceil(REZOOM_DUR * fps)) + 2
    bw = 480 if width >= height else 270
    bh = max(2, int(round(bw * height / width / 2)) * 2)
    gw, gh = int(width * 1.08) // 2 * 2, int(height * 1.08) // 2 * 2
    # Taille paire basse (le +1e-4 évite qu'un arrondi flottant fasse
    # basculer la taille pile sur une valeur paire, ex. 0,85 × 1920 = 1632).
    wl = f"2*floor({width}*({zs})/2+1e-4)"
    hl = f"2*floor({height}*({zs})/2+1e-4)"
    def _frac(dim: int, z: str) -> str:
        return f"clip({dim}*({z})/2-floor({dim}*({z})/2+1e-4)\\,0\\,1)"
    fxs, fys = _frac(width, zs), _frac(height, zs)       # côté scale (t)
    fx, fy = _frac(width, zg), _frac(height, zg)         # côté geq (T)
    wm = f"{wl}+2*gte({fxs}\\,{fys})"                   # coin intermédiaire
    hm = f"{hl}+2*lt({fxs}\\,{fys})"
    # Poids : r = C00·(1−a) + Cm·a, puis sortie = r·(1−b) + C11·b
    # avec b = min(fx, fy) et a = |fx − fy| / (1 − b).
    wb = f"min({fx}\\,{fy})"
    wa = f"clip(abs({fx}-{fy})/max(1-{wb}\\,1e-6)\\,0\\,1)"

    def _mask(f: str) -> str:
        return (f"geq=lum='lum(X\\,Y)*({f})':cb='cb(X\\,Y)*({f})':cr='cr(X\\,Y)*({f})',"
                f"scale={width}:{height}:flags=neighbor")

    comps = ""
    for k, (w, h) in {"0": (wl, hl), "m": (wm, hm), "1": (f"{wl}+2", f"{hl}+2")}.items():
        comps += (f"[izf{k}]scale=w='{w}':h='{h}':eval=frame[izs{k}];"
                  f"[izb{k}][izs{k}]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame"
                  f":format=auto[izc{k}];")
    return (
        f"{src}tpad=start={n_lead}:start_mode=clone,split=2[izA][izB];"
        f"[izB]trim=end_frame={m},split=3[izbg0][izfg0][izm0];"
        + (f"[izbg0]scale={bw}:{bh},gblur=sigma={BG_BLUR}:steps=4,scale={gw}:{gh}:flags=bicubic,"
           if BG_BLUR > 0 else
           # Sans flou : la même image simplement agrandie, en pleine définition.
           f"[izbg0]scale={gw}:{gh}:flags=bicubic,")
        + f"crop={width}:{height},lutyuv=y='(val-minval)*{BG_DIM}+minval',"
        f"split=3[izb0][izbm][izb1];"
        f"[izfg0]split=3[izf0][izfm][izf1];"
        + comps +
        # Masques uniformes = poids (valeur max du format × fraction) ;
        # lutyuv donne le maxval « studio » (940 en 10 bits) ; minval×16−1 = 2^bits − 1.
        f"[izm0]scale=2:2,lutyuv=y='max(minval*16-1\\,maxval)':u='max(minval*16-1\\,maxval)'"
        f":v='max(minval*16-1\\,maxval)',split=2[izma0][izmb0];"
        f"[izma0]{_mask(wa)}[izma];"
        f"[izmb0]{_mask(wb)}[izmb];"
        f"[izc0][izcm][izma]maskedmerge[izr];"
        f"[izr][izc1][izmb]maskedmerge[izfx];"
        f"[izA][izfx]overlay=x=0:y=0:eof_action=pass:format=auto{dst};"
    )


def _rotation_filter(angle: int) -> str:
    return {90: "transpose=1", 270: "transpose=2",
            180: "transpose=1,transpose=1"}.get(angle, "")
