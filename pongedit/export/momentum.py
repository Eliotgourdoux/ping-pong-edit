"""Dynamique du match (« momentum ») pour le tableau de fin.

Une courbe par set : l'écart de points AU SEIN du set (au-dessus de zéro =
joueur 1 devant, en dessous = joueur 2), repartant de 0 à chaque set. On a
préféré ce découpage à un écart cumulé sur tout le match : les sets sont
indépendants (11 points chacun), le score en sets est déjà en tête du tableau,
et un écart cumulé ferait démarrer le set 2 à +7 alors qu'il commence à 0-0,
masquant précisément les remontées qu'on veut voir.

Le tracé est une interpolation monotone (Hermite, pentes de Fritsch-Carlson) :
elle passe par chaque point, ne dépasse jamais (pas de faux écart), s'arrondit
aux changements de tendance et reste une droite dans les séries. Rendu
sur-échantillonné (SS×) en masques numpy, puis composé en RGBA : la même
fonction sert à l'image fixe et à l'animation (tracé révélé de gauche à droite,
cf. _render_momentum_anim), dont la dernière image est la version fixe.
"""

import os, math

MOMENTUM_DUR = 2.4       # s : durée du tracé animé, après le fondu du tableau
SS = 3                   # sur-échantillonnage des masques (anti-crénelage)
GAP = 1.6                # espace entre deux sets, en « points » de l'axe X
ANIM_PATTERN = "mom_%05d.tga"


def _momentum_series(points_seq: list[int], sets: list[dict]) -> list[list[int]]:
    """Écart J1 − J2 après chaque point, set par set (chaque liste commence à 0).

    Découpe selon le nombre de points de chaque set de `stats["sets"]` (même
    règle, même ordre que _compute_stats_from_dicts) ; à défaut, 11 pts / 2 d'écart.
    """
    seq = [1 if p == 1 else 2 for p in (points_seq or [])]
    sizes = [int(sum(sd.get("score", (0, 0)))) for sd in (sets or [])]
    if not sizes or sum(sizes) != len(seq):
        sizes, a, b, n = [], 0, 0, 0
        for p in seq:
            n += 1
            a, b = a + (p == 1), b + (p == 2)
            if max(a, b) >= 11 and abs(a - b) >= 2:
                sizes.append(n); a = b = n = 0
        if n:
            sizes.append(n)
    out, i = [], 0
    for n in sizes:
        d, cur = 0, [0]
        for p in seq[i:i + n]:
            d += 1 if p == 1 else -1
            cur.append(d)
        out.append(cur)
        i += n
    return [s for s in out if len(s) > 1]


def _momentum_range(series: list[list[int]]) -> tuple[int, int]:
    """(plus grande avance de J1, plus grande avance de J2), au moins 2 chacune."""
    top = max([max(s) for s in series] + [0])
    bot = max([-min(s) for s in series] + [0])
    return max(2, top), max(2, bot)


def _momentum_x(series: list[list[int]], w: float) -> list[tuple[float, float]]:
    """Bornes horizontales (px) de chaque set dans une zone de largeur `w`."""
    total = sum(len(s) - 1 for s in series) + GAP * (len(series) - 1)
    k = w / max(total, 1e-6)
    out, x = [], 0.0
    for s in series:
        out.append((x * k, (x + len(s) - 1) * k))
        x += len(s) - 1 + GAP
    return out


def _pchip(ys: list[int], n_samp: int):
    """Interpolation monotone de `ys` (abscisses 0..n-1) sur `n_samp` points."""
    import numpy as np
    y = np.asarray(ys, dtype=np.float64)
    n = len(y)
    d = np.diff(y)
    m = np.zeros(n)
    m[0], m[-1] = d[0], d[-1]
    for k in range(1, n - 1):
        m[k] = 0.0 if d[k - 1] * d[k] <= 0 else 2 * d[k - 1] * d[k] / (d[k - 1] + d[k])
    t = np.linspace(0.0, n - 1, n_samp)
    k = np.minimum(np.floor(t).astype(int), n - 2)
    s = t - k
    h00, h10 = 2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s
    h01, h11 = -2 * s**3 + 3 * s**2, s**3 - s**2
    return t, h00 * y[k] + h10 * m[k] + h01 * y[k + 1] + h11 * m[k + 1]


