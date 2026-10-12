"""Finitions : survol des boutons (fondu 0,15 s), entrée douce unique (<= 0,34 s).

Règles : aucune animation en boucle ; l'effet d'opacité de l'entrée est retiré à la fin (aucun
QGraphicsEffect permanent, jamais sur la vidéo ni la timeline) ; Reduce Motion de macOS
respecté (aucune animation) ; rien ne coûte pendant la lecture.
"""

import sys

from PySide6.QtCore import Qt, QRectF, QTimer, QVariantAnimation, QEasingCurve, QPointF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QPushButton, QGraphicsOpacityEffect, QComboBox, QStylePainter,
                               QStyleOptionComboBox, QStyle)


def reduce_motion() -> bool:
    if sys.platform == "darwin":
        try:
            from AppKit import NSWorkspace
            return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
        except Exception:
            return False
    return False


class FadeButton(QPushButton):
    """QPushButton identique + voile clair de survol qui s'installe en 0,15 s. Un seul bouton
    est repeint par image, jamais en boucle."""

    _VARIANTS = {"toolbar": 17, "ghost": 14, "danger": 14, "primary": 17}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_t)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _on_t(self, v):
        self._t = float(v); self.update()

    def _go(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._t); self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        if self.isEnabled() and not reduce_motion():
            self._go(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._t > 0:
            self._go(0.0)
        super().leaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        rad = self._VARIANTS.get(self.property("variant"))
        if rad is None or self._t <= 0.01 or self.isDown():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(255, 255, 255, int(16 * self._t))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(c)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = min(rad, r.height() / 2)
        p.drawRoundedRect(r, rad, rad)


def play_entrance(groups, dy: int = 14, duration: int = 340, stagger: int = 30):
    """Fondu + glissement de 14 px (<= 0,34 s, courbe douce) sur 2-3 groupes, lancé après le
    premier rendu. Reduce Motion : aucune animation. Renvoie les animations (à garder en vie)."""
    if reduce_motion():
        return []
    anims = []
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.2, 0.8), QPointF(0.2, 1.0), QPointF(1.0, 1.0))
    for i, w in enumerate(groups):
        eff = QGraphicsOpacityEffect(w); eff.setOpacity(0.0)
        w.setGraphicsEffect(eff)
        end_pos = w.pos()
        a = QVariantAnimation(w)
        a.setDuration(duration - i * stagger)
        a.setEasingCurve(curve)
        a.setStartValue(0.0); a.setEndValue(1.0)

        def step(v, w=w, eff=eff, end_pos=end_pos):
            eff.setOpacity(float(v))
            w.move(end_pos.x(), end_pos.y() + int(round(dy * (1 - float(v)))))

        def done(w=w, end_pos=end_pos):
            w.move(end_pos)
            w.setGraphicsEffect(None)

        a.valueChanged.connect(step)
        a.finished.connect(done)
        w.move(end_pos.x(), end_pos.y() + dy)
        QTimer.singleShot(i * stagger, a.start)
        anims.append(a)
    return anims


class ElidedCombo(QComboBox):
    """QComboBox identique, mais le texte affiché trop long finit par « … » au lieu d'être
    coupé net (le contenu de la liste n'est pas modifié)."""

    def paintEvent(self, _):
        p = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        r = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt,
                                        QStyle.SubControl.SC_ComboBoxEditField, self)
        opt.currentText = self.fontMetrics().elidedText(opt.currentText, Qt.TextElideMode.ElideMiddle,
                                                        max(0, r.width() - 4))
        p.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)
