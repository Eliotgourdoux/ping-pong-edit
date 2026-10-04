"""Palette de l'interface (thèmes sombre / clair) et helpers de widgets.

L'interface suit le thème de macOS : `UI` contient les jetons du thème actif et
est mis à jour EN PLACE par `apply_palette()` (les modules l'importent par
référence). Les feuilles de style locales enregistrées via `themed()` sont
ré-appliquées par `restyle_registered()` lors d'un changement de thème.
Les incrustations vidéo (HUD, bandeaux) utilisent `UI_DARK` : elles ne changent
pas avec le thème.
"""

from PySide6.QtWidgets import QVBoxLayout, QLabel, QFrame


# ── Palettes (design system) ─────────────────────────────────────────────────
# Mêmes clés dans les deux thèmes. Les jetons « tl_* » servent au dessin de la
# timeline (QColor, format #AARRGGBB).

UI_DARK = {
    "bg":      "#0B0E14",
    "surface": "#141821",
    "raised":  "#1A1F2B",
    "border":  "#232936",
    "text":    "#FFFFFF",   # blanc franc : l'ancien #E6E9EF paraissait gris
    "muted":   "#8B93A5",
    "dim":     "#6B7387",
    "faint":   "#4B5366",
    "accent":  "#4F8DFF",
    "p1":      "#386CD4",   # = OV_P1 du rendu final (export/cards.py)
    "p2":      "#DE6842",   # = OV_P2
    "danger":  "#F0525A",
    "success": "#3DDC97",
    "gold":    "#F5C542",
    "warn":    "#F5C542",
    # Survol / états
    "hover":          "#222838",
    "border_hover":   "#2E3648",
    "selected":       "#182642",
    "on_accent":      "#0B0E14",
    "on_player":      "#0B0E14",
    "accent_hover":   "#6AA0FF",
    "accent_pressed": "#3B74E0",
    "accent_dis_bg":  "#2A3A5C",
    "accent_dis_fg":  "#7C8CAB",
    "danger_bg":      "#3A1518",
    "danger_edge":    "#4A1B1F",
    "danger_pressed": "#2A0E10",
    "danger_text":    "#F58A90",
    "danger_soft":    "#FBB4B8",
    "success_bg":     "#0F3B2C",
    "success_edge":   "#1B5A43",
    # Rotation (violet)
    "rot":            "#B08CFF",
    "rot_bg":         "#2A1F4D",
    "rot_fg":         "#C9B8FF",
    "rot_edge":       "#372A63",
    "rot_fg_hover":   "#EDE8FF",
    "rot_on":         "#7A5BE0",
    "rot_on_hover":   "#8B6CF6",
    "rot_on_fg":      "white",
    # Timeline (dessin)
    "tl_mid":       "#0EFFFFFF",
    "tl_cut":       "#66F0525A",
    "tl_live_cut":  "#A0F0525A",
    "tl_reject":    "#78000000",
    "tl_hatch":     "#1CFFFFFF",
    "tl_played":    "#164F8DFF",
    "tl_hover":     "#46FFFFFF",
    "tl_tc_bg":     "#BE0B1120",
    "tl_tc_fg":     "#8B93A5",
}

