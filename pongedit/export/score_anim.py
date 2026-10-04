"""Animation « compteur mécanique » des chiffres de la vignette de score.

Quand un point est marqué, le chiffre du joueur ne change plus d'un coup :
il tourne comme une roue, l'ancien part vers le bas, le nouveau arrive du
haut, masqué dans sa case. Quatre variantes (SCORE_ANIM_STYLE) :

  0  pas d'animation (graphe ffmpeg strictement identique à avant) ;
  1  roue simple : ease-out net + micro-rebond à l'arrivée ;
  2  tambour 3D : cylindre vu de face, chiffres comprimés et assombris
     en passant par le haut / le bas de la case ;
  3  machine à sous : quelques chiffres défilent très vite (flou de
     mouvement vertical) puis se calent avec un petit rebond ;
  4  roue + éclat : la 1, plus un bref éclat / halo de la couleur du
     joueur sur sa case au moment où le chiffre se pose.

Deux cas, rendus en séquences d'images RGBA (TGA, converties HLG si besoin
par _ov_save) à la cadence de la vidéo :

  « cell » : un seul chiffre courant change (cas normal). On ne rend que la
    CASE (plus le halo) : un petit patch opaque posé par-dessus la vignette
    fixe du nouveau score, pendant l'animation. Le patch ne dépend que du
    joueur et des deux valeurs : un « 3 → 4 » est rendu une fois pour tout
    le match et réutilisé.
  « card » : fin de set (remise à 0, nouvelle case de set, vignette plus
    large). La vignette entière est rendue pendant l'animation et REMPLACE la
    vignette fixe (fond semi-transparent : la superposer doublerait l'opacité).
    Les deux scores courants tournent vers 0 et le score du set qui vient de
    se terminer arrive du haut dans sa nouvelle case.

Première et dernière images : exactement la vignette d'avant / d'après
(mêmes glyphes, même position), pas de saut au début ni à la fin.
"""

import math, os, threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from pongedit.export import cards as _cards

# Variante utilisée à l'export (0 = pas d'animation). Cf. docstring du module.
SCORE_ANIM_STYLE = 1

# Durées (s) du mouvement du chiffre, par variante. La 4 ajoute la traîne
# de l'éclat (FLASH_TAIL) après la pose.
ROLL_DUR = {1: 0.40, 2: 0.45, 3: 0.52, 4: 0.40}
FLASH_TAIL = 0.26
# Instant (fraction du mouvement) où le chiffre de la variante 4 « se pose »
# (premier passage à sa place avec easeOutBack s=1,4 : u = 1 − s/(s+1) ≈ 0,42) :
# départ de l'éclat.
FLASH_AT = 0.42
SLOT_SPIN = 5          # chiffres intermédiaires de la machine à sous


def _dur(style: int) -> float:
    d = ROLL_DUR.get(style, 0.4)
    if style == 4:
        d = max(d, d * FLASH_AT + FLASH_TAIL)
    return d


def _ease_out_back(u: float, s: float) -> float:
    x = u - 1.0
    return 1.0 + (s + 1.0) * x ** 3 + s * x ** 2


def _travel(style: int, u: float, k: int) -> float:
    """Déplacement du ruban (en nombre de chiffres) à la fraction u du mouvement."""
    u = min(max(u, 0.0), 1.0)
    if style == 2:
        return k * _ease_out_back(u, 0.8)          # rebond ~3 %
    if style == 3:
        # Départ lancé, arrivée encore en mouvement (« clac »), puis un petit
        # rebond sous la position finale, amorti jusqu'au repos.
        u0 = 0.78
        if u < u0:
            v = u / u0
            return k * (0.8 * (1 - (1 - v) ** 2) + 0.2 * v)
        x = (u - u0) / (1 - u0)
        return k + 0.16 * math.sin(math.pi * x) * (1 - x)
    return k * _ease_out_back(u, 1.4)              # 1 et 4 : rebond ~7 %


def _slot_texts(old: str, new: str) -> list[str]:
    """Ruban de la machine à sous : ancien, quelques valeurs, nouveau (déterministe)."""
    import random
    rng = random.Random(f"{old}>{new}")
    hi = max(12, int(new or 0) + 3, int(old or 0) + 3)
    out, prev = [], old
    for _ in range(SLOT_SPIN):
        v = str(rng.randrange(0, hi))
        while v in (prev, new, old):
            v = str(rng.randrange(0, hi))
        out.append(v)
        prev = v
    return [old] + out + [new]


