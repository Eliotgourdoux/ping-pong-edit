"""Liste d'actions : lignes à deux niveaux (titre + détail) avec pastille de couleur."""

from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from pongedit.ui.style import UI

# Rôles de données d'une ligne
ROLE_KIND, ROLE_TITLE, ROLE_SUB, ROLE_TIME = (
    Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 2,
    Qt.ItemDataRole.UserRole + 3, Qt.ItemDataRole.UserRole + 4)

_KIND_COLOR = {"p1": "p1", "p2": "p2", "cut": "danger", "rot": "rot", "swap": "muted"}
_KIND_GLYPH = {"cut": "✂", "rot": "↻", "swap": "⇄"}


class ActionDelegate(QStyledItemDelegate):
    ROW_H = 46

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        kind = index.data(ROLE_KIND) or "swap"
        col = QColor(UI[_KIND_COLOR.get(kind, "muted")])
        if option.state & QStyle.State_Selected:
            p.setPen(Qt.NoPen); p.setBrush(QColor(UI["selected"])); p.drawRoundedRect(r, 8, 8)
            p.setPen(QPen(col, 1)); p.setBrush(Qt.NoBrush); p.drawRoundedRect(r.adjusted(.5, .5, -.5, -.5), 8, 8)
        elif option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen); p.setBrush(QColor(UI["surface"])); p.drawRoundedRect(r, 8, 8)

        # Pastille : disque plein pour un point, glyphe pour les autres actions
        cx, cy = r.left() + 20, r.center().y()
        glyph = _KIND_GLYPH.get(kind)
        if glyph:
            p.setPen(col); f = QFont(p.font()); f.setPixelSize(15); p.setFont(f)
            p.drawText(QRectF(cx - 10, cy - 10, 20, 20), Qt.AlignCenter, glyph)
        else:
            p.setPen(Qt.NoPen); p.setBrush(col); p.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))

        # Temps à droite (mono)
        tfont = QFont("Menlo"); tfont.setPixelSize(11); tfont.setStyleHint(QFont.Monospace)
        p.setFont(tfont); p.setPen(QColor(UI["muted"]))
        tr = QRectF(r.left() + 36, r.top(), r.width() - 48, r.height())
        p.drawText(tr, Qt.AlignRight | Qt.AlignVCenter, index.data(ROLE_TIME) or "")

        # Titre + détail à gauche
        tw = r.width() - 36 - 12 - 92
        title_f = QFont(option.font); title_f.setPixelSize(13); title_f.setWeight(QFont.DemiBold)
        p.setFont(title_f); p.setPen(QColor(UI["text"]))
        p.drawText(QRectF(r.left() + 36, r.top() + 5, tw, 19), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(index.data(ROLE_TITLE) or "", Qt.ElideRight, int(tw)))
        sub_f = QFont(title_f); sub_f.setPixelSize(11); sub_f.setWeight(QFont.Normal)
        p.setFont(sub_f); p.setPen(QColor(UI["dim"]))
        p.drawText(QRectF(r.left() + 36, r.top() + 23, r.width() - 48, 17), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(index.data(ROLE_SUB) or "", Qt.ElideRight, int(r.width() - 48)))
        p.restore()
