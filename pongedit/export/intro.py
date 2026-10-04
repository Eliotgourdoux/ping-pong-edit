"""Carte d'intro « duel » : présentation des deux joueurs en début de montage.

Deux encarts (un par joueur) arrivent de l'extérieur de l'image vers le
centre, se posent avec un léger rebond ; à l'impact, un « VS » claque au
centre, avec un flash bref, une onde de choc et une petite secousse. Un
bandeau commun (occasion · lieu · date) se glisse ensuite dessous. L'intro
tient, puis les encarts repartent vers l'extérieur au moment où commence le
passage du 1er point (cf. intro_exit_time).

Même direction artistique que la vignette de score et le tableau de fin
(cf. cards) : mêmes polices, fond encre, couleurs joueurs, arrondis, cotes en
fraction du plus petit côté (`u`, cf. _overlay_unit). La même fonction de
mise en page sait rendre le 16:9 (encarts côte à côte) et le 9:16 (encarts
empilés, pour une future version TikTok).

Rendu : seule une bande pleine largeur autour du groupe est dessinée
(pas une image plein écran), en séquence TGA à la cadence de la vidéo,
superposée par ffmpeg. Les images de la tenue, identiques, sont des liens
durs vers une seule image : seules l'entrée (~1 s) et la sortie (0,4 s) sont
réellement rendues, en parallèle.
"""

import os, math, datetime, subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from pongedit.export import cards as _cards

# ── Chronologie (s, depuis le début de la vidéo montée) ──────────────────────
T_HIT      = 0.34    # les encarts touchent le centre : impact
SETTLE     = 0.26    # rebond puis repos après l'impact (entrée ≈ 0,6 s)
VS_POP     = 0.20    # le « VS » claque (échelle 1,65 → 1)
FLASH_DUR  = 0.24
RING_DUR   = 0.55
SHAKE_DUR  = 0.30
BAND_DELAY = 0.14    # le bandeau suit l'impact
BAND_DUR   = 0.32
EXIT_DUR   = 0.45    # retrait des encarts = re-zoom de la vidéo, qui démarre
INTRO_DUR  = 3.6     # s : durée de l'image figée ajoutée avant le montage
ZOOM_MIN   = 0.85    # taille de la vidéo réduite pendant l'intro (dézoom de 15 %)
BG_BLUR    = 0       # flou du fond (sigma, sur une image de 480 px de large) ; 0 = pas de flou
BG_DIM     = 0.82    # luminosité du fond flouté (1 = inchangée)
ZOOM_IN    = INTRO_DUR  # s : dézoom lent 1 → ZOOM_MIN au début (ease-in-out sinusoïdal) ;
                     #     >= INTRO_DUR = sur toute la tenue ; 0 = vidéo déjà dézoomée
REZOOM_DUR = 0.70    # s : re-zoom ZOOM_MIN → 1 (ease-in-out sinusoïdal), dès le retrait
                     #     des encarts (EXIT_DUR, inchangé) ; vitesse nulle aux jonctions
ANIM_PATTERN = "intro_%05d.tga"
FLASH_COL  = (255, 251, 244)
RING_COL   = (240, 243, 250)

_BG   = (8, 10, 16)
_BG_A = 238          # fond des encarts un peu plus dense que le tableau de fin

FIELDS = ("p1_first", "p1_last", "p1_rank", "p1_club",
          "p2_first", "p2_last", "p2_rank", "p2_club",
          "occasion", "lieu", "date")

OCCASION_SUGGESTIONS = ["Tournoi", "Championnat", "Championnat par équipes",
                        "Critérium fédéral", "Coupe", "Match amical", "Entraînement"]

_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
         "août", "septembre", "octobre", "novembre", "décembre")


# ── Données ──────────────────────────────────────────────────────────────────

def _clean(s) -> str:
    return " ".join(str(s or "").split())


def split_name(full: str) -> tuple[str, str]:
    """« Eliot Gourdoux » → (« Eliot », « Gourdoux ») : le 1er mot est le prénom."""
    parts = _clean(full).split(" ")
    if not parts or not parts[0]:
        return "", ""
    return parts[0], " ".join(parts[1:])


def format_date_fr(d: datetime.date) -> str:
    return f"{d.day} {_MOIS[d.month - 1]} {d.year}"


def video_date(path: str) -> str:
    """Date de tournage (creation_time via ffprobe, heure locale), sinon mtime."""
    if not path:
        return ""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=10)
        s = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        if s:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            if dt.year > 1971:
                return format_date_fr(dt.date())
    except Exception:
        pass
    try:
        return format_date_fr(datetime.date.fromtimestamp(os.path.getmtime(path)))
    except OSError:
        return ""