class _Roller:
    """Ruban vertical de valeurs pour UNE case, échantillonné image par image.

    Le ruban est un masque (glyphes blancs) : la valeur k est dessinée `k`
    pas au-dessus de l'ancienne. Déplacer le ruban de `o` pixels vers le bas
    fait descendre l'ancienne valeur et arriver la suivante du haut. Les
    glyphes sont dessinés avec _text_glyph_mm et la même partie décimale de
    position que la vignette fixe : au repos, raster identique.
    """

    def __init__(self, cell: dict, texts: list[str], style: int):
        from PIL import Image, ImageDraw
        x0, y0, x1, y1 = cell["rect"]
        self.w, self.h = x1 - x0 + 1, y1 - y0 + 1
        cx, cy = cell["center"][0] - x0, cell["center"][1] - y0
        self.style = style
        self.R = self.h / 2.0
        # Pas entre deux valeurs : juste hors de la case pour la roue ; pour le
        # tambour, assez grand pour que la voisine reste derrière le cylindre.
        self.pitch = int(round(2.1 * self.R)) if style == 2 else int(round(0.9 * self.h))
        self.k = len(texts) - 1
        self.top = self.k * self.pitch + self.h           # marge au-dessus (entière)
        sh = self.top + 2 * self.h
        m = Image.new("L", (self.w, sh), 0)
        d = ImageDraw.Draw(m)
        for i, t in enumerate(texts):
            if t:
                _cards._text_glyph_mm(d, cx, cy + self.top - i * self.pitch, t,
                                      cell["font"], 255)
        self.strip = np.concatenate([np.asarray(m, np.float32) / 255.0,
                                     np.zeros((1, self.w), np.float32)])
        self.dy = np.arange(self.h, dtype=np.float32) + 0.5 - self.h / 2.0
        yr = np.clip(self.dy / self.R, -1.0, 1.0)
        self.cyl = self.R * np.arcsin(yr)                 # abscisse curviligne
        self.bright = np.sqrt(1.0 - yr ** 2)              # éclairage du cylindre

    def _sample(self, o: float, env: float) -> np.ndarray:
        rows = self.dy + (self.cyl - self.dy) * env if env > 0 else self.dy
        sr = self.top + self.h / 2.0 - 0.5 + rows - o
        i0 = np.floor(sr).astype(np.int64)
        f = (sr - i0)[:, None]
        n = self.strip.shape[0] - 1                      # dernière ligne = zéros
        a = np.where((i0 >= 0) & (i0 < n), i0, n)
        b = np.where((i0 + 1 >= 0) & (i0 + 1 < n), i0 + 1, n)
        return self.strip[a] * (1 - f) + self.strip[b] * f

    def frame(self, u: float, dt_u: float, env: float):
        """(alpha du chiffre, facteur de luminosité par ligne) à la fraction u.

        `dt_u` : durée d'une image en fraction du mouvement (flou de bougé de
        la machine à sous : moyenne de sous-positions sur ~70 % de l'image)."""
        o = _travel(self.style, u, self.k) * self.pitch
        if self.style == 3 and u < 1.0:
            o_prev = _travel(self.style, u - 0.7 * dt_u, self.k) * self.pitch
            n = int(min(16, max(1, math.ceil(abs(o - o_prev) / 1.5))))
            if n > 1:
                acc = sum(self._sample(o_prev + (o - o_prev) * j / (n - 1), 0.0)
                          for j in range(n))
                return acc / n, None
        a = self._sample(o, env)
        if env > 0:
            return a, 1.0 - env * (1.0 - (0.12 + 0.88 * self.bright))
        return a, None


def _f(img) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), np.float32) / 255.0


def _to_img(a: np.ndarray):
    from PIL import Image
    return Image.fromarray((np.clip(a, 0, 1) * 255.0 + 0.5).astype(np.uint8), "RGBA")


