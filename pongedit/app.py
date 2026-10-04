"""Lancement de l'application."""

import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon

from pongedit.utils import PROJECT_DIR
from pongedit.app_state import _cleanup_orphan_exports
from pongedit.ui.main_window import MainWindow
from pongedit.ui import style


# ── Entry point ───────────────────────────────────────────────────────────────

def _deferred_orphan_cleanup():
    """Nettoyage des exports orphelins, exécuté APRÈS l'affichage de la fenêtre.
    Il lance `ps` (~150 ms) : le faire avant `show()` retardait l'apparition de la
    fenêtre d'autant, pour un travail qui n'a aucune raison d'être bloquant."""
    try:
        n = _cleanup_orphan_exports()
        if n:
            print(f"Nettoyage : {n} export(s) orphelin(s) supprimé(s).")
    except Exception as e:
        print(f"Nettoyage orphelins: {e}")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ping Pong Edit")

    if sys.platform.startswith("win"):
        # Sans cet identifiant, la barre des tâches Windows regroupe l'app sous l'icône
        # de Python/PyInstaller au lieu de la raquette.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("fr.eliotgourdoux.pingpongedit")
        except Exception:
            pass
    _icon = Path(__file__).parent / "assets" / "icon.png"
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))

    # Dock icon via PyObjC (only reliable API for macOS dock)
    try:
        from AppKit import (NSApplication, NSImage, NSColor, NSFont,
                            NSBezierPath, NSMutableAttributedString,
                            NSFontAttributeName)
        _nsapp = NSApplication.sharedApplication()

        # Prefer the pre-built .icns from the .app bundle next to this script
        _icns = PROJECT_DIR / "Ping Pong Edit.app" / "Contents" / "Resources" / "AppIcon.icns"
        if _icns.exists():
            _img = NSImage.alloc().initWithContentsOfFile_(str(_icns))
        else:
            # Fallback: render emoji live via AppKit
            from AppKit import NSRectFill
            _sz = 256
            _img = NSImage.alloc().initWithSize_((_sz, _sz))
            _img.lockFocus()
            NSColor.clearColor().setFill()
            NSRectFill(((0, 0), (_sz, _sz)))
            _s = NSMutableAttributedString.alloc().initWithString_attributes_(
                '🏓', {NSFontAttributeName: NSFont.systemFontOfSize_(_sz * 0.78)}
            )
            _s.drawAtPoint_((_sz * 0.08, _sz * 0.08))
            _img.unlockFocus()

        _nsapp.setApplicationIconImage_(_img)
    except Exception as _e:
        if sys.platform == "darwin":
            print(f"Dock icon: {_e}")

    # Thème : suit macOS (Dark / Light), y compris à chaud via System Settings →
    # Appearance. La palette doit être posée AVANT de construire la fenêtre.
    style.apply_palette(style.preferred_dark())

    win = MainWindow()

    def _on_color_scheme_changed(*_):
        if style.apply_palette(style.preferred_dark()):
            # Toutes les fenêtres principales ouvertes dans ce processus.
            for w in QApplication.topLevelWidgets():
                if isinstance(w, MainWindow):
                    w.apply_theme()

    try:
        app.styleHints().colorSchemeChanged.connect(_on_color_scheme_changed)
    except AttributeError:
        pass   # Qt < 6.5 : pas de suivi à chaud, thème fixé au lancement

    win.show()
    # Différé : la fenêtre apparaît ~150 ms plus tôt, le nettoyage se fait juste après.
    QTimer.singleShot(0, _deferred_orphan_cleanup)
    from pongedit import update_ui
    update_ui.start_background_check(win)
    if sys.platform.startswith("win"):
        import threading
        from pongedit import winshell
        threading.Thread(target=winshell.refresh_shortcut_icons, daemon=True).start()
    sys.exit(app.exec())