def auto_values(p1n: str, p2n: str, p1_rank: str, p2_rank: str,
                memory: dict | None = None, date: str = "") -> dict:
    """Préremplissage : tableau de score (nom découpé, classement), mémoire de
    l'app (club par joueur, dernière occasion) et date de la vidéo."""
    memory = memory or {}
    clubs = memory.get("clubs") or {}
    out = {}
    for k, full, rank in (("p1", p1n, p1_rank), ("p2", p2n, p2_rank)):
        first, last = split_name(full)
        out[f"{k}_first"], out[f"{k}_last"] = first, last
        out[f"{k}_rank"] = _clean(rank)
        out[f"{k}_club"] = _clean(clubs.get(_clean(full).lower(), ""))
    out["occasion"] = _clean(memory.get("occasion", ""))
    out["lieu"] = ""
    out["date"] = _clean(date)
    return out


def effective_values(saved: dict | None, auto: dict) -> dict:
    """Valeurs retenues : celles du dialogue priment QUAND l'utilisateur les a
    modifiées (différentes du préremplissage de l'époque) ; les autres suivent
    le préremplissage actuel (nom corrigé dans le tableau de score, etc.)."""
    saved = saved or {}
    vals, snap = saved.get("values") or {}, saved.get("auto") or {}
    out = {}
    for k in FIELDS:
        if k in vals and _clean(vals[k]) != _clean(snap.get(k, "")):
            out[k] = _clean(vals[k])
        else:
            out[k] = _clean(auto.get(k, ""))
    return out


def intro_lead(fps: float, dur: float = INTRO_DUR) -> tuple[int, float]:
    """(nombre d'images figées, durée exacte D) : D est un multiple exact de
    la période image, pour que tout le montage soit décalé d'images entières."""
    n = max(1, int(round(dur * fps)))
    return n, n / fps


def _zoom_in_dur(lead: float) -> float:
    """Durée effective du dézoom d'entrée (0 = déjà dézoomée)."""
    if ZOOM_IN <= 0:
        return 0.0
    return lead if ZOOM_IN >= INTRO_DUR - 1e-9 else min(ZOOM_IN, lead)


def zoom_at(t: float, lead: float) -> float:
    """Échelle de la vidéo à l'instant `t` (même courbe que zoom_expr).

    Dézoom 1 → ZOOM_MIN en ease-in-out sinusoïdal sur _zoom_in_dur (vitesse
    nulle au départ et à l'arrivée), tenue éventuelle, puis re-zoom
    ZOOM_MIN → 1 en ease-in-out sinusoïdal sur REZOOM_DUR à partir de `lead`.
    La vitesse est continue partout (nulle aux jonctions) : un « V » arrondi.
    """
    zi, a = _zoom_in_dur(lead), 1.0 - ZOOM_MIN
    if zi > 0 and t < zi:
        return 1.0 - a * (1.0 - math.cos(math.pi * t / zi)) / 2
    if t < lead:
        return ZOOM_MIN
    if t < lead + REZOOM_DUR:
        return ZOOM_MIN + a * (1.0 - math.cos(math.pi * (t - lead) / REZOOM_DUR)) / 2
    return 1.0


def zoom_expr(lead: float, tv: str = "t") -> str:
    """Expression ffmpeg (virgules échappées) de zoom_at, en fonction de la
    variable temps `tv` (ex. "(n/59.94)"). Vaut 1 pile après lead + REZOOM_DUR."""
    zi, zm, r = _zoom_in_dur(lead), ZOOM_MIN, REZOOM_DUR
    a = 1.0 - zm
    rest = (f"if(lt({tv}\\,{lead!r})\\,{zm!r}\\,"
            f"if(lt({tv}\\,{lead + r!r})\\,"
            f"{zm!r}+{a!r}*(1-cos(PI*({tv}-{lead!r})/{r!r}))/2\\,1))")
    if zi <= 0:                        # vidéo déjà dézoomée dès la 1re image
        return rest
    return (f"if(lt({tv}\\,{zi!r})\\,1-{a!r}*(1-cos(PI*{tv}/{zi!r}))/2\\,{rest})")


# ── Polices et texte ─────────────────────────────────────────────────────────

@lru_cache(maxsize=128)
def _hn(size: int, index: int):
    """Helvetica Neue, graisse choisie (0 regular, 1 bold, 10 medium)."""
    from PIL import ImageFont
    p = _cards._find_label_font()
    try:
        if p and p.endswith(".ttc"):
            return ImageFont.truetype(p, size, index=index)
    except Exception:
        pass
    return _cards._load_font(p, size)


def _disp(size: int):
    return _cards._load_font(_cards._find_display_font(), size)


