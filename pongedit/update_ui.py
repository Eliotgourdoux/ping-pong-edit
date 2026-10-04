"""Interface de la mise à jour : vérification au démarrage, bouton « version », redémarrage."""

import os
import subprocess
import sys
import threading

from PySide6.QtCore import QTimer
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
