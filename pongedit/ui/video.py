"""Conteneur vidéo avec incrustations."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QSizePolicy, QFrame, QGraphicsView,
    QGraphicsScene,
)
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter

# Incrustations sur la vidéo : palette SOMBRE figée, quel que soit le thème de
# l'interface (fond vidéo noir, et cohérence avec les vignettes exportées).
from pongedit.ui.style import UI_DARK as UI


# ── Video container with overlays ─────────────────────────────────────────────

class VideoContainer(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: black;")

        # Vue graphique plutôt qu'un QVideoWidget : elle sait pivoter l'image, donc
        # l'aperçu montre les rotations exactement comme le montage final.
        self.video_view = QGraphicsView(self)
        self.video_view.setScene(QGraphicsScene(self))
        self.video_view.setFrameShape(QFrame.Shape.NoFrame)
        self.video_view.setStyleSheet("background:black;border:none;")
        self.video_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.video_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.video_view.setRenderHints(QPainter.SmoothPixmapTransform)
        # Repeindre tout le viewport : sinon l'image animée laisse des traînées.
        self.video_view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        # La vue ne doit ni prendre le focus ni avaler les clics : les raccourcis
        # clavier et le clic sur la vidéo restent gérés par la fenêtre.
        self.video_view.setFocusPolicy(Qt.NoFocus)
        self.video_view.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.video_item = QGraphicsVideoItem()
        self.video_view.scene().addItem(self.video_item)
        self.video_widget = self.video_view      # ancien nom, conservé
        self._preview_rotation = 0
        self.video_item.nativeSizeChanged.connect(lambda *_: self._layout_video())

        # Score HUD — two stacked labels
        self.hud = QWidget(self)
        self.hud.setAttribute(Qt.WA_TransparentForMouseEvents)
        # Look « broadcast » cohérent avec la vignette exportée : fond sombre
        # 85 %, bandes couleur joueur de chaque côté, chiffres condensés.
        self.hud.setStyleSheet(
            f"background: rgba(10,12,18,217); border-radius: 6px;"
            f"border-left: 5px solid {UI['p1']}; border-right: 5px solid {UI['p2']};"
        )
        hud_v = QVBoxLayout(self.hud)
        hud_v.setContentsMargins(14, 5, 14, 5)
        hud_v.setSpacing(0)

        self.hud_cur = QLabel("0  —  0")
        self.hud_cur.setAlignment(Qt.AlignCenter)
        self.hud_cur.setStyleSheet(
            "color:white; font-size:20px; font-weight:800; letter-spacing:1px;"
            "background:transparent; border:none;"
        )
        self.hud_sets = QLabel("")
        self.hud_sets.setAlignment(Qt.AlignCenter)
        self.hud_sets.setStyleSheet(
            f"color:{UI['muted']}; font-size:11px; letter-spacing:1px;"
            "background:transparent; border:none;"
        )
        hud_v.addWidget(self.hud_cur)
        hud_v.addWidget(self.hud_sets)

        # Flash overlay
        self.flash = QWidget(self)
        self.flash.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.flash.hide()
        self._flash_timer = QTimer(singleShot=True)
        self._flash_timer.timeout.connect(self.flash.hide)

        # Cut overlay
        self.cut_lbl = QLabel("✂  COUPE EN COURS  ·  2×  ·  relâcher C pour terminer", self)
        self.cut_lbl.setAlignment(Qt.AlignCenter)
        self.cut_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.cut_lbl.setStyleSheet(
            "background:rgba(180,0,0,110); color:white;"
            "font-size:20px; font-weight:bold; letter-spacing:1px;"
        )
        self.cut_lbl.hide()

        # Bandeau du mode highlight (bas de l'image)
        self.hl_lbl = QLabel("", self)
        self.hl_lbl.setAlignment(Qt.AlignCenter)
        self.hl_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hl_lbl.hide()
        self._hl_state: str | None = None
        self._style_hl_lbl(None)

    def _style_hl_lbl(self, state: str | None):
        edge = {"keep": UI["success"], "reject": UI["danger"]}.get(state or "", UI["accent"])
        self.hl_lbl.setStyleSheet(
            f"background: rgba(10,12,18,217); color: white; border-radius: 6px;"
            f"border-left: 5px solid {edge}; border-right: 5px solid {edge};"
            "font-size: 13px; font-weight: 700; letter-spacing: 1px; padding: 6px 14px;"
        )

    def set_highlight_hud(self, text: str | None, state: str | None = None):
        """Affiche (ou masque si `text` est None) le bandeau du mode highlight."""
        if not text:
            self.hl_lbl.hide()
            return
        if state != self._hl_state:
            self._hl_state = state
            self._style_hl_lbl(state)
        self.hl_lbl.setText(text)
        self._place_hl_lbl()
        self.hl_lbl.show()
        self.hl_lbl.raise_()

    def _place_hl_lbl(self):
        self.hl_lbl.adjustSize()
        r = self.rect()
        w = min(self.hl_lbl.sizeHint().width(), max(100, r.width() - 20))
        h = self.hl_lbl.sizeHint().height()
        self.hl_lbl.setGeometry((r.width() - w) // 2, r.height() - h - 10, w, h)

    def set_preview_rotation(self, angle: int):
        """Fait pivoter l'aperçu, dans le cadre d'origine, comme le fera l'export."""
        angle = int(angle) % 360
        if angle == self._preview_rotation:
            return
        self._preview_rotation = angle
        self._layout_video()

    def _layout_video(self):
        nat = self.video_item.nativeSize()
        if nat.isEmpty():
            return
        w, h = nat.width(), nat.height()
        self.video_item.setSize(nat)
        self.video_item.setPos(-w / 2, -h / 2)
        self.video_item.setTransformOriginPoint(w / 2, h / 2)
        self.video_item.setRotation(self._preview_rotation)
        # Le cadre de sortie ne change pas de format : un morceau pivoté est remis
        # à l'échelle dedans, bandes noires comprises, comme dans le filtre ffmpeg.
        self.video_item.setScale(min(w / h, h / w) if self._preview_rotation % 180 else 1.0)
        rect = QRectF(-w / 2, -h / 2, w, h)
        self.video_view.scene().setSceneRect(rect)
        self.video_view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        r = self.rect()
        self.video_view.setGeometry(r)
        self._layout_video()
        self.flash.setGeometry(r)
        self.cut_lbl.setGeometry(r)
        self.hud.adjustSize()
        hw = max(self.hud.sizeHint().width(), 260)
        hh = self.hud.sizeHint().height()
        self.hud.setGeometry((r.width() - hw) // 2, 8, hw, hh)
        if self.hl_lbl.isVisible():
            self._place_hl_lbl()

    def show_flash(self, player: int):
        color = "rgba(56,108,212,120)" if player == 1 else "rgba(222,104,66,120)"
        self.flash.setStyleSheet(f"background:{color};")
        self.flash.show()
        self._flash_timer.start(380)

    def set_cutting(self, cutting: bool):
        self.cut_lbl.setVisible(cutting)

    def update_score(self, p1n: str, p2n: str,
                     completed: list[tuple[int,int]], cur_p1: int, cur_p2: int):
        p1_sets = sum(1 for s in completed if s[0] > s[1])
        p2_sets = sum(1 for s in completed if s[1] > s[0])

        self.hud_cur.setText(
            f"<span style='color:{UI['p1']}'>{p1n.upper()}</span>"
            f"  <span style='color:white'>{cur_p1} — {cur_p2}</span>"
            f"  <span style='color:{UI['p2']}'>{p2n.upper()}</span>"
        )
        if completed:
            sets_txt = "  ·  ".join(f"{a}-{b}" for a, b in completed)
            self.hud_sets.setText(f"SETS {p1_sets}-{p2_sets}   {sets_txt}")
        else:
            self.hud_sets.setText("")

        # Resize HUD to fit content
        self.hud.adjustSize()
        hw = max(self.hud.sizeHint().width(), 260)
        hh = self.hud.sizeHint().height()
        r = self.rect()
        self.hud.setGeometry((r.width() - hw) // 2, 8, hw, hh)