def _tw(text: str, font, track: float = 0.0) -> float:
    """Largeur d'un texte, avec un espacement de lettres `track` (px)."""
    if not text:
        return 0.0
    if not track:
        return font.getlength(text)
    return sum(font.getlength(c) for c in text) + track * (len(text) - 1)


def _cap(font) -> int:
    """Hauteur de capitale (px) : sert à poser les lignes sur leurs lignes de base."""
    b = font.getbbox("H", anchor="ls")
    return -b[1]


def _text(d, x: float, y: float, text: str, font, fill, track: float = 0.0,
          align: str = "l") -> None:
    """Texte posé sur la ligne de base `y`, aligné à gauche (`l`), à droite
    (`r`) ou centré (`m`) sur `x`, avec espacement de lettres optionnel."""
    w = _tw(text, font, track)
    x = x if align == "l" else x - w if align == "r" else x - w / 2
    if not track:
        d.text((x, y), text, font=font, fill=fill, anchor="ls")
        return
    for c in text:
        d.text((x, y), c, font=font, fill=fill, anchor="ls")
        x += font.getlength(c) + track


def _fit(text: str, font, max_w: float, track: float = 0.0) -> str:
    if _tw(text, font, track) <= max_w:
        return text
    for n in range(len(text) - 1, 0, -1):
        c = text[:n].rstrip() + "…"
        if _tw(c, font, track) <= max_w:
            return c
    return "…"


def _lighter(c, f=0.35):
    return tuple(int(round(a + (255 - a) * f)) for a in c)


# ── Sprites (dessinés une fois) ──────────────────────────────────────────────

def _player_lines(v: dict, k: str):
    first, last = _clean(v.get(f"{k}_first")), _clean(v.get(f"{k}_last"))
    rank, club = _clean(v.get(f"{k}_rank")), _clean(v.get(f"{k}_club"))
    if not first and not last:
        first = "Joueur 1" if k == "p1" else "Joueur 2"
    big = (last or first).upper()
    small = first.upper() if last else ""
    return small, big, rank, club


def _panel_fonts(U):
    return dict(small=_hn(U(23), 1), big=_disp(U(86)), rank=_hn(U(22), 10),
                club=_hn(U(22), 0), track=U(3.2))


def _panel_measure(v: dict, k: str, U, big_size: int | None = None):
    """(largeur du texte, hauteur du bloc) du contenu d'un encart."""
    f = _panel_fonts(U)
    small, big, rank, club = _player_lines(v, k)
    fb = _disp(big_size) if big_size else f["big"]
    w = _tw(big, fb)
    if small:
        w = max(w, _tw(small, f["small"], f["track"]))
    meta_w = _tw(rank, f["rank"]) + (U(34) if rank and club else 0) + _tw(club, f["club"])
    w = max(w, meta_w)
    h = _cap(fb)
    if small:
        h += _cap(f["small"]) + U(16)
    if rank or club:
        h += U(22) + _cap(f["rank"])
    return w, h