def _cell_mask(card: np.ndarray, cell: dict) -> np.ndarray:
    """Pixels de la case (couleur de fond exacte : ImageDraw ne lisse pas)."""
    x0, y0, x1, y1 = cell["rect"]
    reg = card[y0:y1 + 1, x0:x1 + 1]
    fill = np.array(cell["fill"], np.float32) / 255.0
    return np.all(np.abs(reg - fill) < 0.6 / 255.0, axis=-1)


def _paint_cell(out: np.ndarray, ox: int, oy: int, cell: dict, mask: np.ndarray,
                a: np.ndarray, shade, flash: float = 0.0, flash_rgb=None):
    """Compose le chiffre (alpha `a`) dans la case, sur `out` (fond de la case
    déjà en place, décalage (ox, oy) de la case dans `out`), masqué à la case."""
    x0, y0, x1, y1 = cell["rect"]
    sl = (slice(y0 - oy, y1 - oy + 1), slice(x0 - ox, x1 - ox + 1))
    reg = out[sl]
    m = mask[..., None]
    rgb, al = reg[..., :3], reg[..., 3:]
    if flash > 0:
        tgt = np.array(flash_rgb, np.float32) / 255.0
        rgb = np.where(m, rgb + (tgt - rgb) * flash, rgb)
    if shade is not None:
        # Cylindre : le fond de la case s'assombrit aussi vers le haut et le bas.
        bg_sh = 1.0 - (1.0 - shade[:, None, None]) * 0.55
        rgb = np.where(m, rgb * bg_sh, rgb)
    ink = np.array(cell["ink"][:3], np.float32) / 255.0
    ia = (a * (cell["ink"][3] / 255.0))[..., None] * m
    col = ink * (shade[:, None, None] if shade is not None else 1.0)
    reg[..., :3] = rgb * (1 - ia) + col * ia
    reg[..., 3:] = al * (1 - ia) + ia


def _flash_env(t: float, t_land: float) -> float:
    """Éclat : montée en 40 ms à la pose, décroissance douce jusqu'à 0."""
    x = t - t_land
    if x <= 0:
        return 0.0
    if x < 0.04:
        return x / 0.04
    y = (x - 0.04) / max(FLASH_TAIL - 0.04, 1e-3)
    return max(0.0, 1.0 - y) ** 2


def _lighten(rgb, f: float):
    return tuple(int(round(c + (255 - c) * f)) for c in rgb)


def _texts_of(compl, p1, p2) -> dict:
    t = {("cur", 0): str(p1), ("cur", 1): str(p2)}
    for k, (s1, s2) in enumerate(compl):
        t[("past", k, 0)], t[("past", k, 1)] = str(s1), str(s2)
    return t