def _momentum_masks(series: list[list[int]], w: int, h: int, u: float,
                    pad_x: float, pad_y: float) -> dict:
    """Masques pleine résolution du graphique dans un rectangle w × h (px).

    La zone tracée est le rectangle intérieur (marges `pad_x`, `pad_y` : place
    pour l'épaisseur du trait et le halo de la tête pendant l'animation).
    Retourne les alphas (float32 0-1) : `grid` (zéro + séparateurs), `fill`
    (aplats en dégradé), `line` (courbe) ; `up` = pixel au-dessus du zéro
    (couleur J1) ; `head` = abscisses/ordonnées (px) de la courbe, pour la tête.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    top, bot = _momentum_range(series)
    pw, ph = w - 2 * pad_x, h - 2 * pad_y
    y0 = pad_y + ph * top / (top + bot)          # ligne du zéro
    unit = ph / (top + bot)                      # px par point d'écart
    Ws, Hs = w * SS, h * SS

    def U(v):
        return max(1, int(round(v * u)))

    # Courbe échantillonnée, set par set.
    xs_all, ys_all = [], []
    curves = []
    for (xa, xb), s in zip(_momentum_x(series, pw), series):
        n_samp = max(8, int((xb - xa) * SS) + 1)
        t, v = _pchip(s, n_samp)
        X = pad_x + xa + (xb - xa) * t / max(len(s) - 1, 1)
        Y = y0 - v * unit
        curves.append((X, Y))
        xs_all.append(X); ys_all.append(Y)

    # Aplats : entre la courbe et le zéro, alpha croissant avec l'écart.
    Yg = (np.arange(Hs, dtype=np.float32) + 0.5) / SS
    fill = np.zeros((Hs, Ws), dtype=np.float32)
    for X, Y in curves:
        c0, c1 = int(math.ceil(X[0] * SS)), int(math.floor(X[-1] * SS))
        if c1 <= c0:
            continue
        cx = (np.arange(c0, c1, dtype=np.float32) + 0.5) / SS
        yc = np.interp(cx, X, Y).astype(np.float32)
        lo, hi = np.minimum(yc, y0), np.maximum(yc, y0)
        inside = (Yg[:, None] >= lo[None, :]) & (Yg[:, None] <= hi[None, :])
        depth = np.abs(Yg[:, None] - y0) / max(unit * max(top, bot), 1.0)
        fill[:, c0:c1] = np.where(inside, 0.10 + 0.42 * np.clip(depth, 0, 1), 0.0)

    # Trait : dessiné à SS× puis réduit.
    lw = max(1.6, 2.2 * u)
    line_im = Image.new("L", (Ws, Hs), 0)
    dl = ImageDraw.Draw(line_im)
    for X, Y in curves:
        pts = list(zip((X * SS).tolist(), (Y * SS).tolist()))
        dl.line(pts, fill=255, width=max(1, int(round(lw * SS))), joint="curve")
        r = lw * SS / 2
        for px, py in (pts[0], pts[-1]):          # bouts arrondis
            dl.ellipse([px - r, py - r, px + r, py + r], fill=255)

    # Zéro (fin, continu) + séparateurs de sets (pointillés).
    grid_im = Image.new("L", (Ws, Hs), 0)
    dg = ImageDraw.Draw(grid_im)
    zy = y0 * SS
    dg.line([(pad_x * SS, zy), ((w - pad_x) * SS, zy)], fill=int(255 * 0.30),
            width=max(1, int(round(1.0 * u * SS))))
    bounds = _momentum_x(series, pw)
    for (xa, xb), (xc, _) in zip(bounds, bounds[1:]):
        X = (pad_x + (xb + xc) / 2) * SS
        yy = pad_y * SS
        while yy < (h - pad_y) * SS:
            dg.line([(X, yy), (X, min(yy + U(4) * SS, (h - pad_y) * SS))],
                    fill=int(255 * 0.22), width=max(1, int(round(1.0 * u * SS))))
            yy += U(8) * SS

    def down(a):
        return a.reshape(h, SS, w, SS).mean(axis=(1, 3)).astype(np.float32)

    up = np.broadcast_to((np.arange(h)[:, None] + 0.5) <= y0, (h, w))
    return {
        "w": w, "h": h, "y0": y0, "lw": lw, "u": u,
        "grid": down(np.asarray(grid_im, dtype=np.float32) / 255.0),
        "fill": down(fill),
        "line": down(np.asarray(line_im, dtype=np.float32) / 255.0),
        "up": up,
        "head": (np.concatenate(xs_all), np.concatenate(ys_all)),
    }


def _lighter(c, f=0.22):
    return tuple(int(round(a + (255 - a) * f)) for a in c)


def _momentum_compose(m: dict, p1_col, p2_col, reveal: float = 1.0, bg=None):
    """Image RGBA du graphique. `reveal` (0-1) : part tracée, de gauche à droite.
    `bg` : couleur RGBA de fond (sinon transparent, à composer sur la carte)."""
    import numpy as np
    from PIL import Image
    w, h = m["w"], m["h"]
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    a = np.zeros((h, w), dtype=np.float32)
    if bg is not None:
        rgb[...] = np.asarray(bg[:3], dtype=np.float32) / 255.0
        a[...] = bg[3] / 255.0

    def over(col, alpha):
        """Compose une couleur unie (ou par pixel) d'alpha `alpha` par-dessus."""
        nonlocal rgb, a
        col = np.asarray(col, dtype=np.float32) / 255.0
        out_a = alpha + a * (1.0 - alpha)
        num = col * alpha[..., None] + rgb * (a * (1.0 - alpha))[..., None]
        rgb = np.where(out_a[..., None] > 0, num / np.maximum(out_a, 1e-6)[..., None], 0.0)
        a = out_a

    up = m["up"][..., None]
    c_fill = np.where(up, np.asarray(p1_col, np.float32), np.asarray(p2_col, np.float32))
    c_line = np.where(up, np.asarray(_lighter(p1_col), np.float32),
                      np.asarray(_lighter(p2_col), np.float32))
    over((235, 238, 245), m["grid"])

    hx, hy = m["head"]
    x_cut = None
    if reveal < 1.0:
        x_cut = hx.min() + (hx.max() - hx.min()) * max(0.0, reveal)
        cols = np.arange(w, dtype=np.float32) + 0.5
        mask = np.clip(x_cut - cols + 0.5, 0.0, 1.0)[None, :]
    else:
        mask = 1.0
    over(c_fill, m["fill"] * mask)
    over(c_line, m["line"] * mask)

    if x_cut is not None and 0.0 < reveal:
        # Tête du tracé : petit point blanc et halo de la couleur du meneur.
        i = int(np.clip(np.searchsorted(hx, x_cut), 0, len(hx) - 1))
        cx, cy = float(hx[i]), float(hy[i])
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dist = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy)
        col = p1_col if cy <= m["y0"] else p2_col
        R = 5.5 * m["lw"]
        over(col, 0.55 * np.clip(1.0 - dist / R, 0.0, 1.0) ** 1.6)
        rc = 1.5 * m["lw"]
        over((245, 247, 252), np.clip(rc + 0.5 - dist, 0.0, 1.0))

    out = np.concatenate([rgb, a[..., None]], axis=-1)
    return Image.fromarray((np.clip(out, 0, 1) * 255.0 + 0.5).astype(np.uint8), "RGBA")