def _panel_sprite(v: dict, k: str, side: str, pw: int, ph: int, U, big_size: int,
                  inner_pad: int):
    """Encart d'un joueur : fond encre, bande de couleur côté extérieur, léger
    dégradé de la couleur du joueur, prénom (petit, espacé) / NOM (grand,
    condensé) / classement · club. `side` : « l » (texte à gauche, bande à
    gauche) ou « r » (miroir). `inner_pad` : marge côté intérieur (VS)."""
    import numpy as np
    from PIL import Image, ImageDraw

    col = _cards.OV_P1 if k == "p1" else _cards.OV_P2
    f = _panel_fonts(U)
    fb = _disp(big_size)
    small, big, rank, club = _player_lines(v, k)
    R = U(14)
    band = U(6)

    # Fond : masque arrondi, dégradé horizontal de la couleur du joueur.
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=R, fill=255)
    m = np.asarray(mask, dtype=np.float32) / 255.0
    xs = np.arange(pw, dtype=np.float32) + 0.5
    dist = xs if side == "l" else pw - xs
    wgt = 0.30 * np.clip(1.0 - dist / (0.75 * pw), 0.0, 1.0) ** 2
    rgb = (np.asarray(_BG, np.float32)[None, :] * (1 - wgt[:, None])
           + np.asarray(col, np.float32)[None, :] * wgt[:, None])
    arr = np.zeros((ph, pw, 4), dtype=np.float32)
    arr[..., :3] = rgb[None, :, :]
    arr[..., 3] = _BG_A
    arr[..., 3] = _BG_A * m
    img = Image.fromarray((arr + 0.5).astype(np.uint8), "RGBA")
    # Filet clair d'1 px (même finition que le tableau de fin)…
    ImageDraw.Draw(img).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=R,
                                          outline=_cards._ov_tint(0.08, _BG_A), width=1)
    # … puis la bande pleine couleur côté extérieur, par-dessus le filet.
    arr = np.asarray(img).copy()
    bcols = dist < band
    arr[:, bcols, :3] = col
    arr[:, bcols, 3] = (255.0 * m[:, bcols] + 0.5).astype(np.uint8)
    img = Image.fromarray(arr, "RGBA")
    d = ImageDraw.Draw(img)

    # Texte, centré verticalement ; aligné sur la bande.
    _, h = _panel_measure(v, k, U, big_size)
    pad_out = band + U(30)
    x = pad_out if side == "l" else pw - pad_out
    max_w = pw - pad_out - inner_pad
    y = (ph - h) / 2
    white = (*_cards.OV_WHITE, 255)
    if small:
        y += _cap(f["small"])
        _text(d, x, y, _fit(small, f["small"], max_w, f["track"]), f["small"],
              (*_lighter(col, 0.55), 255), f["track"], side)
        y += U(16)
    y += _cap(fb)
    _text(d, x, y, _fit(big, fb, max_w), fb, white, 0, side)
    if rank or club:
        y += U(22) + _cap(f["rank"])
        parts = []
        if rank:
            parts.append((rank, f["rank"], (*_lighter(col, 0.30), 255)))
        if club:
            parts.append((club, f["club"], (214, 218, 228, 255)))
        gap = U(34)
        # Club tronqué si la ligne déborde (le classement reste entier).
        if len(parts) == 2:
            room = max_w - _tw(rank, f["rank"]) - gap
            parts[1] = (_fit(club, f["club"], room), f["club"], parts[1][2])
        total = sum(_tw(t, fn) for t, fn, _ in parts) + (gap if len(parts) == 2 else 0)
        cx = x if side == "l" else x - total
        for i, (t, fn, c) in enumerate(parts):
            _text(d, cx, y, t, fn, c)
            cx += _tw(t, fn)
            if i == 0 and len(parts) == 2:
                # Séparateur : petit point discret entre classement et club.
                r = U(2.6)
                dy = y - _cap(fn) / 2
                d.ellipse([cx + gap / 2 - r, dy - r, cx + gap / 2 + r, dy + r],
                          fill=(*_cards.OV_FAINT, 255))
                cx += gap
    return img


def _vs_sprite(U, scale: float = 1.0, ss: int = 4):
    """Pastille « VS » : disque encre, anneau mi-bleu mi-orange, VS blanc.
    Dessinée en sur-échantillonné puis réduite (bords nets à toute échelle)."""
    from PIL import Image, ImageDraw
    R = U(60) * scale
    ring = max(1.0, U(4.5) * scale)
    size = int(math.ceil(2 * R + 4))
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = n / 2
    rr = R * ss
    d.ellipse([c - rr, c - rr, c + rr, c + rr], fill=(*_BG, 245))
    w = int(round(ring * ss))
    box = [c - rr + w / 2, c - rr + w / 2, c + rr - w / 2, c + rr - w / 2]
    d.arc(box, 90, 270, fill=(*_cards.OV_P1, 255), width=w)
    d.arc(box, 270, 90, fill=(*_cards.OV_P2, 255), width=w)
    f = _disp(max(4, int(round(U(62) * scale * ss))))
    l, t, r, b = d.textbbox((0, 0), "VS", font=f, anchor="ls")
    d.text((c - (l + r) / 2, c - (t + b) / 2), "VS", font=f, fill=(255, 255, 255, 255),
           anchor="ls")
    return img.resize((size, size), Image.LANCZOS)


