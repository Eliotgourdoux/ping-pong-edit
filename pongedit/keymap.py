"""Raccourcis clavier indépendants de la disposition (QWERTY / AZERTY / QWERTZ).

L'app a été pensée pour un QWERTY : A et S côte à côte pour les points, C juste en dessous
pour couper. Sur un AZERTY, viser la LETTRE disperse ces trois touches (le A est en haut à
gauche). Elles suivent donc la POSITION physique de la touche, et l'écran affiche la lettre
réellement gravée à cet endroit sur le clavier de l'utilisateur.

Tous les autres raccourcis (F, Z, R, M, I, N) restent des LETTRES, sur toutes les dispositions ;
la vitesse utilise les flèches haut / bas.

Identifiants logiques : "A", "S", "C" (positionnels) et "F", "Z", "R", "M", "I", "N" (lettres).
"""

import subprocess
import sys

from PySide6.QtCore import Qt

# Touches positionnelles : identifiant → (code Windows, code macOS, code Linux/X11)
_PHYS = {
    "A": (0x1E, 0, 38),   "S": (0x1F, 1, 39),   "C": (0x2E, 8, 54),
}
_POSITIONAL = tuple(_PHYS)
_COL = 0 if sys.platform.startswith("win") else (1 if sys.platform == "darwin" else 2)
_BY_CODE = {v[_COL]: k for k, v in _PHYS.items()}

# Identifiant logique d'après la lettre produite (toutes dispositions).
_BY_QTKEY = {
    Qt.Key.Key_A: "A", Qt.Key.Key_S: "S", Qt.Key.Key_C: "C", Qt.Key.Key_F: "F",
    Qt.Key.Key_Z: "Z", Qt.Key.Key_R: "R", Qt.Key.Key_M: "M", Qt.Key.Key_I: "I",
    Qt.Key.Key_N: "N",
}

# Lettre gravée à l'emplacement QWERTY de A / S / C, selon la famille de clavier.
_LABELS = {
    "qwerty": {},
    "azerty": {"A": "Q"},
    "qwertz": {},
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


def _positional(event, fam: str) -> str | None:
    """A / S / C d'après l'EMPLACEMENT (code matériel, sinon caractère produit)."""
    sc = event.nativeScanCode()
    if sc:
        return _BY_CODE.get(sc)
    # Pas de code matériel (ou macOS, où la touche A vaut 0) : on retrouve l'emplacement
    # à partir du caractère produit — sur AZERTY, l'emplacement du A produit « Q ».
    labels = _LABELS[fam]
    inverse = {labels.get(k, k): k for k in _POSITIONAL}
    qk = event.key()
    ch = chr(qk).upper() if 0x20 < int(qk) < 0x7F else ""
    return inverse.get(ch)


def logical_key(event) -> str | None:
    """Identifiant logique de la touche pressée ("A", "S", "C", "F", "Z", …) ou None.

    QWERTY : on se fie à la lettre (comportement historique, inchangé).
    Autre disposition : A / S / C suivent la position physique ; une lettre A, S ou C qui n'est
    PAS à cet emplacement ne déclenche rien (la vraie lettre A, en haut à gauche d'un AZERTY,
    lancerait sinon « point J1 » en plus de la touche voisine de S). Les autres raccourcis
    restent des lettres.
    """
    letter = _BY_QTKEY.get(event.key())
    fam = family()
    if fam == "qwerty":
        return letter
    pos = _positional(event, fam)
    if pos:
        return pos
    if letter in _POSITIONAL:
        return None
    return letter


def label(logical: str) -> str:
    """Ce qu'il faut afficher pour ce raccourci sur le clavier courant."""
    return _LABELS.get(family(), {}).get(logical, logical)
