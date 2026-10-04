"""Interface de la mise à jour : vérification au démarrage, bouton « version », redémarrage."""

import os
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from pongedit import updater
from pongedit.version import VERSION


def restart():
    """Relance l'app (même slot de fenêtre) puis ferme celle-ci."""
    from pongedit.utils import INSTANCE_SLOT
    cmd = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(sys.argv[0])]
    cmd += ["--slot", str(INSTANCE_SLOT)]
    QApplication.instance().aboutToQuit.connect(lambda: subprocess.Popen(cmd, start_new_session=True))
    QApplication.quit()


def _ask_restart(win, version):
    box = QMessageBox(win)
    box.setWindowTitle("Mise à jour")
    box.setIcon(QMessageBox.Information)
    box.setText(f"La version {version} est prête.")
    box.setInformativeText("Redémarrer maintenant pour l'utiliser ?\n"
                           "(Sinon, elle sera utilisée au prochain lancement.)")
    if updater.LAST_NOTES:
        box.setDetailedText(updater.LAST_NOTES)      # bouton « Afficher les détails… »
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.Yes)
    box.button(QMessageBox.Yes).setText("Redémarrer")
    box.button(QMessageBox.No).setText("Plus tard")
    box.finished.connect(lambda r: restart() if r == QMessageBox.Yes else None)
    box.open()


def _run_check(win, force: bool, on_done):
    """Vérifie dans un thread (réseau) et rappelle `on_done(version, état)` dans l'interface."""
    result = {}

    def work():
        result["v"] = updater.check_and_download(force=force)
        result["state"] = updater.LAST_STATE

    t = threading.Thread(target=work, daemon=True)
    t.start()
    timer = QTimer(win)

    def poll():
        if t.is_alive():
            return
        timer.stop()
        on_done(result.get("v"), result.get("state"))

    timer.timeout.connect(poll)
    timer.start(300)


def start_background_check(win):
    """Au démarrage : silencieux, sauf si une mise à jour vient d'être téléchargée."""
    _run_check(win, False, lambda v, st: _ask_restart(win, v) if v else None)


def manual_check(win, button=None):
    """Bouton « vX.Y.Z » : vérifie maintenant et répond dans tous les cas."""
    if button is not None:
        button.setEnabled(False)

    def done(v, state):
        if button is not None:
            button.setEnabled(True)
        if v:
            _ask_restart(win, v)
        elif state == "uptodate":
            QMessageBox.information(win, "Mise à jour",
                                    f"Vous avez déjà la dernière version (v{VERSION}).")
        else:
            QMessageBox.warning(win, "Mise à jour",
                                "Impossible de vérifier les mises à jour (pas de connexion, "
                                "ou GitHub ne répond pas).\nRéessayez plus tard.")

    _run_check(win, True, done)


# ── Menu de la version : À propos, notes, vérification ────────────────────────

AUTHOR = "Eliot Gourdoux"
REPO_URL = "https://github.com/Eliotgourdoux/ping-pong-edit-releases"


def _asset(name: str) -> str:
    from pathlib import Path
    p = Path(__file__).parent / "assets" / name
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _changelog_section(version: str) -> str:
    """Notes de la version `version` d'après CHANGELOG.md ('' si absentes)."""
    out, take = [], False
    for line in _asset("CHANGELOG.md").splitlines():
        if line.startswith("## "):
            if take:
                break
            take = line[3:].split("—")[0].strip() == version
            continue
        if take:
            out.append(line)
    return "\n".join(out).strip()


def show_about(win):
    box = QMessageBox(win)
    box.setWindowTitle("À propos")
    box.setIcon(QMessageBox.NoIcon)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(
        f"<h3>Ping Pong Edit 🏓</h3>"
        f"<p>Version {VERSION}</p>"
        f"<p>Créé par <b>{AUTHOR}</b><br>© 2026 {AUTHOR}. Tous droits réservés.</p>"
        f"<p><a href=\"{REPO_URL}\">{REPO_URL}</a></p>"
    )
    box.setDetailedText(_asset("LICENSE.txt"))
    box.exec()


def show_notes(win):
    notes = _changelog_section(VERSION) or "Aucune note pour cette version."
    box = QMessageBox(win)
    box.setWindowTitle(f"Nouveautés de la version {VERSION}")
    box.setIcon(QMessageBox.NoIcon)
    box.setTextFormat(Qt.TextFormat.MarkdownText)
    box.setText(f"### Version {VERSION}\n\n{notes}")
    box.exec()


def version_menu(win, button):
    """Menu ouvert par le bouton « vX.Y.Z »."""
    from PySide6.QtWidgets import QMenu
    m = QMenu(win)
    m.addAction("À propos de Ping Pong Edit…", lambda: show_about(win))
    m.addAction("Notes de cette version…", lambda: show_notes(win))
    m.addSeparator()
    m.addAction("Vérifier les mises à jour", lambda: manual_check(win, button))
    m.exec(button.mapToGlobal(button.rect().bottomLeft()))