def _band_sprite(v: dict, U, max_w: float):
    """Bandeau de l'événement : OCCASION · lieu · date. Deux lignes si trop
    large (occasion au-dessus). None si tout est vide."""
    from PIL import Image, ImageDraw
    occ = _clean(v.get("occasion")).upper()
    rest = [t for t in (_clean(v.get("lieu")), _clean(v.get("date"))) if t]
    if not occ and not rest:
        return None
    f_occ, tr = _hn(U(19), 1), U(3.0)
    f_rest = _hn(U(20), 0)
    gap = U(36)
    pad_x, R = U(30), U(12)

    def seg_w(items):
        return sum(_tw(t, fn, trk) for t, fn, trk, _ in items) + gap * (len(items) - 1)

    items = []
    if occ:
        items.append((occ, f_occ, tr, (*_cards.OV_GOLD, 255)))
    for i, t in enumerate(rest):
        items.append((t, f_rest, 0, (224, 228, 236, 255) if i == 0 and len(rest) == 2
                      else (*_cards.OV_MUTED, 255) if t == _clean(v.get("date")) else
                      (224, 228, 236, 255)))
    lines = [items]
    if seg_w(items) + 2 * pad_x > max_w and occ and rest:
        lines = [items[:1], items[1:]]
    lines = [[(_fit(t, fn, max_w - 2 * pad_x, trk), fn, trk, c) for t, fn, trk, c in ln]
             for ln in lines]
    line_h = U(50)
    bw = int(min(max_w, max(seg_w(ln) for ln in lines) + 2 * pad_x))
    bh = line_h * len(lines) - (U(12) if len(lines) == 2 else 0)
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=R, fill=(*_BG, _BG_A))
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=R, outline=_cards._ov_tint(0.08, _BG_A),
                        width=1)
    for li, ln in enumerate(lines):
        cy = (li + 0.5) * (bh / len(lines))
        x = (bw - seg_w(ln)) / 2
        for i, (t, fn, trk, c) in enumerate(ln):
            base = cy + _cap(fn) / 2
            _text(d, x, base, t, fn, c, trk)
            x += _tw(t, fn, trk)
            if i < len(ln) - 1:
                r = U(2.6)
                d.ellipse([x + gap / 2 - r, cy - r, x + gap / 2 + r, cy + r],
                          fill=(*_cards.OV_FAINT, 255))
                x += gap
    return img


# ── Mise en page (16:9 et 9:16) ──────────────────────────────────────────────

def intro_layout(values: dict, W: int, H: int) -> dict:
    """Géométrie et sprites de la carte pour une vidéo W × H.

    Paysage : encarts côte à côte, VS à cheval sur leurs bords intérieurs,
    bandeau dessous. Portrait : encarts empilés (J1 en haut, décalé à gauche ;
    J2 en bas, décalé à droite), VS entre les deux, bandeau dessous.
    Tout est coté en `u`. La zone rendue (`region`) est une bande pleine
    largeur : les encarts entrent depuis les bords de l'image.
    """
    u = _cards._overlay_unit(W, H)

    def U(x: float) -> int:
        return max(1, int(round(x * u)))

    portrait = H > W
    band = U(6)
    pad_out = band + U(30)
    vs_r = U(60)

    if portrait:
        inner = U(34)
        pw_min, pw_max = U(560), W - U(110)
    else:
        seam = U(30)                             # demi-écart entre les deux encarts
        inner = vs_r - seam + U(30)              # le texte s'arrête avant le VS
        pw_min, pw_max = U(470), int(W / 2 - seam - U(56))

    # Police du nom : réduite (jusqu'à 62 u) avant de tronquer si le nom est long.
    big = U(86)
    for sz in (86, 80, 74, 68, 62):
        big = U(sz)
        need = max(_panel_measure(values, k, U, big)[0] for k in ("p1", "p2"))
        if need + pad_out + inner <= pw_max:
            break
    need = max(_panel_measure(values, k, U, big)[0] for k in ("p1", "p2"))
    pw = int(min(pw_max, max(pw_min, need + pad_out + inner + U(8))))
    pw -= pw % 2
    h_c = max(_panel_measure(values, k, U, big)[1] for k in ("p1", "p2"))
    ph = int(max(U(150), h_c + 2 * U(32)))
    ph -= ph % 2

    p1 = _panel_sprite(values, "p1", "l", pw, ph, U, big, inner)
    p2 = _panel_sprite(values, "p2", "r", pw, ph, U, big, inner)
    bnd = _band_sprite(values, U, min(W - 2 * U(60), U(1100) if not portrait else W))

    cx = W / 2
    if portrait:
        gap = U(150)
        stagger = U(26)
        bh = (U(30) + bnd.height) if bnd is not None else 0
        total = 2 * ph + gap + bh
        top = H / 2 - total / 2
        x1 = (W - pw) / 2 - stagger
        x2 = (W - pw) / 2 + stagger
        y1, y2 = top, top + ph + gap
        vs_c = (cx, top + ph + gap / 2)
        band_y = y2 + ph + U(30)
    else:
        bh = (U(26) + bnd.height) if bnd is not None else 0
        total = ph + bh
        top = H / 2 - total / 2
        x1, x2 = cx - seam - pw, cx + seam
        y1 = y2 = top
        vs_c = (cx, top + ph / 2)
        band_y = top + ph + U(26)

    group_top = top
    group_bot = top + total
    margin = U(90)
    ry0 = max(0, int(group_top - margin)) // 2 * 2
    ry1 = min(H, int(math.ceil(group_bot + margin)))
    rh = (ry1 - ry0 + 1) // 2 * 2
    rh = min(rh, H - ry0 - (H - ry0) % 2)

    return dict(
        W=W, H=H, u=u, U=U, portrait=portrait, region=(0, ry0, W, rh),
        p1=p1, p2=p2, band=bnd, pw=pw, ph=ph,
        pos1=(x1, y1 - ry0), pos2=(x2, y2 - ry0),
        # Distance d'entrée : l'encart part entièrement hors de l'image.
        d1=x1 + pw + U(40), d2=W - x2 + U(40),
        vs_c=(vs_c[0], vs_c[1] - ry0), vs_r=vs_r,
        vs_hi=_vs_sprite(U, 1.7),               # source des échelles > 1 (pop)
        vs=_vs_sprite(U, 1.0),
        band_pos=((W - bnd.width) / 2 if bnd is not None else 0, band_y - ry0),
    )


