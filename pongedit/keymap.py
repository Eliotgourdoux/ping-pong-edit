"""Raccourcis clavier indépendants de la disposition (QWERTY / AZERTY / QWERTZ).

L'app a été pensée pour un QWERTY : A et S côte à côte pour les points, Z juste en dessous
pour annuler, [ et ] côte à côte pour la vitesse. Sur un AZERTY, viser la LETTRE disperse ces
touches. On vise donc la POSITION physique de la touche (son code matériel), et on affiche à
l'écran la lettre réellement gravée à cet endroit sur le clavier de l'utilisateur.

Identifiants logiques = la lettre QWERTY à cet emplacement : "A", "S", "C", "F", "Z", "R",
"M", "I", "N", "[" et "]".
"""

import subprocess
import sys

from PySide6.QtCore import Qt

# identifiant → (code Windows, code macOS, code Linux/X11)
_PHYS = {
    "A": (0x1E, 0, 38),   "S": (0x1F, 1, 39),   "C": (0x2E, 8, 54),   "F": (0x21, 3, 41),
    "Z": (0x2C, 6, 52),   "R": (0x13, 15, 27),  "M": (0x32, 46, 58),  "I": (0x17, 34, 31),
    "N": (0x31, 45, 57),  "[": (0x1A, 33, 34),  "]": (0x1B, 30, 35),
}
_COL = 0 if sys.platform.startswith("win") else (1 if sys.platform == "darwin" else 2)
_BY_CODE = {v[_COL]: k for k, v in _PHYS.items()}

# Repli quand le système ne fournit pas le code matériel (ou en QWERTY) : on vise la lettre.
_BY_QTKEY = {
    Qt.Key.Key_A: "A", Qt.Key.Key_S: "S", Qt.Key.Key_C: "C", Qt.Key.Key_F: "F",
    Qt.Key.Key_Z: "Z", Qt.Key.Key_R: "R", Qt.Key.Key_M: "M", Qt.Key.Key_I: "I",
    Qt.Key.Key_N: "N", Qt.Key.Key_BracketLeft: "[", Qt.Key.Key_BracketRight: "]",
}

# Lettre gravée à l'emplacement QWERTY, selon la famille de clavier.
_LABELS = {
    "qwerty": {},
    "azerty": {"A": "Q", "Z": "W", "M": ",", "[": "^", "]": "$"},
    "qwertz": {"Z": "Y"},
}

_family: str | None = None


def _detect_windows() -> str:
    """Compare ce que Windows grave réellement sur les emplacements A et Z."""
    import ctypes
    u = ctypes.windll.user32

    def char_at(scancode: int) -> str:
        vk = u.MapVirtualKeyW(scancode, 3)            # MAPVK_VSC_TO_VK_EX
        ch = u.MapVirtualKeyW(vk, 2) & 0x7FFFFFFF      # MAPVK_VK_TO_CHAR (touche morte : bit de poids fort)
        return chr(ch).upper() if ch else ""

    a, z = char_at(0x1E), char_at(0x2C)
    if a == "Q":
        return "azerty"
    if z == "Y":
        return "qwertz"
    return "qwerty"


def _detect_other() -> str:
    ident = ""
    try:
        if sys.platform == "darwin":
            ident = subprocess.run(
                ["defaults", "read", "com.apple.HIToolbox", "AppleCurrentKeyboardLayoutInputSourceID"],
                capture_output=True, text=True, timeout=3).stdout.lower()
        else:
            ident = subprocess.run(["setxkbmap", "-query"], capture_output=True,
                                   text=True, timeout=3).stdout.lower()
    except Exception:
        pass
    if "french" in ident or "belgian" in ident or "layout:     fr" in ident or "layout:     be" in ident:
        return "azerty"
    if "german" in ident or "swiss" in ident or "austrian" in ident or "layout:     de" in ident:
        return "qwertz"
    return "qwerty"


def family() -> str:
    """'qwerty' | 'azerty' | 'qwertz' (détecté une fois)."""
    global _family
    if _family is None:
        try:
            _family = _detect_windows() if sys.platform.startswith("win") else _detect_other()
        except Exception:
            _family = "qwerty"
        print(f"Clavier détecté : {_family}")
    return _family


def _char_of(qt_key: int) -> str:
    if qt_key in (Qt.Key.Key_Dead_Circumflex, Qt.Key.Key_AsciiCircum):
        return "^"
    return chr(qt_key).upper() if 0x20 < int(qt_key) < 0x7F else ""


def logical_key(event) -> str | None:
    """Identifiant logique de la touche pressée ("A", "S", …) ou None.

    QWERTY : on se fie à la lettre (comportement historique, inchangé).
    Autre disposition : on se fie à l'EMPLACEMENT physique (code matériel) ; à défaut de code,
    on retrouve l'emplacement à partir du caractère produit (sur AZERTY, l'emplacement du A
    produit « Q »). Une lettre qui n'est PAS à un emplacement connu ne déclenche rien — sinon
    la vraie lettre A, en haut à gauche d'un AZERTY, lancerait « point J1 » en plus de la
    touche voisine de S.
    """
    fam = family()
    if fam == "qwerty":
        return _BY_QTKEY.get(event.key())
    sc = event.nativeScanCode()
    if sc:
        return _BY_CODE.get(sc)
    labels = _LABELS[fam]
    inverse = {labels.get(k, k): k for k in _PHYS}
    return inverse.get(_char_of(event.key()))


def label(logical: str) -> str:
    """Ce qu'il faut afficher pour ce raccourci sur le clavier courant."""
    return _LABELS.get(family(), {}).get(logical, logical)