UI_LIGHT = {
    "bg":      "#EEF1F6",
    "surface": "#F8F9FC",
    "raised":  "#FFFFFF",
    "border":  "#D6DBE4",
    "text":    "#1A1F2B",
    "muted":   "#5B6475",
    "dim":     "#646C7E",
    "faint":   "#8A92A3",
    "accent":  "#2563D9",
    "p1":      "#2F5FC4",
    "p2":      "#C4532F",
    "danger":  "#C2303A",
    "success": "#0B7A50",
    "gold":    "#D99A00",
    "warn":    "#8A5D00",
    "hover":          "#F0F3F8",
    "border_hover":   "#BEC5D2",
    "selected":       "#DCE7FB",
    "on_accent":      "#FFFFFF",
    "on_player":      "#FFFFFF",
    "accent_hover":   "#3A74E6",
    "accent_pressed": "#1D52BA",
    "accent_dis_bg":  "#C9D6EE",
    "accent_dis_fg":  "#6F7F9C",
    "danger_bg":      "#FBE4E6",
    "danger_edge":    "#F0C3C7",
    "danger_pressed": "#F6CFD3",
    "danger_text":    "#B42630",
    "danger_soft":    "#9E1F29",
    "success_bg":     "#DDF4EA",
    "success_edge":   "#A6DCC4",
    "rot":            "#7447D6",
    "rot_bg":         "#EEE8FD",
    "rot_fg":         "#5B34B8",
    "rot_edge":       "#D8CCF7",
    "rot_fg_hover":   "#46238F",
    "rot_on":         "#7A5BE0",
    "rot_on_hover":   "#6A4AD4",
    "rot_on_fg":      "white",
    "tl_mid":       "#14000000",
    "tl_cut":       "#4AE5484F",
    "tl_live_cut":  "#99E5484F",
    "tl_reject":    "#2E1A1F2B",
    "tl_hatch":     "#38000000",
    "tl_played":    "#1A2563D9",
    "tl_hover":     "#591A1F2B",
    "tl_tc_bg":     "#E6FFFFFF",
    "tl_tc_fg":     "#5B6475",
}

assert UI_DARK.keys() == UI_LIGHT.keys()

# Palette active (mutée en place). Sombre par défaut, comme avant.
UI = dict(UI_DARK)
_current = {"dark": True}


def is_dark() -> bool:
    return _current["dark"]


def system_prefers_dark() -> bool:
    """Thème du système (Qt ≥ 6.5). Inconnu (ex. offscreen) → sombre."""
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import Qt
        scheme = QGuiApplication.styleHints().colorScheme()
        return scheme != Qt.ColorScheme.Light
    except Exception:
        return True


def preferred_dark() -> bool:
    """Thème voulu : choix manuel mémorisé (bouton de la barre d'outils) sinon thème du système."""
    try:
        from PySide6.QtCore import QSettings
        v = QSettings("PongEdit", "PongEdit").value("theme", "")
        if v in ("dark", "light"):
            return v == "dark"
    except Exception:
        pass
    return system_prefers_dark()


def save_theme_choice(dark: bool):
    try:
        from PySide6.QtCore import QSettings
        QSettings("PongEdit", "PongEdit").setValue("theme", "dark" if dark else "light")
    except Exception:
        pass


def apply_palette(dark: bool) -> bool:
    """Met `UI` à jour EN PLACE. Renvoie True si le thème a changé."""
    changed = dark != _current["dark"]
    _current["dark"] = dark
    UI.clear()
    UI.update(UI_DARK if dark else UI_LIGHT)
    return changed


# ── Feuilles de style dépendant du thème ─────────────────────────────────────
# widget → fonction qui construit sa feuille de style à partir de UI.
_THEMED: dict[int, tuple[object, object]] = {}


def themed(widget, make_css):
    """Applique `make_css()` au widget et le ré-applique à chaque changement de thème."""
    widget.setStyleSheet(make_css())
    _THEMED[id(widget)] = (widget, make_css)
    return widget


def restyle_registered():
    try:
        from shiboken6 import isValid
    except Exception:          # pragma: no cover
        isValid = lambda _w: True
    for key, (w, make_css) in list(_THEMED.items()):
        try:
            if not isValid(w):
                raise RuntimeError
            w.setStyleSheet(make_css())
        except RuntimeError:
            _THEMED.pop(key, None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sep():
    f = QFrame(); f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    themed(f, lambda: f"background:{UI['border']};border:none;"); return f

def _section(text):
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionTitle")
    return lbl

def _field_label(text, min_w=0):
    lbl = QLabel(text)
    lbl.setObjectName("fieldLabel")
    if min_w:
        lbl.setMinimumWidth(min_w)
    return lbl

def _card(title: str | None = None, spacing: int = 8) -> tuple[QFrame, QVBoxLayout]:
    """Carte de section du panneau latéral : surface arrondie + titre discret."""
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(12, 10, 12, 12)
    v.setSpacing(spacing)
    if title:
        v.addWidget(_section(title))
    return f, v