# ── Animation ────────────────────────────────────────────────────────────────

def _ease_out(p: float, k: float = 3.0) -> float:
    p = min(1.0, max(0.0, p))
    return 1.0 - (1.0 - p) ** k


def _ease_in(p: float, k: float = 3.0) -> float:
    p = min(1.0, max(0.0, p))
    return p ** k


def intro_state(t: float, t_gone: float, L: dict) -> dict:
    """État de la carte à l'instant `t` (s). `t_gone` : fin de la sortie."""
    U = L["U"]
    ov = U(16) if not L["portrait"] else U(12)   # dépassement vers le centre à l'impact
    t_exit = max(T_HIT + SETTLE + 0.2, t_gone - EXIT_DUR)
    s = {}

    # Encarts : entrée ease-out marquée jusqu'au dépassement, puis rebond amorti.
    if t < T_HIT:
        k = 1.0 - _ease_out(t / T_HIT, 3.2)      # 1 → 0
        pos = (L["d1"] + ov) * k - ov            # distance au repos (< 0 : au-delà)
        s["o1"], s["o2"] = -pos, pos * L["d2"] / L["d1"]
    elif t < T_HIT + SETTLE:
        a = (t - T_HIT) / SETTLE
        pos = -ov * math.exp(-3.2 * a) * math.cos(math.pi * 1.25 * a) * (1 - a)
        s["o1"], s["o2"] = -pos, pos
    else:
        s["o1"] = s["o2"] = 0.0
    if t >= t_exit:                              # sortie : ease-in vers l'extérieur
        e = _ease_in((t - t_exit) / EXIT_DUR, 2.6)
        s["o1"] = -(L["d1"] + L["U"](20)) * e
        s["o2"] = (L["d2"] + L["U"](20)) * e

    # Secousse à l'impact (amortie, sur le groupe : encarts + VS).
    a = t - T_HIT
    if 0 <= a < SHAKE_DUR:
        amp = U(7) * math.exp(-a / 0.075)
        s["shake"] = (amp * math.sin(2 * math.pi * 17 * a),
                      0.55 * amp * math.sin(2 * math.pi * 13 * a + 1.3))
    else:
        s["shake"] = (0.0, 0.0)

    # VS : claque à l'impact (grossi puis posé), s'efface à la sortie.
    a = t - (T_HIT - 0.03)
    if a < 0:
        s["vs"] = (0.0, 1.0)
    elif a < VS_POP:
        p = a / VS_POP
        sc = 1.0 + 0.65 * (1 - _ease_out(p, 3.0)) - 0.05 * math.sin(math.pi * p)
        s["vs"] = (min(1.0, p / 0.25), sc)
    else:
        s["vs"] = (1.0, 1.0)
    if t >= t_exit:
        p = (t - t_exit) / (EXIT_DUR * 0.6)
        s["vs"] = (max(0.0, 1.0 - _ease_in(p, 2.0)), 1.0 - 0.25 * _ease_in(p, 2.0))

    # Flash et onde de choc.
    a = t - T_HIT
    s["flash"] = 0.55 * (1 - a / FLASH_DUR) ** 2 if 0 <= a < FLASH_DUR else 0.0
    if 0 <= a < RING_DUR:
        p = a / RING_DUR
        r0, r1 = L["vs_r"] * 1.05, (U(560) if not L["portrait"] else U(430))
        s["ring"] = (r0 + (r1 - r0) * _ease_out(p, 2.4),
                     max(1.0, U(5) * (1 - p) + U(1.2) * p),
                     0.50 * (1 - p) ** 1.6)
    else:
        s["ring"] = None

    # Bandeau : glisse vers le haut et apparaît après l'impact ; part en premier.
    a = t - (T_HIT + BAND_DELAY)
    if a < 0:
        s["band"] = (0.0, U(16))
    else:
        p = _ease_out(a / BAND_DUR, 3.0)
        s["band"] = (p, U(16) * (1 - p))
    if t >= t_exit:
        p = _ease_in((t - t_exit) / (EXIT_DUR * 0.55), 2.0)
        s["band"] = (max(0.0, 1.0 - p), U(10) * p)

    # Voile sombre derrière le groupe (lisibilité), suit l'entrée et la sortie.
    dim = _ease_out(t / 0.35, 2.0)
    if t >= t_exit:
        dim *= 1.0 - _ease_in((t - t_exit) / EXIT_DUR, 1.5)
    s["dim"] = dim

    s["static"] = (T_HIT + max(SETTLE, RING_DUR, BAND_DELAY + BAND_DUR, SHAKE_DUR) + 0.02
                   <= t < t_exit)
    s["gone"] = t >= t_gone
    return s


