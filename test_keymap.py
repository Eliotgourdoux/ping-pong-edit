"""Tests des raccourcis (disposition clavier) et du focus des champs de saisie — headless."""
import os, sys, unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pongedit import keymap

_app = QApplication.instance() or QApplication(sys.argv)


def ev(qt_key, sc=0):
    return QKeyEvent(QEvent.Type.KeyPress, qt_key, Qt.KeyboardModifier.NoModifier, sc, 0, 0, "")


def code(logical):
    return keymap._PHYS[logical][keymap._COL]


class Clavier(unittest.TestCase):
    def setUp(self):
        self._f = keymap._family

    def tearDown(self):
        keymap._family = self._f

    def test_qwerty_inchange(self):
        keymap._family = "qwerty"
        for qk, lk in ((Qt.Key.Key_A, "A"), (Qt.Key.Key_S, "S"), (Qt.Key.Key_C, "C"),
                       (Qt.Key.Key_F, "F"), (Qt.Key.Key_Z, "Z"), (Qt.Key.Key_M, "M")):
            self.assertEqual(keymap.logical_key(ev(qk)), lk)
        self.assertEqual(keymap.label("A"), "A")

    def test_azerty_lettres_conservees(self):
        keymap._family = "azerty"
        # muet, annuler, service, rotation : des LETTRES, comme demandé
        for qk, lk in ((Qt.Key.Key_M, "M"), (Qt.Key.Key_Z, "Z"), (Qt.Key.Key_F, "F"),
                       (Qt.Key.Key_R, "R"), (Qt.Key.Key_I, "I"), (Qt.Key.Key_N, "N")):
            self.assertEqual(keymap.logical_key(ev(qk)), lk)

    def test_azerty_a_s_c_suivent_la_position(self):
        keymap._family = "azerty"
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Q)), "A")   # emplacement du A, sans code matériel
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_S)), "S")
        self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_C)), "C")
        for k in ("S", "C"):                                           # avec code matériel
            if code(k):
                self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Exclam, code(k))), k)
        if code("A"):
            self.assertEqual(keymap.logical_key(ev(Qt.Key.Key_Q, code("A"))), "A")

    def test_azerty_vraie_lettre_a_ne_declenche_rien(self):
        keymap._family = "azerty"
        self.assertIsNone(keymap.logical_key(ev(Qt.Key.Key_A)))        # haut gauche d'un AZERTY

    def test_etiquettes(self):
        keymap._family = "azerty"
        self.assertEqual([keymap.label(k) for k in "ASCFZM"], ["Q", "S", "C", "F", "Z", "M"])
        keymap._family = "qwertz"
        self.assertEqual([keymap.label(k) for k in "ASCFZM"], list("ASCFZM"))

    def test_codes_uniques(self):
        for col in (0, 1, 2):
            codes = [v[col] for v in keymap._PHYS.values()]
            self.assertEqual(len(codes), len(set(codes)))


class Focus(unittest.TestCase):
    """Entrée dans un champ : valide, rend le focus, et les raccourcis refonctionnent."""

    @classmethod
    def setUpClass(cls):
        from pongedit.ui.main_window import MainWindow
        cls.win = MainWindow()
        cls.win.show()
        cls.win.activateWindow()
        for _ in range(50):
            _app.processEvents()

    def test_entree_rend_le_focus(self):
        w = self.win
        if QApplication.activeWindow() is not w:
            self.skipTest("fenêtre non active en mode offscreen")
        field = w.scoreboard.p1_input if hasattr(w, "scoreboard") else w.export_name_input
        field.setFocus(); _app.processEvents()
        self.assertIs(QApplication.focusWidget(), field)
        field.setText("Jean")
        QTest.keyClick(field, Qt.Key.Key_Return); _app.processEvents()
        self.assertIsNot(QApplication.focusWidget(), field)
        self.assertEqual(field.text(), "Jean")                          # la saisie est conservée

    def test_clic_dehors_libere_le_focus(self):
        w = self.win
        if QApplication.activeWindow() is not w:
            self.skipTest("fenêtre non active en mode offscreen")
        field = w.export_name_input
        field.setFocus(); _app.processEvents()
        QTest.mouseClick(w.version_btn.parentWidget() or w, Qt.MouseButton.LeftButton)
        _app.processEvents()
        self.assertIsNot(QApplication.focusWidget(), field)


if __name__ == "__main__":
    unittest.main(verbosity=1)
