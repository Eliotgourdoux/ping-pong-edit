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
    # Verre liquide « Cockpit » : les fonds sont des voiles translucides (#AARRGGBB, valables
    # en QSS comme pour QColor) posés sur le voile de la fenêtre ; le texte est toujours blanc
    # sur vitre sombre. Les teintes de texte restent opaques (le mélange QColor ignore l'alpha).
    "bg":      "#00000000",   # transparent : c'est le voile de la fenêtre qui fait le fond
    "surface": "#4D060910",   # vitre : nuit désaturée 0.30
    "raised":  "#12FFFFFF",   # bouton / champ sur vitre : blanc 0.07
    "border":  "#22FFFFFF",
    "text":    "#FFFFFF",
    "muted":   "#C6CAD2",     # ~0.78
    "dim":     "#A2A7B2",
    "faint":   "#8B909C",     # ~0.58, reste lisible sur la vitre
    "accent":  "#5CC8FF",     # cyan-bleu : seule couleur d'accent
    "p1":      "#386CD4",     # = OV_P1 du rendu final (export/cards.py)
    "p2":      "#DE6842",     # = OV_P2
    "p1_text": "#7FA8FF",     # versions claires pour du TEXTE sur vitre sombre
    "p2_text": "#FF9A78",
    "danger":  "#FF5A6A",
    "success": "#4BF0A0",
    "gold":    "#FFB648",
    "warn":    "#FFB648",
    # Survol / états
    "hover":          "#2BFFFFFF",
    "border_hover":   "#59FFFFFF",
    "selected":       "#2E5CC8FF",
    "on_accent":      "#FFFFFF",
    "on_player":      "#0B0E14",
    "accent_hover":   "#8DD9FF",
    "accent_pressed": "#3FA6DE",
    "accent_dis_bg":  "#14FFFFFF",
    "accent_dis_fg":  "#6F7385",
    "danger_bg":      "#33FF5A6A",
    "danger_edge":    "#8CFF5A6A",
    "danger_pressed": "#1FFF5A6A",
    "danger_text":    "#FF9AA4",
    "danger_soft":    "#FFC2C8",
    "success_bg":     "#294BF0A0",
    "success_edge":   "#8C4BF0A0",
    # Rotation (violet néon)
    "rot":            "#8FA6C8",
    "rot_bg":         "#268FA6C8",
    "rot_fg":         "#D6E2F5",
    "rot_edge":       "#668FA6C8",
    "rot_fg_hover":   "#FFFFFF",
    "rot_on":         "#8C8FA6C8",
    "rot_on_hover":   "#A68FA6C8",
    "rot_on_fg":      "white",
    # Néon / verre
    "violet":      "#7F93B5",   # neutralisé (bleu-gris) : plus de violet
    "pink":        "#5CC8FF",   # neutralisé : plus de rose
    "field":       "#4D04060C",   # fond des champs de saisie
    "glass_top":   "#12FFFFFF",   # dégradé du haut des cartes
    "glass_bot":   "#40000000",
    "spec":        "#66FFFFFF",   # reflet spéculaire (bord haut)
    "accent_bg":   "#2E5CC8FF",
    "accent_edge": "#8C5CC8FF",
    "popup":       "#1A1E27",     # menus déroulants : opaques, jamais translucides
    "veil":        "#12151C",     # fond opaque des boîtes de dialogue système
    # Timeline (dessin)
    "tl_bg":        "#9908090E",
    "tl_mid":       "#14FFFFFF",
    "tl_cut":       "#66FF5A6A",
    "tl_live_cut":  "#A0FF5A6A",
    "tl_reject":    "#78000000",
    "tl_hatch":     "#1CFFFFFF",
    "tl_played":    "#205CC8FF",
    "tl_hover":     "#46FFFFFF",
    "tl_tc_bg":     "#BE0A0D14",
    "tl_tc_fg":     "#C6CAD2",
}

# Le verre néon est sombre par nature (texte blanc sur vitre sombre) : le thème « clair »
# reprend la même palette. Le sélecteur de thème de la barre d'outils est masqué.
UI_LIGHT = dict(UI_DARK)

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
    dark = True   # verre néon : un seul thème, sombre
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
    lbl = QLabel(text)
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
    from pongedit.ui.glass import GlassCard
    f = GlassCard()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 14, 16, 16)
    v.setSpacing(spacing)
    if title:
        v.addWidget(_section(title))
    return f, v