def _paste(img, sprite, x: float, y: float, alpha: float = 1.0) -> None:
    """Compose `sprite` en (x, y) arrondis, rogné aux bords, alpha global."""
    if sprite is None or alpha <= 0.0:
        return
    if alpha < 1.0:
        sprite = sprite.copy()
        a = sprite.getchannel("A").point(lambda v: int(v * alpha + 0.5))
        sprite.putalpha(a)
    x0, y0 = int(round(x)), int(round(y))
    sx, sy = max(0, -x0), max(0, -y0)
    ex, ey = min(sprite.width, img.width - x0), min(sprite.height, img.height - y0)
    if ex <= sx or ey <= sy:
        return
    img.alpha_composite(sprite.crop((sx, sy, ex, ey)) if (sx or sy or ex < sprite.width
                                                           or ey < sprite.height) else sprite,
                        (x0 + sx, y0 + sy))


def _ring(img, cx: float, cy: float, r: float, w: float, alpha: float, color) -> None:
    """Anneau anti-aliasé (onde de choc), calculé dans sa seule boîte."""
    import numpy as np
    from PIL import Image
    if alpha <= 0.0:
        return
    R = r + w + 2
    x0, y0 = max(0, int(cx - R)), max(0, int(cy - R))
    x1, y1 = min(img.width, int(math.ceil(cx + R))), min(img.height, int(math.ceil(cy + R)))
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    dist = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy)
    # Profil : cœur net + traîne douce vers l'intérieur (lecture « onde »).
    core = np.clip(w / 2 + 0.5 - np.abs(dist - r), 0.0, 1.0)
    tail = np.clip(1.0 - (r - dist) / (w * 5.0), 0.0, 1.0) * (dist < r)
    a = np.maximum(core, 0.18 * tail ** 2) * alpha
    layer = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
    layer[..., 0], layer[..., 1], layer[..., 2] = color
    layer[..., 3] = (a * 255.0 + 0.5).astype(np.uint8)
    img.alpha_composite(Image.fromarray(layer, "RGBA"), (x0, y0))


def _hlg_color(rgb) -> tuple[int, int, int]:
    from PIL import Image
    return tuple(_cards._ov_hlg(Image.new("RGBA", (1, 1), (*rgb, 255))).getpixel((0, 0))[:3])


def _dim_layer(L: dict):
    """Voile : bande noire à bords très adoucis, à l'opacité maximale (~30 %)."""
    import numpy as np
    from PIL import Image
    _, _, W, rh = L["region"]
    y = (np.arange(rh, dtype=np.float32) + 0.5) / rh
    edge = 0.34
    prof = np.clip(np.minimum(y, 1 - y) / edge, 0.0, 1.0)
    prof = prof * prof * (3 - 2 * prof) * 0.22
    col = np.zeros((rh, 1, 4), dtype=np.uint8)
    col[..., 3] = (prof[:, None] * 255 + 0.5).astype(np.uint8)
    return Image.fromarray(col, "RGBA").resize((W, rh), Image.NEAREST)


def _halo(img, cx: float, cy: float, radius: float, strength: float, color) -> None:
    """Halo radial (alpha dégradé) composé sur `img` (RGBA), rogné aux bords."""
    import numpy as np
    from PIL import Image
    if strength <= 0.0 or radius < 1.0:
        return
    x0, y0 = int(math.floor(cx - radius)) - 1, int(math.floor(cy - radius)) - 1
    size = int(math.ceil(2 * radius)) + 3
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    dist = np.hypot(xx + x0 + 0.5 - cx, yy + y0 + 0.5 - cy) / radius
    a = np.clip(1.0 - dist, 0.0, 1.0) ** 1.5 * strength
    layer = np.zeros((size, size, 4), dtype=np.uint8)
    layer[..., 0], layer[..., 1], layer[..., 2] = color
    layer[..., 3] = (a * 255.0 + 0.5).astype(np.uint8)
    sprite = Image.fromarray(layer, "RGBA")
    # alpha_composite refuse une destination négative : on rogne à la main.
    sx, sy = max(0, -x0), max(0, -y0)
    ex, ey = min(size, img.width - x0), min(size, img.height - y0)
    if ex <= sx or ey <= sy:
        return
    img.alpha_composite(sprite.crop((sx, sy, ex, ey)), (x0 + sx, y0 + sy))


