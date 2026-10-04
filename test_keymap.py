"""Tests des raccourcis indépendants de la disposition (headless)."""
import os, sys, unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from pongedit import keymap

_app = QApplication.instance() or QApplication(sys.argv)


def ev(qt_key, sc=0):
    return QKeyEvent(QEvent.Type.KeyPress, qt_key, Qt.KeyboardModifier.NoModifier, sc, 0, 0, "")


def code(logical):
    return keymap._PHYS[logical][keymap._COL]


class T(unittest.TestCase):
    def setUp(self):
        self._f = keymap._family

    def tearDown(self):
        keymap._family = self._f

    def test_qwerty_inchange(self):
        keymap._family = "qwerty"
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_A)), "A")
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Z)), "Z")
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_BracketLeft)), "[")
        self.assertEqual(keymap.label("A"), "A")

    def test_azerty_par_code_materiel(self):
        keymap._family = "azerty"
        # le code 0 (touche A sur macOS) est indiscernable d'un code absent : couvert par le
        # test « sans code » ci-dessous
        for k in ("A", "S", "C", "F", "R", "I", "N", "Z", "M", "[", "]"):
            if code(k):
                self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Exclam, code(k))), k)

    def test_azerty_par_caractere_sans_code(self):
        keymap._family = "azerty"
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Q)), "A")      # emplacement du A
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_S)), "S")
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_W)), "Z")      # annuler
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Comma)), "M")  # muet
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Dead_Circumflex)), "[")
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Dollar)), "]")

    def test_azerty_lettre_orpheline_ne_declenche_rien(self):
        keymap._family = "azerty"
        # la vraie lettre A (haut gauche d'un AZERTY) ne doit PAS marquer un point
        self.assertIsNone(keymap.logical_key(ev(Qt.Key.Key_A)))
        self.assertIsNone(keymap.logical_key(ev(Qt.Key.Key_Z)))
        self.assertIsNone(keymap.logical_key(ev(Qt.Key.Key_M)))

    def test_etiquettes(self):
        keymap._family = "azerty"
        self.assertEqual([keymap.label(k) for k in "A S Z M [ ]".split()], ["Q", "S", "W", ",", "^", "$"])
        keymap._family = "qwertz"
        self.assertEqual(keymap.label("Z"), "Y")
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Y)), "Z")

    def test_toutes_les_touches_ont_un_emplacement_unique(self):
        for col in (0, 1, 2):
            codes = [v[col] for v in keymap._PHYS.values()]
            self.assertEqual(len(codes), len(set(codes)))


if __name__ == "__main__":
    unittest.main()
