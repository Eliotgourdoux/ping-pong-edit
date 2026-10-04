"""Incrustations Pillow : vignette de score, carte de stats."""

import os, math
from functools import lru_cache

from pongedit.utils import _probe_video_props
from pongedit.export.momentum import (
    _momentum_series, _momentum_range, _momentum_x, _momentum_masks, _momentum_compose,
)


def _make_stats_card_png(stats: dict, p1n: str, p2n: str, path: str, W: int, H: int,
                         p1_rank: str = "", p2_rank: str = "",
                         anim: dict | None = None,
                         points_seq: list | None = None) -> tuple[int, int]:
    """Carte « Match terminé », direction artistique « broadcast TV ».

    `W`/`H` sont les dimensions de la VIDÉO : la carte se dimensionne elle-même,
    en multiples de `u` (cf. _overlay_unit), et sa hauteur suit le contenu. Aucune
    cote en pixels fixes, aucune colonne posée « au jugé » : chaque colonne est
    mesurée, et si l'ensemble déborde, la police est réduite avant de tronquer.
    Retourne (largeur, hauteur) de l'image écrite.

    `points_seq` : vainqueur (1/2) de chaque point, dans l'ordre du match. S'il
    est fourni, le tableau gagne la « dynamique du match » (cf. momentum) sous
    le détail par set, et perd la ligne « Sets gagnés » (redite du score en
    tête). Sans lui, le tableau est dessiné exactement comme avant.

    `anim` (dict vide fourni par l'appelant) : la courbe de dynamique sera
    ANIMÉE par une incrustation à part. L'image écrite a alors un TROU
    transparent à son emplacement ; `anim["momentum"]` reçoit la géométrie du
    rectangle et son contenu final (cf. momentum._render_momentum_anim).
    """
    from PIL import Image, ImageDraw

    display = _find_display_font()
    label   = _find_label_font()
    p1n = p1n.strip() or "Joueur 1"
    p2n = p2n.strip() or "Joueur 2"
    p1_rank = (p1_rank or "").strip()
    p2_rank = (p2_rank or "").strip()
    sets = stats.get("sets", [])
    n_sets = len(sets)
    mgeo: dict = {}                # position de la courbe de dynamique (dernier layout)
    series = _momentum_series(points_seq, sets) if points_seq else []

    def pct_parts(n: int, d: int) -> tuple[str, str]:
        return (f"{100 * n // d}%", f"{n}/{d}") if d else ("—", "")

    def layout(u: float):
        """Construit la liste des opérations de dessin pour une unité `u` donnée.
        Retourne (card_w, card_h, ops) ; ops = fonctions prenant un ImageDraw."""
        def U(v: float) -> int:
            return max(1, int(round(v * u)))

        card_w = (U(700) // 2) * 2
        PAD = U(40)
        cw = card_w - 2 * PAD          # largeur utile
        x0 = PAD

        f_title = _load_font(label,   U(14))
        f_name  = _load_font(display, U(40))
        f_rank  = _load_font(label,   U(15))
        f_big   = _load_font(display, U(84))
        f_sec   = _load_font(label,   U(12))
        f_lbl   = _load_font(label,   U(16))
        f_val   = _load_font(display, U(34))
        f_sub   = _load_font(label,   U(14))
        f_hdr   = _load_font(label,   U(12))
        f_set   = _load_font(display, U(32))
        f_srv   = _load_font(label,   U(13))

        ops = []
        y = PAD

        # Titre + en-tête : ajoutés à la fin (cf. header(0, total_w)), une fois la
        # largeur de la carte connue.
        def header(hx, hw, y=y):
            # ── Titre ────────────────────────────────────────────────────────
            ops.append(lambda d, y=y: d.text((hx + hw / 2, y), "M A T C H   T E R M I N É",
                                             font=f_title, fill=(*OV_MUTED, 255), anchor="mt"))
            y += U(14) + U(26)

            # ── En-tête : nom 1 | score en sets | nom 2 ───────────────────────
            big = f"{stats['p1_sets']}  –  {stats['p2_sets']}"
            big_w = _text_w(big, f_big)
            name_max = (hw - 2 * PAD - big_w) / 2 - U(30)
            # Noms longs : on réduit d'abord la police (jusqu'à 30 u) avant de tronquer.
            for _sz in (40, 36, 33, 30):
                f_name = _load_font(display, U(_sz))
                if max(_text_w(p1n.upper(), f_name), _text_w(p2n.upper(), f_name)) <= name_max:
                    break
            n1 = _fit_text(p1n.upper(), f_name, name_max)
            n2 = _fit_text(p2n.upper(), f_name, name_max)
            r1 = _fit_text(p1_rank, f_rank, name_max)
            r2 = _fit_text(p2_rank, f_rank, name_max)
            head_h = U(96)
            cy = y + head_h / 2
            ops.append(lambda d, cy=cy: d.text((hx + hw / 2, cy + U(6)), big, font=f_big,
                                               fill=(*OV_WHITE, 255), anchor="mm"))
            for nm, rk, col, anchor_x, side in ((n1, r1, OV_P1, hx + PAD, "l"), (n2, r2, OV_P2, hx + hw - PAD, "r")):
                band_x = anchor_x if side == "l" else anchor_x - U(5)
                ops.append(lambda d, bx=band_x, col=col, cy=cy: d.rounded_rectangle(
                    [bx, cy - U(26), bx + U(5) - 1, cy + U(26)], radius=U(2), fill=(*col, 255)))
                tx = anchor_x + U(16) if side == "l" else anchor_x - U(16)
                if rk:
                    ops.append(lambda d, tx=tx, cy=cy, nm=nm, a=side: d.text(
                        (tx, cy - U(2)), nm, font=f_name, fill=(*OV_WHITE, 255), anchor=a + "s"))
                    ops.append(lambda d, tx=tx, cy=cy, rk=rk, a=side, col=col: d.text(
                        (tx, cy + U(8)), rk, font=f_rank, fill=(*col, 255), anchor=a + "t"))
                else:
                    ops.append(lambda d, tx=tx, cy=cy, nm=nm, a=side: d.text(
                        (tx, cy), nm, font=f_name, fill=(*OV_WHITE, 255), anchor=a + "m"))

        y += U(14) + U(26) + U(96) + U(26)

        def hline(yy):
            ops.append(lambda d, yy=yy: d.rectangle([x0, yy, x0 + cw - 1, yy], fill=_ov_tint(0.09, 228)))

        def section(yy, text):
            ops.append(lambda d, yy=yy, text=text: d.text((x0, yy), text, font=f_sec,
                                                          fill=(*OV_FAINT, 255), anchor="lt"))

        hline(y); y += U(24)
        section(y, "S T A T I S T I Q U E S"); y += U(12) + U(18)

        # ── Lignes globales : valeur J1 | libellé | valeur J2 ─────────────
        p1_ret_won = stats["p1_pts"] - stats["p1_won_srv"]
        p2_ret_won = stats["p2_pts"] - stats["p2_won_srv"]
        rows = [] if series else [
            ("Sets gagnés",   (str(stats["p1_sets"]), ""), (str(stats["p2_sets"]), ""))]
        rows += [
            ("Points gagnés", (str(stats["p1_pts"]), ""),  (str(stats["p2_pts"]), "")),
            ("Service gagné", pct_parts(stats["p1_won_srv"], stats["p1_tot_srv"]),
                              pct_parts(stats["p2_won_srv"], stats["p2_tot_srv"])),
            ("Retour gagné",  pct_parts(p1_ret_won, stats["p2_tot_srv"]),
                              pct_parts(p2_ret_won, stats["p1_tot_srv"])),
        ]
        lbl_w = max(_text_w(r[0], f_lbl) for r in rows) + 2 * U(20)
        val_w = max(_text_w(v[0], f_val) + (_text_w(v[1], f_sub) + U(8) if v[1] else 0)
                    for r in rows for v in (r[1], r[2]))
        # Si valeurs + libellé débordent, l'appelant relance avec un `u` plus petit.
        overflow = (2 * val_w + lbl_w) > cw
        row_h = U(44)
        for lbl_txt, v1, v2 in rows:
            cy = y + row_h / 2
            ops.append(lambda d, cy=cy, t=lbl_txt: d.text((card_w / 2, cy), t, font=f_lbl,
                                                          fill=(*OV_MUTED, 255), anchor="mm"))
            xl = card_w / 2 - lbl_w / 2       # bord droit de la valeur J1
            xr = card_w / 2 + lbl_w / 2       # bord gauche de la valeur J2
            win1 = win2 = False
            try:
                a = float(v1[0].rstrip("%")); b = float(v2[0].rstrip("%"))
                win1, win2 = a > b, b > a
            except ValueError:
                pass
            # Valeur J1 : alignée à droite sur xl ; la fraction (petite) à droite du %.
            def draw_val(d, x_edge, v, col, win, side, cy=cy):
                main, sub = v
                mw = _text_w(main, f_val)
                sw = _text_w(sub, f_sub) + U(8) if sub else 0
                if side == "l":
                    x_main = x_edge - sw - mw
                else:
                    x_main = x_edge
                fill = (*col, 255) if win else (*OV_WHITE, 255)
                d.text((x_main, cy + U(2)), main, font=f_val, fill=fill, anchor="lm")
                if sub:
                    d.text((x_main + mw + U(8), cy + U(3)), sub, font=f_sub,
                           fill=(*OV_FAINT, 255), anchor="lm")
            ops.append(lambda d, f=draw_val, xl=xl, v1=v1, w=win1: f(d, xl, v1, OV_P1, w, "l"))
            ops.append(lambda d, f=draw_val, xr=xr, v2=v2, w=win2: f(d, xr, v2, OV_P2, w, "r"))
            y += row_h

        # ── Détail par set : feuille de match ─────────────────────────────
        if n_sets:
            y += U(14)
            hline(y); y += U(24)
            section(y, "D É T A I L   P A R   S E T"); y += U(12) + U(18)

            f_pn = _load_font(display, U(22))
            pn_w = max(_text_w(p1n.upper(), f_pn), _text_w(p2n.upper(), f_pn),
                       _text_w("Service gagné", f_srv))
            pn_w = min(pn_w, U(170)) + U(16)
            col_w = (cw - pn_w) / n_sets
            hdr_h = U(24)
            for i in range(n_sets):
                cx = x0 + pn_w + col_w * (i + 0.5)
                ops.append(lambda d, cx=cx, yy=y, i=i: d.text((cx, yy), f"SET {i + 1}", font=f_hdr,
                                                             fill=(*OV_FAINT, 255), anchor="mt"))
            y += hdr_h

            score_h = U(46)
            for pi, (nm, col) in enumerate(((p1n, OV_P1), (p2n, OV_P2))):
                cy = y + score_h / 2
                ops.append(lambda d, cy=cy, nm=nm, col=col: d.text(
                    (x0, cy), _fit_text(nm.upper(), f_pn, pn_w - U(16)), font=f_pn,
                    fill=(*col, 255), anchor="lm"))
                for i, sd in enumerate(sets):
                    sp1, sp2 = sd["score"]
                    mine, other = (sp1, sp2) if pi == 0 else (sp2, sp1)
                    won = mine > other
                    cx = x0 + pn_w + col_w * (i + 0.5)
                    if won:
                        bw, bh = min(col_w - U(6), U(54)), U(40)
                        ops.append(lambda d, cx=cx, cy=cy, bw=bw, bh=bh, col=col: d.rounded_rectangle(
                            [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], radius=U(6),
                            fill=(*col, 255)))
                    ops.append(lambda d, cx=cx, cy=cy, v=mine, won=won: d.text(
                        (cx, cy + U(2)), str(v), font=f_set,
                        fill=(*OV_WHITE, 255) if won else (*OV_FAINT, 255), anchor="mm"))
                y += score_h

            # Service gagné par set : une ligne par joueur, identifiée par une
            # pastille de sa couleur ; chaque cellule = pourcentage + fraction +
            # mini-jauge remplie proportionnellement. Se lit sans légende.
            y += U(10)
            hline(y); y += U(12)
            srv_h = U(34)
            f_pct = _load_font(display, U(20))
            f_frac = _load_font(label, U(11))
            ops.append(lambda d, yy=y: d.text((x0, yy + srv_h), "Service gagné", font=f_srv,
                                              fill=(*OV_FAINT, 255), anchor="lm"))
            bar_w = min(col_w - U(16), U(56))
            for pi, col in enumerate((OV_P1, OV_P2)):
                cy = y + srv_h / 2
                dx = x0 + pn_w - U(12)
                ops.append(lambda d, cy=cy, dx=dx, col=col: d.ellipse(
                    [dx - U(4), cy - U(4), dx + U(4), cy + U(4)], fill=(*col, 255)))
                for i, sd in enumerate(sets):
                    n_, d_ = (sd["p1_won_srv"], sd["p1_tot_srv"]) if pi == 0 else (sd["p2_won_srv"], sd["p2_tot_srv"])
                    main, sub = pct_parts(n_, d_)
                    cx = x0 + pn_w + col_w * (i + 0.5)
                    ratio = (n_ / d_) if d_ else 0.0
                    ty = cy - U(6)
                    mw = _text_w(main, f_pct)
                    sw = _text_w(sub, f_frac) if sub else 0
                    tx = cx - (mw + (U(5) + sw if sub else 0)) / 2
                    ops.append(lambda d, tx=tx, ty=ty, t=main: d.text(
                        (tx, ty), t, font=f_pct, fill=(*OV_WHITE, 255), anchor="lm"))
                    if sub:
                        ops.append(lambda d, tx=tx + mw + U(5), ty=ty + U(1), t=sub: d.text(
                            (tx, ty), t, font=f_frac, fill=(*OV_FAINT, 255), anchor="lm"))
                    by = cy + U(9)
                    ops.append(lambda d, cx=cx, by=by: d.rounded_rectangle(
                        [cx - bar_w / 2, by, cx + bar_w / 2, by + U(4)], radius=U(2),
                        fill=_ov_tint(0.12, 228)))
                    if ratio > 0:
                        ops.append(lambda d, cx=cx, by=by, r=ratio, col=col: d.rounded_rectangle(
                            [cx - bar_w / 2, by, cx - bar_w / 2 + max(U(4), bar_w * r), by + U(4)],
                            radius=U(2), fill=(*col, 255)))
                y += srv_h

        # ── Dynamique du match : écart de points au fil de chaque set ─────
        # Rectangle du tracé (bords pairs, cf. anim) ; les libellés (S1 + score
        # du set au-dessus, plus grandes avances à gauche) restent HORS du
        # rectangle : l'animation ne redessine que la courbe.
        if series:
            y += U(12)
            hline(y); y += U(24)
            section(y, "D Y N A M I Q U E   D U   M A T C H")
            f_cap = _load_font(label, U(11))
            ops.append(lambda d, yy=y: d.text((x0 + cw, yy), "écart de points dans chaque set",
                                              font=f_cap, fill=(*OV_FAINT, 255), anchor="rt"))
            y += U(12) + U(16)
            f_ms = _load_font(label, U(11))
            f_msc = _load_font(display, U(17))
            f_ax = _load_font(display, U(15))
            pad_x, pad_y = U(8), U(8)
            lab_h = U(22)
            plot_l = x0 + U(34)
            rx = (int(plot_l - pad_x) // 2) * 2
            ry = (int(y + lab_h - pad_y) // 2) * 2
            rw = ((int(x0 + cw + pad_x) - rx) // 2) * 2
            rh = ((U(96) + 2 * pad_y) // 2) * 2
            top, bot = _momentum_range(series)
            pw, ph = rw - 2 * pad_x, rh - 2 * pad_y
            for i, ((xa, xb), s_) in enumerate(zip(_momentum_x(series, pw), series)):
                n1 = sum(1 for a, b in zip(s_, s_[1:]) if b > a)      # points de J1
                sp1, sp2 = n1, len(s_) - 1 - n1
                col = OV_P1 if sp1 > sp2 else OV_P2 if sp2 > sp1 else OV_WHITE
                xs = rx + pad_x + xa
                ops.append(lambda d, xs=xs, yy=ry, t=f"S{i + 1}": d.text(
                    (xs, yy - U(3)), t, font=f_ms, fill=(*OV_FAINT, 255), anchor="ls"))
                sx_ = xs + _text_w(f"S{i + 1}", f_ms) + U(7)
                ops.append(lambda d, sx_=sx_, yy=ry, t=f"{sp1}-{sp2}", col=col: d.text(
                    (sx_, yy - U(3)), t, font=f_msc, fill=(*col, 255), anchor="ls"))
            # Plus grande avance de chacun, à sa hauteur réelle (rien si jamais devant).
            y_zero = ry + pad_y + ph * top / (top + bot)
            unit = ph / (top + bot)
            lead1 = max(max(s_) for s_ in series)
            lead2 = max(-min(s_) for s_ in series)
            for lead, sgn, col in ((lead1, -1, OV_P1), (lead2, 1, OV_P2)):
                if lead > 0:
                    ops.append(lambda d, yy=y_zero + sgn * lead * unit, t=f"+{lead}", col=col: d.text(
                        (rx - U(3), yy), t, font=f_ax, fill=(*col, 255), anchor="rm"))
            mgeo.update(rect=(rx, ry, rw, rh), pad=(pad_x, pad_y), u=u)
            y = ry + rh

        total_w = card_w
        header(0, total_w)
        card_h = ((y + PAD) // 2) * 2
        return total_w, card_h, ops, overflow

    u = _overlay_unit(W, H)
    card_w, card_h, ops, overflow = layout(u)
    # Garde-fous : la carte ne dépasse jamais 90 % de la hauteur ni 92 % de la
    # largeur, et les colonnes de stats ne se chevauchent jamais.
    for _ in range(12):
        if card_h <= 0.90 * H and card_w <= 0.92 * W and not overflow:
            break
        u *= 0.94
        card_w, card_h, ops, overflow = layout(u)

    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = max(2, int(round(14 * u)))
    draw.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=r, fill=(8, 10, 16, 228))
    draw.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=r,
                           outline=_ov_tint(0.07, 228), width=1)
    for op in ops:
        op(draw)

    # Courbe de dynamique : image composée sur le fond exact de la carte, collée
    # telle quelle (le rectangle ne contient rien d'autre que le fond).
    if mgeo:
        rx, ry, rw, rh = mgeo["rect"]
        m_masks = _momentum_masks(series, rw, rh, mgeo["u"], *mgeo["pad"])
        m_img = _momentum_compose(m_masks, OV_P1, OV_P2, 1.0, bg=(8, 10, 16, 228))
        img.paste(m_img, (rx, ry))
        if anim is not None:
            anim["momentum"] = dict(card_size=(card_w, card_h), rect=mgeo["rect"],
                                    masks=m_masks, final=m_img, bg=(8, 10, 16, 228))
            img.paste((8, 10, 16, 0), (rx, ry, rx + rw, ry + rh))

    _ov_save(img, path)
    return card_w, card_h


# ── FFmpeg / Pillow export ─────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _find_font() -> str | None:
    for p in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/Library/Fonts/Arial.ttf",
    ]:
        if os.path.exists(p): return p
    return None


@lru_cache(maxsize=1)
def _find_display_font() -> str | None:
    """Police de la vignette de score : condensée et grasse.

    Une grotesque condensée (DIN) tient deux fois plus de chiffres à hauteur
    égale qu'une Helvetica, ce qui laisse grossir le score courant sans faire
    déborder la vignette — c'est la typo du graphisme sportif télé."""
    for p in [
        "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf",
        "/System/Library/Fonts/Avenir Next Condensed.ttc",
        "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
    ]:
        if os.path.exists(p):
            return p
    return _find_font()


@lru_cache(maxsize=64)
def _load_font(font_path: str | None, size: int):
    """Charge (et mémorise) une police PIL. Évite de relire le .ttc depuis le
    disque pour chaque image de score — le gros du temps de prépa avant export."""
    from PIL import ImageFont
    try:
        return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default(size=size)


# Couleurs partagées par toutes les incrustations (vignette de score, carte de fin).
# Même palette que l'interface (UI["p1"] / UI["p2"]) pour que la vidéo et l'app
# racontent la même chose.
# Teintes volontairement moins saturées que l'UI : un aplat plein écran sur une
# image vidéo fatigue vite, une teinte « broadcast » plus profonde reste lisible.
OV_P1     = (56, 108, 212)
OV_P2     = (222, 104, 66)
OV_GOLD   = (240, 196, 80)
OV_INK    = (10, 12, 18)
OV_WHITE  = (255, 255, 255)
OV_MUTED  = (150, 158, 175)
OV_FAINT  = (95, 102, 120)
OV_BG_A   = 217   # opacité du fond des incrustations (~85 %)
SCORECARD_SCALE = 1.35   # le score est nettement plus grand que l'unité de base : il doit se lire du canapé (1,15 → 1,55 → 1,35 le 2026-09-30)
SCORECARD_BG_A  = 236    # fond du score plus dense (~93 %) que la carte de stats : sol orange derrière

# Les incrustations sont dessinées en sRGB. Quand la vidéo est HDR HLG (iPhone :
# BT.2020 + arib-std-b67), le filtre `overlay` de ffmpeg recopie nos valeurs telles
# quelles dans un signal HLG : le bleu devient fluo, l'orange rouge vif, le fond
# grisâtre. On convertit donc nous-mêmes les pixels (sRGB → linéaire → primaires
# BT.2020 → OETF HLG, blanc SDR calé à OV_HLG_WHITE du signal) avant d'écrire l'image.
# Drapeau de module : positionné par ExportWorker le temps d'un export, lu par
# `_ov_save` dans tous les threads de rendu.
_OV_HLG = False
# Niveau du blanc des incrustations dans le signal HLG. 75 % = blanc de référence
# de la norme, mais à côté des hautes lumières de la vidéo il paraissait gris :
# on le monte (demande de l'utilisateur). Toutes les couleurs suivent.
OV_HLG_WHITE = 0.90


def _ov_save(img, path: str) -> None:
    """Écrit une incrustation RGBA en TGA RLE, adaptée au signal HLG si besoin.

    TGA compressé plutôt que PNG : l'encodage PNG représentait 30 % du temps de
    préparation en 1080p et 56 % en 4K ; TGA RLE est ~2,3× plus rapide, garde
    l'alpha et donne une sortie ffmpeg strictement identique.
    """
    if _OV_HLG:
        img = _ov_hlg(img)
    img.save(path, "TGA", rle=True)


def _ov_hlg(img):
    """Pixels sRGB → signal HLG (cf. _ov_save), alpha inchangé. Séparée pour
    que les animations convertissent leurs sprites UNE fois (cf. intro)."""
    import numpy as np
    a = np.asarray(img.convert("RGBA")).astype(np.float32) / 255.0
    rgb, alpha = a[..., :3], a[..., 3:]
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    import math
    ha, hb, hc = 0.17883277, 0.28466892, 0.55991073
    lin *= (math.exp((OV_HLG_WHITE - hc) / ha) + hb) / 12.0   # blanc sRGB → OV_HLG_WHITE
    m = np.array([[0.6274, 0.3293, 0.0433],
                  [0.0691, 0.9195, 0.0114],
                  [0.0164, 0.0880, 0.8956]], dtype=np.float32)
    e = np.clip(lin @ m.T, 0.0, 1.0)
    ha, hb, hc = 0.17883277, 0.28466892, 0.55991073
    hlg = np.where(e <= 1.0 / 12.0, np.sqrt(3.0 * e),
                   ha * np.log(np.maximum(12.0 * e - hb, 1e-6)) + hc)
    out = np.concatenate([np.clip(hlg, 0, 1), alpha], axis=-1)
    from PIL import Image
    return Image.fromarray((out * 255.0 + 0.5).astype(np.uint8), "RGBA")


def _is_hlg_source(video_path: str) -> bool:
    props = _probe_video_props(video_path) if video_path else {}
    return props.get("color_transfer", "") == "arib-std-b67"


def _text_glyph_mm(draw, cx: float, cy: float, text: str, font, fill) -> None:
    """Texte centré sur la BOÎTE RÉELLE des glyphes (pas sur les métriques de la
    police) : avec DIN Condensed, l'ancre « mm » de PIL pose les chiffres trop bas."""
    l, t, r, b = draw.textbbox((0, 0), text, font=font, anchor="ls")
    draw.text((cx - (l + r) / 2, cy - (t + b) / 2), text, font=font, fill=fill, anchor="ls")


def _text_glyph_lm(draw, x: float, cy: float, text: str, font, fill) -> None:
    """Idem, aligné à gauche et centré verticalement sur les glyphes."""
    l, t, r, b = draw.textbbox((0, 0), text, font=font, anchor="ls")
    draw.text((x - l, cy - (t + b) / 2), text, font=font, fill=fill, anchor="ls")


def _draw_serve_dot(img, cx: float, cy: float, r: float, color) -> None:
    """Pastille de service : disque net anti-aliasé entouré d'un halo court dont
    l'alpha suit un vrai dégradé radial (calculé pixel par pixel, en 4×, puis
    réduit) — aucun anneau visible. `img` est RGBA, on compose dessus."""
    import numpy as np
    from PIL import Image
    SS = 4
    glow = r * 1.7                       # halo court : ~0.7 rayon au-delà du disque
    size = int(math.ceil(glow * 2)) + 4
    n = size * SS
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    dist = np.hypot(xx - (n - 1) / 2, yy - (n - 1) / 2) / SS
    # Disque plein avec bord adouci sur 0.5 px, puis halo en smoothstep vers 0.
    core = np.clip((r + 0.5 - dist) / 1.0, 0.0, 1.0)
    t = np.clip((dist - r) / max(glow - r, 1e-3), 0.0, 1.0)
    halo = (1.0 - t) ** 2 * (3.0 - 2.0 * (1.0 - t))   # smoothstep inversé, tangente nulle aux 2 bouts
    alpha = np.maximum(core, 0.55 * halo)
    layer = np.zeros((n, n, 4), dtype=np.uint8)
    layer[..., 0], layer[..., 1], layer[..., 2] = color
    layer[..., 3] = (alpha * 255.0 + 0.5).astype(np.uint8)
    im = Image.fromarray(layer, "RGBA").resize((size, size), Image.LANCZOS)
    img.alpha_composite(im, (int(round(cx - size / 2)), int(round(cy - size / 2))))


def _ov_tint(f: float, bg_alpha: int = OV_BG_A) -> tuple[int, int, int, int]:
    """Blanc à `f` (0-1) « posé » sur le fond sombre, en couleur opaque équivalente.
    ImageDraw écrit les pixels sans les mélanger : un blanc semi-transparent
    ferait un TROU dans le fond (la vidéo apparaîtrait à travers). On calcule
    donc la couleur résultante nous-mêmes."""
    r, g, b = (int(round(c + (255 - c) * f)) for c in OV_INK)
    return (r, g, b, int(round(bg_alpha + (255 - bg_alpha) * f)))


@lru_cache(maxsize=1)
def _find_label_font() -> str | None:
    """Police des libellés (texte courant) : une grotesque normale, lisible en petit."""
    for p in [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]:
        if os.path.exists(p):
            return p
    return _find_font()


def _fit_text(text: str, font, max_w: int) -> str:
    """Tronque `text` avec « … » pour tenir dans `max_w` pixels."""
    if _text_w(text, font) <= max_w:
        return text
    ell = "…"
    for n in range(len(text) - 1, 0, -1):
        cand = text[:n].rstrip() + ell
        if _text_w(cand, font) <= max_w:
            return cand
    return ell


def _overlay_unit(width: int, height: int) -> float:
    """Unité de mise à l'échelle des incrustations : 1/1000 du PLUS PETIT côté.
    Toutes les cotes sont exprimées en multiples de `u`, jamais en pixels fixes :
    une vignette occupe donc la même part de l'image en 1080p, en 4K, et reste
    de taille raisonnable sur une vidéo verticale."""
    return max(1, min(width, height)) / 1000.0


def _scorecard_margin(width: int, height: int) -> int:
    """Marge de la vignette par rapport au bord de l'image (28 u)."""
    return max(4, int(round(28 * _overlay_unit(width, height))))


def _ov_over(rgb, f: float, bg_alpha: int = SCORECARD_BG_A) -> tuple[int, int, int, int]:
    """Couleur `rgb` à `f` (0-1) posée sur le fond sombre, en couleur opaque
    équivalente (même raison que _ov_tint, pour n'importe quelle teinte)."""
    r, g, b = (int(round(c + (v - c) * f)) for c, v in zip(OV_INK, rgb))
    return (r, g, b, int(round(bg_alpha + (255 - bg_alpha) * f)))


def _make_scorecard_png(
    p1n: str, p2n: str,
    completed: list[tuple[int, int]],
    cur_p1: int, cur_p2: int,
    path: str,
    width: int, height: int,
    serving: int | None = None,
    p1_rank: str = "", p2_rank: str = "",
    last_points: list[int] | None = None,
    blank=None, cells: dict | None = None, img_out: list | None = None,
) -> tuple[int, int]:
    """Vignette de score « en blocs », direction artistique « broadcast TV ».

    Une rangée de blocs détachés, alignés en haut :
      [noms] [set 1] [set 2] … [score courant]
    — noms : deux lignes, bande de couleur du joueur à gauche, pastille dorée
      du serveur, classement en petit sous le nom ;
    — un petit bloc par set TERMINÉ : chiffres d'autant plus éteints que le set
      est ancien, celui du vainqueur plus clair et souligné de sa couleur ;
    — score courant : deux aplats de la couleur des joueurs, chiffres blancs.
    Pas de compteur de sets (« 2-0 ») : les blocs de sets le disent déjà.

    Sous la rangée, un bloc « forme » de même largeur qu'elle : libellé
    « 8 DERNIERS POINTS » à gauche, 8 carrés arrondis à droite (du plus ancien
    au plus récent), pleine couleur du gagnant, gris pâle pour les points pas
    encore joués du set. Toujours affiché, même à 0-0 (8 carrés gris) : la
    hauteur de la vignette ne change pas, rien ne saute à l'écran. Tout est
    dimensionné en `u` (cf. _overlay_unit).
    Retourne (largeur, hauteur) de l'image écrite.

    Pour l'animation des chiffres (cf. export/score_anim) : `blank` = cases dont
    le chiffre n'est PAS dessiné (("cur", i) ou ("past", k, i)), `cells` reçoit
    la géométrie de chaque case (rectangle, centre, police, couleurs) et
    `img_out` l'image sRGB ; `path` à None : rien n'est écrit. Sans ces
    paramètres, rendu strictement inchangé.
    """
    from PIL import Image, ImageDraw

    u = _overlay_unit(width, height) * SCORECARD_SCALE
    def U(v: float) -> int:
        return max(1, int(round(v * u)))

    display = _find_display_font()
    label   = _find_label_font()
    name_font = _load_font(display, U(22))
    rank_font = _load_font(label,   U(11))
    past_font = _load_font(display, U(20))
    cur_font  = _load_font(display, U(30))
    hint_font = _load_font(label,   U(10))

    last_points = list(last_points or [])[-8:]
    past = list(completed)
    n_past = len(past)

    names = (p1n.strip().upper() or "JOUEUR 1", p2n.strip().upper() or "JOUEUR 2")
    ranks = ((p1_rank or "").strip(), (p2_rank or "").strip())

    # Cotes (u). Le classement sous le nom demande une ligne un peu plus haute.
    ROW      = U(44) if any(ranks) else U(40)
    BAND     = U(5)
    PAD      = U(14)
    DOT_R    = U(3.5)          # pastille dorée du serveur, à gauche du nom
    DOT_SLOT = U(16)           # place du point de service, entre le padding et le nom
    GAP      = U(5)            # entre deux blocs
    RADIUS   = U(6)
    SHADOW   = U(1)            # ombre douce vers le bas
    SET_W    = U(30)           # bloc d'un set terminé
    CUR_W    = U(50)           # bloc du score courant
    STRIP_H  = U(28)           # bloc « 8 derniers points »
    SQ, SQ_GAP = U(11), U(4)
    HAIR     = max(1, U(0.8))  # filet entre les deux lignes

    NAME_MAX = U(240)
    name_w = max(_text_w(names[0], name_font), _text_w(names[1], name_font))
    rank_w = max((_text_w(r, rank_font) for r in ranks if r), default=0)
    NAME_W = max(U(60), min(NAME_MAX, max(name_w, rank_w)))
    # Marge droite du bloc des noms : un vrai vide entre la fin du nom (ou du
    # classement) le plus long et les chiffres. 45 u ≈ 65 px en 1080p
    # (demande utilisateur : « entre 50 et 100 px »).
    NAME_PAD_R = U(45)
    x_name = BAND + PAD + DOT_SLOT

    # Bloc « forme » : libellé + 8 carrés ne doivent jamais se chevaucher ; si la
    # rangée est trop étroite (noms courts, pas de set fini), on élargit les noms.
    hint = "8 DERNIERS POINTS"
    squares_w = 8 * SQ + 7 * SQ_GAP
    strip_min = x_name + _text_w(hint, hint_font) + U(24) + squares_w + PAD

    def _row_w(nw: int) -> int:
        return x_name + nw + NAME_PAD_R + GAP + n_past * (SET_W + GAP) + CUR_W

    if _row_w(NAME_W) < strip_min:
        NAME_W += strip_min - _row_w(NAME_W)
    names_w = x_name + NAME_W + NAME_PAD_R

    x_past = names_w + GAP
    x_cur = x_past + n_past * (SET_W + GAP)
    row_h = 2 * ROW
    y_form = row_h + GAP
    total_w = x_cur + CUR_W
    row_w = total_w
    total_h = y_form + STRIP_H + SHADOW
    total_w += total_w % 2           # dimensions paires (sous-échantillonnage 4:2:0)
    total_h += total_h % 2

    img  = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    BG = (*OV_INK, SCORECARD_BG_A)
    colors = (OV_P1, OV_P2)

    def _block(x0, y0, w, h, fill=BG):
        # Ombre d'1 u vers le bas puis l'aplat sombre (~93 %).
        draw.rounded_rectangle([x0, y0 + SHADOW, x0 + w - 1, y0 + h - 1 + SHADOW],
                               radius=RADIUS, fill=(0, 0, 0, 90))
        draw.rounded_rectangle([x0, y0, x0 + w - 1, y0 + h - 1], radius=RADIUS, fill=fill)

    # ── Bloc des noms ────────────────────────────────────────────────────
    _block(0, 0, names_w, row_h)
    x_dot = BAND + PAD + DOT_R + U(1)
    for i in (0, 1):
        y0, y1 = i * ROW, i * ROW + ROW - 1
        cy = y0 + ROW / 2
        # Bande de couleur (coin arrondi côté extérieur seulement). ImageDraw
        # écrit les pixels sans les mélanger : on repeint le fond pour ne garder
        # que BAND pixels de couleur.
        draw.rounded_rectangle([0, y0, BAND * 3, y1], radius=RADIUS, fill=(*colors[i], 255),
                               corners=(i == 0, False, False, i == 1))
        draw.rectangle([BAND, y0, BAND * 3, y1], fill=BG)
        if serving == i + 1:
            _draw_serve_dot(img, x_dot, cy, DOT_R, OV_GOLD)
        nm = _fit_text(names[i], name_font, NAME_W)
        if ranks[i]:
            rk = _fit_text(ranks[i], rank_font, NAME_W)
            _, nt, _, nb = draw.textbbox((0, 0), nm, font=name_font, anchor="ls")
            _, rt, _, rb = draw.textbbox((0, 0), rk, font=rank_font, anchor="ls")
            gap = U(5)
            top = cy - ((nb - nt) + gap + (rb - rt)) / 2
            _text_glyph_lm(draw, x_name, top + (nb - nt) / 2, nm, name_font, (*OV_WHITE, 255))
            _text_glyph_lm(draw, x_name, top + (nb - nt) + gap + (rb - rt) / 2, rk, rank_font,
                           (*OV_MUTED, 255))
        else:
            _text_glyph_lm(draw, x_name, cy, nm, name_font, (*OV_WHITE, 255))
    draw.rectangle([BAND, ROW - 1, names_w - 1, ROW - 2 + HAIR], fill=_ov_over(OV_WHITE, 22 / 255))

    # ── Un bloc par set terminé ──────────────────────────────────────────
    # Plus le set est ancien, plus il s'efface ; le vainqueur reste plus clair.
    for k, (s1, s2) in enumerate(past):
        age = n_past - 1 - k                        # 0 = le plus récent
        win_f = (0.78, 0.55, 0.45)[min(age, 2)]
        lose_f = (0.34, 0.26, 0.22)[min(age, 2)]
        sx = x_past + k * (SET_W + GAP)
        _block(sx, 0, SET_W, row_h)
        draw.rectangle([sx + U(4), ROW - 1, sx + SET_W - 1 - U(4), ROW - 2 + HAIR],
                       fill=_ov_over(OV_WHITE, 18 / 255))
        for i in (0, 1):
            v, won = (s1, s1 > s2) if i == 0 else (s2, s2 > s1)
            y0 = i * ROW
            cy = y0 + ROW / 2 + U(1)
            f = win_f if won else lose_f
            ink = (*(int(round(c + (255 - c) * f)) for c in OV_INK), 255)
            if not blank or ("past", k, i) not in blank:
                _text_glyph_mm(draw, sx + SET_W / 2, cy, str(v), past_font, ink)
            if won:                                 # soulignement dans la couleur du vainqueur
                draw.rounded_rectangle([sx + SET_W / 2 - U(5), cy + U(11),
                                        sx + SET_W / 2 + U(5), cy + U(12.6)],
                                       radius=U(1), fill=(*colors[i], 255))
            if cells is not None:
                # Case = demi-bloc, filet exclu (le masque de l'animation ne
                # retient de toute façon que les pixels de la couleur du fond).
                cells[("past", k, i)] = {
                    "rect": (int(sx), int(y0), int(sx + SET_W - 1), int(y0 + ROW - 2)),
                    "center": (sx + SET_W / 2, cy), "font": past_font, "ink": ink,
                    "fill": BG, "text": str(v)}

    # ── Score courant : deux aplats de couleur, coins arrondis dehors ─────
    cur = (cur_p1, cur_p2)
    draw.rounded_rectangle([x_cur, SHADOW, x_cur + CUR_W - 1, row_h - 1 + SHADOW],
                           radius=RADIUS, fill=(0, 0, 0, 90))
    for i in (0, 1):
        y0, y1 = i * ROW, i * ROW + ROW - 1
        cy = y0 + ROW / 2 + U(1)
        draw.rounded_rectangle([x_cur, y0, x_cur + CUR_W - 1, y1], radius=RADIUS,
                               fill=(*colors[i], 255),
                               corners=(i == 0, i == 0, i == 1, i == 1))
        if not blank or ("cur", i) not in blank:
            _text_glyph_mm(draw, x_cur + CUR_W / 2, cy, str(cur[i]), cur_font, (*OV_WHITE, 255))
        if cells is not None:
            cells[("cur", i)] = {
                "rect": (int(x_cur), int(y0), int(x_cur + CUR_W - 1), int(y1)),
                "center": (x_cur + CUR_W / 2, cy), "font": cur_font,
                "ink": (*OV_WHITE, 255), "fill": (*colors[i], 255), "text": str(cur[i])}

    # ── Forme : 8 derniers points du set, du plus ancien au plus récent ───
    # Bloc de la largeur de la rangée. Cases pas encore jouées : gris pâle.
    _block(0, y_form, row_w, STRIP_H)
    _text_glyph_lm(draw, x_name, y_form + STRIP_H / 2, hint, hint_font, (*OV_FAINT, 255))
    slots = [w if w in (1, 2) else None for w in last_points]
    slots = [None] * (8 - len(slots)) + slots
    x = row_w - PAD - squares_w
    yq = y_form + (STRIP_H - SQ) / 2
    for w in slots:
        fill = (*colors[w - 1], 255) if w else _ov_tint(0.12, SCORECARD_BG_A)
        draw.rounded_rectangle([x, yq, x + SQ - 1, yq + SQ - 1], radius=U(2), fill=fill)
        x += SQ + SQ_GAP

    if cells is not None:
        cells["size"] = (total_w, total_h)
        cells["unit"] = u
    if img_out is not None:
        img_out.append(img)
    if path is not None:
        _ov_save(img, path)
    return total_w, total_h


# Bandeau « balle de set / balle de match » : collé au-dessus de la vignette de
# score (la vignette est ancrée en bas à gauche, il n'y a pas la place dessous).
SETPOINT_GAP = 5     # u, entre le bandeau et la vignette (= écart entre blocs)


def _set_point_state(completed, cur_p1: int, cur_p2: int, match_format: int = 5):
    """Balle de set / de match sur le PROCHAIN point, vu le score courant.

    Sets en 11 points, 2 d'écart : le joueur en tête a une balle de set dès
    qu'il a ≥ 10 points et au moins 1 d'avance (10-8, 10-9, 11-10…) ; à
    égalité (10-10), rien. Si ce set lui donne le match (`match_format` =
    « au meilleur des N sets »), c'est une balle de match.
    Retourne (joueur 1|2, "BALLE DE SET"|"BALLE DE MATCH") ou None."""
    lead = 1 if cur_p1 > cur_p2 else 2 if cur_p2 > cur_p1 else 0
    if not lead or max(cur_p1, cur_p2) < 10:
        return None
    won = sum(1 for s1, s2 in completed if (s1 > s2) == (lead == 1))
    to_win = max(1, int(match_format or 5)) // 2 + 1
    return lead, ("BALLE DE MATCH" if won + 1 >= to_win else "BALLE DE SET")


def _make_setpoint_png(text: str, leader: int, path: str,
                       width: int, height: int) -> tuple[int, int]:
    """Bandeau « BALLE DE SET » / « BALLE DE MATCH », même style que la vignette.

    Même fond sombre (même opacité, même ombre, mêmes coins), même police
    condensée en blanc, et la bande de couleur du joueur qui a la balle à
    gauche, alignée sur celles de la vignette. Coté en `u` (cf. _overlay_unit).
    Retourne (largeur, hauteur) de l'image écrite."""
    from PIL import Image, ImageDraw

    u = _overlay_unit(width, height) * SCORECARD_SCALE
    def U(v: float) -> int:
        return max(1, int(round(v * u)))

    font = _load_font(_find_display_font(), U(19))
    BAND, PAD, H, RADIUS = U(5), U(14), U(30), U(6)
    TRACK = U(1.5)                   # interlettrage : des capitales qui respirent
    glyph_w = [_text_w(c, font) if c != " " else U(6) for c in text]
    text_w = sum(glyph_w) + TRACK * (len(text) - 1)
    total_w = BAND + PAD + text_w + PAD
    total_w += total_w % 2           # largeur paire (sous-échantillonnage 4:2:0)

    img = Image.new("RGBA", (total_w, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, U(1), total_w - 1, H - 1], radius=RADIUS, fill=(0, 0, 0, 90))
    draw.rounded_rectangle([0, 0, total_w - 1, H - 2], radius=RADIUS,
                           fill=(*OV_INK, SCORECARD_BG_A))
    # Bande de couleur du joueur : même astuce que la vignette (coin arrondi
    # extérieur, puis on repeint le fond pour n'en garder que BAND pixels).
    col = OV_P1 if leader == 1 else OV_P2
    draw.rounded_rectangle([0, 0, BAND * 3, H - 2], radius=RADIUS, fill=(*col, 255))
    draw.rectangle([BAND, 0, BAND * 3, H - 2], fill=(*OV_INK, SCORECARD_BG_A))

    # Texte lettre par lettre (interlettrage), centré sur la boîte des glyphes.
    _, t, _, b = draw.textbbox((0, 0), text, font=font, anchor="ls")
    base = (H - 1) / 2 - (t + b) / 2
    x = BAND + PAD
    for c, w in zip(text, glyph_w):
        if c != " ":
            l = draw.textbbox((0, 0), c, font=font, anchor="ls")[0]
            draw.text((x - l, base), c, font=font, fill=(*OV_WHITE, 255), anchor="ls")
        x += w + TRACK

    _ov_save(img, path)
    return total_w, H


@lru_cache(maxsize=512)
def _text_bbox(text: str, font):
    """Mesure (et mémorise) la bbox d'un texte. Avant, chaque mesure recréait une image
    1×1 + un ImageDraw : ~1570 appels pour 93 vignettes alors qu'il n'y a qu'une
    cinquantaine de textes distincts (les 2 noms + les chiffres) → ~97 % de hits."""
    return font.getbbox(text)


def _text_w(text: str, font) -> int:
    bb = _text_bbox(text, font)
    return bb[2] - bb[0]

def _text_h(text: str, font) -> int:
    bb = _text_bbox(text, font)
    return bb[3] - bb[1]