class _Ctx:
    def __init__(self, p1n, p2n, width, height, p1_rank, p2_rank, fps, tmp_dir, style):
        self.p1n, self.p2n = p1n, p2n
        self.width, self.height = width, height
        self.p1_rank, self.p2_rank = p1_rank, p2_rank
        self.fps, self.tmp_dir, self.style = fps, tmp_dir, style
        self.dur = _dur(style)
        self.n = int(round(self.dur * fps)) + 1           # images, repos compris

    def card(self, spec, blank=None):
        _p, compl, pp1, pp2, srv, lastp = spec
        cells, imgs = {}, []
        _cards._make_scorecard_png(self.p1n, self.p2n, compl, pp1, pp2, None,
                                   self.width, self.height, serving=srv,
                                   p1_rank=self.p1_rank, p2_rank=self.p2_rank,
                                   last_points=lastp, blank=blank, cells=cells,
                                   img_out=imgs)
        return _f(imgs[0]), cells

    def timeline(self):
        """(temps, fraction du mouvement, durée d'une image en fraction) par image."""
        roll = ROLL_DUR.get(self.style, 0.4)
        for n in range(self.n):
            t = n / self.fps
            yield n, t, min(1.0, t / roll), 1.0 / (self.fps * roll)

    def env(self, u: float) -> float:
        # Effet cylindre pendant la rotation seulement : il s'éteint quand le
        # chiffre est posé (~75 % du mouvement), sinon le dégradé traîne.
        if self.style != 2 or not 0 < u < 0.75:
            return 0.0
        return math.sin(math.pi * u / 0.75) ** 0.6

    def texts(self, old: str, new: str) -> list[str]:
        return _slot_texts(old, new) if self.style == 3 else [old, new]

    # ── Cas « cell » : patch d'une seule case ──────────────────────────
    def render_cell(self, spec_new, i: int, old: str, out_dir: str):
        final, cells = self.card(spec_new)
        blank, _ = self.card(spec_new, blank={("cur", i)})
        cell = cells[("cur", i)]
        x0, y0, x1, y1 = cell["rect"]
        g = int(round(14 * cells["unit"])) if self.style == 4 else 0
        # Patch = case (+ halo), origine et taille PAIRES (sous-échantillonnage
        # 4:2:0 aligné sur celui de la vignette).
        px0, py0 = (x0 - g) // 2 * 2, (y0 - g) // 2 * 2
        px1, py1 = x1 + g + 1, y1 + g + 1
        pw, ph = (px1 - px0 + 1) // 2 * 2, (py1 - py0 + 1) // 2 * 2
        H, W = final.shape[:2]

        def crop(img):
            out = np.zeros((ph, pw, 4), np.float32)
            sx0, sy0 = max(px0, 0), max(py0, 0)
            sx1, sy1 = min(px0 + pw, W), min(py0 + ph, H)
            out[sy0 - py0:sy1 - py0, sx0 - px0:sx1 - px0] = img[sy0:sy1, sx0:sx1]
            return out

        mask = _cell_mask(blank, cell)
        full_mask = np.zeros((ph, pw), bool)
        full_mask[y0 - py0:y1 - py0 + 1, x0 - px0:x1 - px0 + 1] = mask
        # Hors de la case : transparent (la vignette fixe dessous est la bonne).
        base = crop(blank) * full_mask[..., None]
        last = crop(final) * full_mask[..., None]

        halo = None
        if g:
            # Distance au rectangle de la case (0 dedans), pour le halo extérieur.
            yy, xx = np.mgrid[0:ph, 0:pw].astype(np.float32)
            ddx = np.maximum(np.maximum((x0 - px0) - xx, xx - (x1 - px0)), 0)
            ddy = np.maximum(np.maximum((y0 - py0) - yy, yy - (y1 - py0)), 0)
            d = np.hypot(ddx, ddy)
            halo = np.clip(1.0 - d / g, 0, 1) ** 2 * (~full_mask)
            halo_rgb = np.array(_lighten(cell["fill"][:3], 0.35), np.float32) / 255.0

        roller = _Roller(cell, self.texts(old, cell["text"]), self.style)
        roll = ROLL_DUR.get(self.style, 0.4)
        os.makedirs(out_dir, exist_ok=True)
        for n, t, u, dtu in self.timeline():
            if n == self.n - 1:
                fr = last
            else:
                fr = base.copy()
                a, shade = roller.frame(u, dtu, self.env(u))
                fl = _flash_env(t, FLASH_AT * roll) if self.style == 4 else 0.0
                _paint_cell(fr, px0, py0, cell, mask, a, shade, 0.45 * fl,
                            _lighten(cell["fill"][:3], 0.55))
                if halo is not None and fl > 0:
                    ha = (halo * 0.8 * fl)[..., None]
                    fr[..., :3] = np.where(ha > 0, halo_rgb, fr[..., :3])
                    fr[..., 3:] = np.maximum(fr[..., 3:], ha)
            _cards._ov_save(_to_img(fr), os.path.join(out_dir, f"f_{n:03d}.tga"))
        return {"kind": "cell", "pattern": os.path.join(out_dir, "f_%03d.tga"),
                "frames": self.n, "x": px0, "y": py0, "card_h": H}

    # ── Cas « card » : vignette entière (fin de set) ───────────────────
    def render_card(self, spec_old, spec_new, out_dir: str):
        _p, compl_o, o1, o2, _s, _l = spec_old
        old_t = _texts_of(compl_o, o1, o2)
        final, cells = self.card(spec_new)
        changed = [k for k in cells if k not in ("size", "unit")
                   and old_t.get(k, "") != cells[k]["text"]]
        blank, _ = self.card(spec_new, blank=set(changed))
        H = final.shape[0]
        rollers = [(k, cells[k], _cell_mask(blank, cells[k]),
                    _Roller(cells[k], self.texts(old_t.get(k, ""), cells[k]["text"]),
                            self.style))
                   for k in changed]
        # Variante 4 : l'éclat va sur la nouvelle case de set du vainqueur.
        win = None
        _pn, compl_n = spec_new[0], spec_new[1]
        if compl_n and len(compl_n) > len(compl_o):
            s1, s2 = compl_n[-1]
            win = 0 if s1 > s2 else 1
        roll = ROLL_DUR.get(self.style, 0.4)
        os.makedirs(out_dir, exist_ok=True)
        for n, t, u, dtu in self.timeline():
            if n == self.n - 1:
                fr = final
            else:
                fr = blank.copy()
                fl = _flash_env(t, FLASH_AT * roll) if self.style == 4 else 0.0
                for k, cell, mask, ro in rollers:
                    a, shade = ro.frame(u, dtu, self.env(u))
                    hot = fl if (k[0] == "past" and k[2] == win) else 0.0
                    rgb = (_cards.OV_P1, _cards.OV_P2)[k[-1]]
                    _paint_cell(fr, 0, 0, cell, mask, a, shade, 0.7 * hot, rgb)
            _cards._ov_save(_to_img(fr), os.path.join(out_dir, f"f_{n:03d}.tga"))
        return {"kind": "card", "pattern": os.path.join(out_dir, "f_%03d.tga"),
                "frames": self.n, "x": 0, "y": 0, "card_h": H}