def render_intro_frame(L: dict, s: dict, dim_full=None):
    """Image RGBA de la zone (`region`) pour l'état `s` (cf. intro_state)."""
    from PIL import Image
    _, _, W, rh = L["region"]
    if s["gone"]:
        return Image.new("RGBA", (W, rh), (0, 0, 0, 0))
    if dim_full is None:
        dim_full = _dim_layer(L)
    if s["dim"] >= 0.999:
        img = dim_full.copy()
    else:
        img = Image.new("RGBA", (W, rh), (0, 0, 0, 0))
        _paste(img, dim_full, 0, 0, s["dim"])
    sx, sy = s["shake"]
    vx, vy = L["vs_c"][0] + sx, L["vs_c"][1] + sy

    x1, y1 = L["pos1"]
    x2, y2 = L["pos2"]
    _paste(img, L["p1"], x1 + s["o1"] + sx, y1 + sy)
    _paste(img, L["p2"], x2 + s["o2"] + sx, y2 + sy)

    b_a, b_dy = s["band"]
    if L["band"] is not None and b_a > 0:
        bx, by = L["band_pos"]
        _paste(img, L["band"], bx, by + b_dy, b_a)

    if s["flash"] > 0:                       # lueur brève au point d'impact
        _halo(img, vx, vy, L["U"](260), s["flash"], L.get("flash_col", FLASH_COL))

    if s["ring"] is not None:
        r, w, a = s["ring"]
        _ring(img, vx, vy, r, w, a, L.get("ring_col", RING_COL))

    v_a, v_sc = s["vs"]
    if v_a > 0:
        if abs(v_sc - 1.0) < 1e-3:
            spr = L["vs"]
        else:
            src = L["vs_hi"]
            n = max(2, int(round(src.width * v_sc / 1.7)))
            spr = src.resize((n, n), Image.LANCZOS)
        _paste(img, spr, vx - spr.width / 2, vy - spr.height / 2, v_a)
    return img


def render_intro_still(values: dict, W: int, H: int, t: float = 2.0,
                       t_gone: float = INTRO_DUR + EXIT_DUR, L: dict | None = None):
    """Image W × H (RGBA transparente hors de la carte) à l'instant `t` :
    aperçu du dialogue et images clés des tests."""
    from PIL import Image
    L = L or intro_layout(values, W, H)
    fr = render_intro_frame(L, intro_state(t, t_gone, L))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(fr, (0, L["region"][1]))
    return out


def render_intro_sequence(values: dict, W: int, H: int, out_dir: str, fps: float,
                          t_gone: float, workers: int | None = None) -> dict:
    """Écrit la séquence (TGA RLE via _ov_save, donc HLG si besoin) dans `out_dir`.

    Retourne {pattern, frames, x, y} : motif ffmpeg, nombre d'images, position
    de la zone dans l'image. La dernière image est vide (intro retirée) ; les
    images de la tenue sont des liens durs vers une seule image.
    """
    L = intro_layout(values, W, H)
    dim_full = _dim_layer(L)
    hlg = _cards._OV_HLG
    if hlg:
        # Source HLG : sprites convertis UNE fois (et couleurs du flash et de
        # l'onde), puis composés directement en signal HLG ; les images sont
        # écrites telles quelles. Le voile est noir : identique dans les deux.
        L = dict(L)
        for k in ("p1", "p2", "band", "vs", "vs_hi"):
            if L[k] is not None:
                L[k] = _cards._ov_hlg(L[k])
        L["flash_col"] = _hlg_color(FLASH_COL)
        L["ring_col"] = _hlg_color(RING_COL)
    n_frames = int(math.ceil(t_gone * fps)) + 1
    keys, todo = [], {}
    for f in range(n_frames):
        t = f / fps
        s = intro_state(t, t_gone, L)
        key = ("gone",) if s["gone"] else ("hold",) if s["static"] else ("f", f)
        keys.append(key)
        todo.setdefault(key, s)
    order = {k: j for j, k in enumerate(todo)}

    def job(item):
        key, s = item
        p = os.path.join(out_dir, f"intro_u{order[key]:05d}.tga")
        img = render_intro_frame(L, s, dim_full)
        if hlg:
            img.save(p, "TGA", rle=True)
        else:
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
    return dict(pattern=os.path.join(out_dir, ANIM_PATTERN), frames=n_frames,
                x=L["region"][0], y=L["region"][1], unique=len(todo))