def _render_momentum_anim(anim: dict, out_dir: str, fps: float, lead: float,
                          dur: float = MOMENTUM_DUR, workers: int | None = None) -> tuple[str, int]:
    """Séquence TGA du tracé (tableau fixe percé d'un trou, rectangle animé par-dessus).

    `anim` : dict rempli par _make_stats_card_png (clé « momentum »). Les
    images avant le début (zéro + séparateurs seuls) et après la fin (= la
    découpe de la carte fixe, identité garantie) sont des liens durs.
    """
    from concurrent.futures import ThreadPoolExecutor
    from pongedit.export import cards as _cards
    m = anim["masks"]
    n_frames = int(math.ceil((lead + dur) * fps)) + 1
    keys, todo = [], {}
    for f in range(n_frames):
        a = f / fps - lead
        key = ("start",) if a <= 0 else ("final",) if a >= dur else ("f", f)
        keys.append(key)
        # Tracé à vitesse constante (linéaire).
        x = min(1.0, max(0.0, a / dur))
        todo.setdefault(key, x)

    order = {k: j for j, k in enumerate(todo)}

    def job(item):
        key, rv = item
        p = os.path.join(out_dir, f"mom_u{order[key]:05d}.tga")
        img = anim["final"] if key == ("final",) else _momentum_compose(
            m, _cards.OV_P1, _cards.OV_P2, reveal=rv, bg=anim["bg"])
        _cards._ov_save(img, p)
        return key, p

    paths = {}
    with ThreadPoolExecutor(max_workers=workers or min(8, os.cpu_count() or 4)) as ex:
        for key, p in ex.map(job, todo.items()):
            paths[key] = p
    for f, key in enumerate(keys):
        dst = os.path.join(out_dir, ANIM_PATTERN % f)
        try:
            os.link(paths[key], dst)
        except OSError:
            import shutil
            shutil.copyfile(paths[key], dst)
    return os.path.join(out_dir, ANIM_PATTERN), n_frames