def render_score_anims(jobs, p1n, p2n, width, height, p1_rank, p2_rank, fps,
                       tmp_dir, style=None) -> dict:
    """Rend les animations des changements de score.

    `jobs` : liste de dicts {pi, t0, t_end, old, new} (old/new = specs de
    vignette de _build_filter). Retourne {pi: info} avec info = kind, pattern,
    frames, x/y (position du patch dans la vignette), card_h, t0, t_b (fin de
    l'animation : instant où la vignette fixe reprend la main si « card »).
    Les patches « cell » identiques (même joueur, mêmes valeurs, même parité
    de position) ne sont rendus qu'une fois.
    """
    style = SCORE_ANIM_STYLE if style is None else style
    if not style or not jobs or fps <= 0:
        return {}
    ctx = _Ctx(p1n, p2n, width, height, p1_rank, p2_rank, fps, tmp_dir, style)
    span = (ctx.n - 0.5) / fps
    plan, uniq = {}, {}
    for j in jobs:
        if j["t_end"] - j["t0"] < span + 0.5 / fps:
            continue                     # point suivant trop proche : pas d'anim
        _p, co, o1, o2, _s, _l = j["old"]
        _p, cn, n1, n2, _s, _l = j["new"]
        old_t, new_t = _texts_of(co, o1, o2), _texts_of(cn, n1, n2)
        if old_t == new_t:
            continue
        diff = [k for k in new_t if old_t.get(k) != new_t[k]]
        if len(co) == len(cn) and len(diff) == 1 and diff[0][0] == "cur":
            i = diff[0][1]
            # La position de la case dépend de la largeur de la vignette : sa
            # parité fait partie de la clé (alignement 4:2:0).
            key = ("cell", i, old_t[diff[0]], new_t[diff[0]], len(cn))
        else:
            key = ("card", j["pi"])
        plan[j["pi"]] = (key, j)
        uniq.setdefault(key, j)

    results, lock = {}, threading.Lock()

    def _job(item):
        key, j = item
        d = os.path.join(tmp_dir, "score_anim", "_".join(str(x) for x in key))
        if key[0] == "cell":
            r = ctx.render_cell(j["new"], key[1], key[2], d)
        else:
            r = ctx.render_card(j["old"], j["new"], d)
        with lock:
            results[key] = r

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_job, uniq.items()))

    out = {}
    for pi, (key, j) in plan.items():
        r = dict(results[key])
        # Relais à l'instant de la DERNIÈRE image de la séquence (= la vignette
        # fixe) : au-delà, overlay (eof_action=pass) ne pose plus rien, et une
        # fin de séquence plus tardive laissait une image SANS vignette en fin
        # de set (« card »), selon la phase des images de la vidéo.
        r["t0"], r["t_b"] = j["t0"], j["t0"] + (ctx.n - 1) / fps
        out[pi] = r
    return out
