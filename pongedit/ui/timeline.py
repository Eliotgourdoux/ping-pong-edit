"""Widget timeline."""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPixmap

from pongedit.utils import fmt_time
from pongedit.match.actions import Action, CutAction, PointAction, RotateAction, ServeSwapAction
from pongedit.ui.style import UI, themed


# ── Timeline ──────────────────────────────────────────────────────────────────

class TimelineWidget(QWidget):
    seek_requested = Signal(float)  # emits timecode in seconds

    def __init__(self):
        super().__init__()
        self.setFixedHeight(64)
        themed(self, lambda: f"background: {UI['surface']};")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Timeline — cliquer ou glisser pour se déplacer  ·  pastilles = points  ·  "
                        "zones rouges = coupes  ·  barre verte = highlight gardé  ·  hachuré = rejeté")
        self.setMouseTracking(True)
        self._hover_x: int | None = None
        self.actions: list[Action] = []
        self.duration = 0.0
        self.current_time = 0.0
        self.live_cut_start: float | None = None
        self.highlights: list[tuple[float, float, bool]] = []   # (start, end, gardé ?)
        self.review_span: tuple[float, float] | None = None     # séquence en cours de revue
        self._bg_cache: QPixmap | None = None   # fond statique mémorisé
        self._bg_sig = None                     # signature d'invalidation

    def _t_from_x(self, x: int) -> float:
        if not self.duration: return 0.0
        return max(0.0, min(self.duration, x / self.width() * self.duration))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.seek_requested.emit(self._t_from_x(e.position().x()))

    def mouseMoveEvent(self, e):
        self._hover_x = int(e.position().x())
        if e.buttons() & Qt.LeftButton:
            self.seek_requested.emit(self._t_from_x(e.position().x()))
        else:
            self.update()

    def leaveEvent(self, e):
        self._hover_x = None
        self.update()
        super().leaveEvent(e)

    def refresh(self, actions, duration, current_time, live_cut_start=None,
                highlights=None, review_span=None):
        # Le fond (coupes + pastilles de points) ne change QUE si les actions ou la
        # durée changent — pas à chaque frame de lecture. On invalide donc son cache
        # uniquement ici, en comparant une signature légère.
        highlights = list(highlights or [])
        sig = (len(actions), duration,
               tuple((a.start, a.end) for a in actions if isinstance(a, CutAction)),
               tuple((a.start, a.end, a.angle) for a in actions if isinstance(a, RotateAction)),
               tuple((a.timecode, a.player) for a in actions if isinstance(a, PointAction)),
               tuple(a.timecode for a in actions if isinstance(a, ServeSwapAction)),
               tuple(highlights))
        if sig != self._bg_sig:
            self._bg_sig = sig
            self._bg_cache = None
        self.actions = actions
        self.duration = duration
        self.current_time = current_time
        self.live_cut_start = live_cut_start
        self.highlights = highlights
        self.review_span = review_span
        self.update()

    def theme_changed(self):
        """Thème système modifié : le fond mémorisé est à redessiner."""
        self._bg_cache = None
        self.update()

    def resizeEvent(self, e):
        self._bg_cache = None   # la géométrie change => fond à redessiner
        super().resizeEvent(e)

    def _build_bg(self) -> QPixmap:
        """Dessine la partie STATIQUE (fond + coupes + points) dans un pixmap réutilisé.
        Avant, ces ~190 primitives étaient redessinées 10-25 fois/s pendant la lecture."""
        w, h = max(1, self.width()), max(1, self.height())
        # Le pixmap doit être créé en pixels PHYSIQUES (× ratio Retina) puis marqué avec
        # son ratio. Sinon, sur un écran Retina (ratio 2), sa taille logique vaut w/2 et
        # le fond n'est dessiné que sur la moitié gauche de la timeline.
        dpr = self.devicePixelRatioF()
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(UI["surface"]))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        # Ligne médiane discrète : donne un repère même sur une vidéo sans action.
        p.fillRect(0, h // 2 - 1, w, 2, QColor(UI["tl_mid"]))
        for a in self.actions:
            if isinstance(a, CutAction):
                x1, x2 = self._x(a.start), self._x(a.end)
                p.fillRect(x1, 0, max(2, x2 - x1), h, QColor(UI["tl_cut"]))
        # Tri highlight : séquence rejetée = voile sombre hachuré, gardée = barre
        # verte fine en haut. Les séquences « à trier » restent telles quelles.
        for s, e, keep in self.highlights:
            x1, x2 = self._x(s), self._x(e)
            bw = max(2, x2 - x1)
            if keep:
                p.fillRect(x1, 0, bw, 3, QColor(UI["success"]))
            else:
                p.fillRect(x1, 0, bw, h, QColor(UI["tl_reject"]))
                hatch = QBrush(QColor(UI["tl_hatch"]), Qt.BDiagPattern)
                p.fillRect(x1, 0, bw, h, hatch)
        # Les rotations ne retirent rien de la vidéo : bandeau fin en haut, pour
        # qu'elles se lisent sans masquer les coupes ni les pastilles de points.
        for a in self.actions:
            if isinstance(a, RotateAction):
                x1, x2 = self._x(a.start), self._x(a.end)
                p.fillRect(x1, 0, max(2, x2 - x1), 5, QColor(UI["rot"]))
        # Inversion forcée du service (F) : trait fin pleine hauteur, couleur discrète.
        for a in self.actions:
            if isinstance(a, ServeSwapAction):
                p.fillRect(self._x(a.timecode) - 1, 0, 2, h, QColor(UI["muted"]))
        p.setPen(Qt.NoPen)
        for a in self.actions:
            if isinstance(a, PointAction):
                x = self._x(a.timecode)
                p.setBrush(QBrush(QColor(UI["p1"]) if a.player == 1 else QColor(UI["p2"])))
                p.drawRoundedRect(x - 2, h // 2 - 13, 4, 26, 2, 2)
        p.end()
        return pm

    def _x(self, t: float) -> int:
        return int(t / self.duration * self.width()) if self.duration else 0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # Fond statique (coupes + points) : dessiné une fois, puis simple blit.
        # On revalide aussi le ratio/dimension : déplacer la fenêtre d'un écran Retina
        # vers un écran standard change le devicePixelRatio.
        _dpr = self.devicePixelRatioF()
        if (self._bg_cache is None
                or self._bg_cache.devicePixelRatio() != _dpr
                or self._bg_cache.width() != int(w * _dpr)):
            self._bg_cache = self._build_bg()
        p.drawPixmap(0, 0, self._bg_cache)
        # Progression déjà lue (léger surlignage à gauche du curseur)
        played_x = self._x(self.current_time)
        p.fillRect(0, 0, played_x, h, QColor(UI["tl_played"]))
        if self.live_cut_start is not None:
            x1, x2 = self._x(self.live_cut_start), self._x(self.current_time)
            p.fillRect(x1, 0, max(2, x2 - x1), h, QColor(UI["tl_live_cut"]))
        # Séquence en cours de revue (mode highlight) : contour accent
        if self.review_span is not None and self.duration:
            x1, x2 = self._x(self.review_span[0]), self._x(self.review_span[1])
            p.setPen(QPen(QColor(UI["accent"]), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(x1 + 0.75, 0.75, max(2, x2 - x1) - 1.5, h - 1.5))
        # Position survolée (aperçu du seek)
        if self._hover_x is not None and self.duration:
            p.setPen(QPen(QColor(UI["tl_hover"]), 1))
            p.drawLine(self._hover_x, 0, self._hover_x, h)
        # Tête de lecture : trait accent + petite tête en haut
        x = self._x(self.current_time)
        p.setPen(QPen(QColor(UI["accent"]), 2))
        p.drawLine(x, 0, x, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(UI["accent"])))
        p.drawRoundedRect(x - 5, 0, 10, 7, 3, 3)
        # Timecode courant / durée — police mono, fond lisible
        font = QFont("Menlo", 10)
        font.setStyleHint(QFont.Monospace)
        p.setFont(font)
        label = f"{fmt_time(self.current_time)} / {fmt_time(self.duration)}"
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(label)
        pad = 6
        bx = w - tw - pad * 2 - 6
        by = h - fm.height() - 6
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(UI["tl_tc_bg"])))
        p.drawRoundedRect(bx, by, tw + pad * 2, fm.height() + 4, 4, 4)
        p.setPen(QColor(UI["tl_tc_fg"]))
        p.drawText(bx + pad, by + 2 + fm.ascent(), label)
