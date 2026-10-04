"""Fenêtre principale."""

import math, os, sys, tempfile, shutil, re, json, time
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QListWidget, QListWidgetItem, QDoubleSpinBox,
    QLineEdit, QProgressBar, QComboBox, QSizePolicy, QFrame, QDialog,
    QScrollArea, QCheckBox, QSplitter, QStackedWidget, QAbstractSpinBox, QTextEdit,
    QPlainTextEdit,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtCore import (
    Qt, QUrl, QTimer, QEvent, QObject, QSize, QRectF, QPointF,
    QVariantAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QColor, QPixmap, QPainter, QPen, QBrush, QFont, QFontMetrics, QPainterPath,
    QLinearGradient, QRadialGradient, QIcon,
)

from pongedit.utils import (
    EXPORTS_DIR, INSTANCE_SLOT, SESSIONS_DIR, _atomic_write_text, _video_hash, _reserve_output_path,
    _fmt_chapter_time, fmt_time, gen_id,
)
from pongedit.app_state import (
    RECENT_MAX, _merge_recent, _read_shared_recent, _read_state,
    _spawn_new_window, _write_state, _read_intro_memory, _remember_intro,
    _read_export_prefs, _write_export_prefs,
)
from pongedit.match.actions import (
    Action, CutAction, PointAction, ROT_LABELS, RotateAction, ServeSwapAction,
    action_time, sort_actions,
)
from pongedit.match.scoring import (_compute_stats_from_dicts, compute_sets, flips_before,
                                    recompute_point_fields, server_at)
from pongedit.match.highlights import HL_TOL, _highlight_counts, _highlight_status, _match_highlight
from pongedit.export.segments import (
    _build_kept_segments, _kept_duration, _merge_overlapping_spans,
    _youtube_chapters_from_actions,
)
from pongedit.export.worker import ExportWorker
from pongedit.export import intro as _intro
from pongedit.ball.trail import TRAIL_LABELS, TrailWorker
from pongedit.ui.style import UI, _card, _field_label, _sep, themed, restyle_registered, is_dark, apply_palette, save_theme_choice
from pongedit.ui.action_list import ActionDelegate, ROLE_KIND, ROLE_TITLE, ROLE_SUB, ROLE_TIME
from pongedit.ui.timeline import TimelineWidget
from pongedit.ui.scoreboard import ScoreboardWidget
from pongedit.ui.video import VideoContainer
from pongedit.ui.dialogs import IntroCardDialog, MergeDialog


# ── Ligne d'option à interrupteur ─────────────────────────────────────────────

class ToggleRow(QCheckBox):
    """Option d'export sous forme de ligne pleine largeur : pastille, titre,
    sous-titre discret et interrupteur façon iOS à droite.

    Reste un vrai QCheckBox (isChecked / setChecked / toggled, Espace au clavier) :
    seul le dessin change. Les couleurs sont lues dans `UI` à chaque peinture,
    le thème clair / sombre suit donc sans rien ré-appliquer.

    `icon` : fonction qui dessine le glyphe de la pastille, appelée avec
    (painter, tile: QRectF, t: 0→1, dark: bool) — cf. les `icon_*` ci-dessous.
    `on_context` : appelé au clic droit (ex. statistiques détaillées)."""

    H = 54

    def __init__(self, title: str, subtitle: str = "", parent=None, *,
                 icon=None, on_context=None):
        super().__init__(title, parent)
        self._title = title
        self._subtitle = subtitle
        self._icon = icon or ToggleRow.icon_comet
        if on_context is not None:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(lambda _pos: on_context())
        self._t = 0.0                      # position du bouton : 0 = OFF, 1 = ON
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        # Tab : anneau de focus ; un clic ne vole pas le focus (pas d'anneau parasite).
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.H)
        # Le style macOS élargit le « rect de layout » des QCheckBox pour y loger
        # son indicateur natif : nos lignes débordaient sur l'espacement et se
        # collaient. On veut le rect du widget tel quel.
        self.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)

    # API
    def setSubtitle(self, text: str):
        if text != self._subtitle:
            self._subtitle = text
            self.setAccessibleDescription(text)
            self.update()

    def subtitle(self) -> str:
        return self._subtitle

    # Géométrie : toute la ligne est cliquable.
    def sizeHint(self):
        return QSize(220, self.H)

    def minimumSizeHint(self):
        return QSize(160, self.H)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    # Animation : seulement sur action de l'utilisateur ; un setChecked()
    # programmatique (préférences au lancement) se cale sans glisser.
    def nextCheckState(self):
        super().nextCheckState()
        self._anim.stop()
        self._anim.setStartValue(float(self._t))
        self._anim.setEndValue(1.0 if self.isChecked() else 0.0)
        self._anim.start()

    def _on_anim(self, v):
        self._t = float(v)
        self.update()

    def keyPressEvent(self, e):
        # Entrée coche aussi (Espace : comportement natif du QCheckBox).
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.isAutoRepeat():
            self.click(); return
        super().keyPressEvent(e)

    def enterEvent(self, e):
        super().enterEvent(e); self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e); self.update()

    @staticmethod
    def _mix(a: QColor, b: QColor, k: float) -> QColor:
        """a → b, k ∈ [0, 1]."""
        return QColor(round(a.red()   + (b.red()   - a.red())   * k),
                      round(a.green() + (b.green() - a.green()) * k),
                      round(a.blue()  + (b.blue()  - a.blue())  * k))

    # ── Glyphes des pastilles ─────────────────────────────────────────────────
    # Même langage partout : encre neutre (faint) à OFF qui glisse vers
    # l'accent à ON, un élément « balle » plein (blanc en sombre, accent en
    # clair) et un halo d'accent qui n'apparaît qu'à ON.

    @staticmethod
    def _ink(t: float) -> QColor:
        return ToggleRow._mix(QColor(UI["faint"]), QColor(UI["accent"]), t)

    @staticmethod
    def _ball(t: float, dark: bool) -> QColor:
        return ToggleRow._mix(QColor(UI["muted"]),
                              QColor("#FFFFFF") if dark else QColor(UI["accent"]), t)

    @staticmethod
    def _halo(p: QPainter, c: QPointF, r: float, t: float):
        if t <= 0.01:
            return
        halo = QRadialGradient(c, r)
        h0 = QColor(UI["accent"]); h0.setAlpha(int(90 * t))
        h1 = QColor(UI["accent"]); h1.setAlpha(0)
        halo.setColorAt(0.0, h0); halo.setColorAt(1.0, h1)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, r, r)

    @staticmethod
    def icon_comet(p: QPainter, tile: QRectF, t: float, dark: bool):
        """Traînée de balle : balle + fuseau effilé vers le bas-gauche."""
        accent = QColor(UI["accent"])
        ink = ToggleRow._ink(t)
        ball_c = QPointF(tile.right() - 11, tile.top() + 11)
        ball_r = 4.6
        tail_end = QPointF(tile.left() + 6, tile.bottom() - 6)
        dx, dy = ball_c.x() - tail_end.x(), ball_c.y() - tail_end.y()
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        nx, ny = -dy / n, dx / n                       # normale
        path = QPainterPath()
        path.moveTo(ball_c.x() + nx * ball_r, ball_c.y() + ny * ball_r)
        path.lineTo(tail_end)
        path.lineTo(ball_c.x() - nx * ball_r, ball_c.y() - ny * ball_r)
        path.closeSubpath()
        grad = QLinearGradient(ball_c, tail_end)
        c0 = QColor(ink); c0.setAlpha(200)
        c1 = QColor(ink); c1.setAlpha(0)
        grad.setColorAt(0.0, c0); grad.setColorAt(1.0, c1)
        p.setBrush(QBrush(grad))
        p.drawPath(path)
        ToggleRow._halo(p, ball_c, ball_r * 2.4, t)
        p.setBrush(ToggleRow._ball(t, dark))
        p.drawEllipse(ball_c, ball_r, ball_r)

    @staticmethod
    def icon_podium(p: QPainter, tile: QRectF, t: float, dark: bool):
        """Tableau de fin : trois barres de stats, la balle posée sur la plus haute."""
        ink = ToggleRow._ink(t)
        bw, gap = 5.0, 2.5
        x0 = tile.center().x() - (3 * bw + 2 * gap) / 2
        base = tile.bottom() - 7
        for i, (h, a) in enumerate(((8.0, 120), (14.0, 220), (11.0, 165))):
            r = QRectF(x0 + i * (bw + gap), base - h, bw, h)
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            c0 = QColor(ink); c0.setAlpha(a)
            c1 = QColor(ink); c1.setAlpha(int(a * 0.35))
            grad.setColorAt(0.0, c0); grad.setColorAt(1.0, c1)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(r, 1.6, 1.6)
        ball_c = QPointF(x0 + bw + gap + bw / 2, base - 14.0 - 4.6)
        ToggleRow._halo(p, ball_c, 8.0, t)
        p.setBrush(ToggleRow._ball(t, dark))
        p.drawEllipse(ball_c, 3.0, 3.0)

    @staticmethod
    def icon_intro(p: QPainter, tile: QRectF, t: float, dark: bool):
        """Intro : bouton lecture dans un anneau qui s'ouvre (le match démarre)."""
        ink = ToggleRow._ink(t)
        c = tile.center()
        ring = QColor(ink); ring.setAlpha(170)
        pen = QPen(ring, 1.6); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rr = QRectF(c.x() - 9.5, c.y() - 9.5, 19, 19)
        p.drawArc(rr, 70 * 16, 300 * 16)               # anneau ouvert en haut à droite
        p.setPen(Qt.PenStyle.NoPen)
        ToggleRow._halo(p, c, 10.5, t)
        tri = QPainterPath()
        tri.moveTo(c.x() - 3.2, c.y() - 5.0)
        tri.lineTo(c.x() + 5.2, c.y())
        tri.lineTo(c.x() - 3.2, c.y() + 5.0)
        tri.closeSubpath()
        p.setBrush(ToggleRow._ball(t, dark))
        p.drawPath(tri)

    def paintEvent(self, _e):
        if self._anim.state() != QVariantAnimation.State.Running:
            self._t = 1.0 if self.isChecked() else 0.0
        t = self._t
        on = self.isChecked()
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not enabled:
            p.setOpacity(0.5)

        accent  = QColor(UI["accent"])
        surface = QColor(UI["surface"])
        dark = is_dark()

        # ── Fond et bord de la ligne ──
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        base = QColor(UI["hover"] if hover else UI["raised"])
        tint = self._mix(surface, accent, 0.14 if dark else 0.09)
        if hover:
            tint = self._mix(surface, accent, 0.20 if dark else 0.13)
        bg = self._mix(base, tint, t)
        edge_off = QColor(UI["border_hover"] if hover else UI["border"])
        edge = self._mix(edge_off, accent, t * (0.85 if dark else 0.7))
        p.setPen(QPen(edge, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)

        if self.hasFocus():
            ring = QColor(accent); ring.setAlpha(110)
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 9, 9)

        # ── Pastille + glyphe de l'option ──
        tile = QRectF(12, (self.height() - 34) / 2, 34, 34)
        tile_bg = self._mix(QColor(UI["surface"] if dark else UI["bg"]),
                            accent, (0.22 if dark else 0.14) * t)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tile_bg)
        p.drawRoundedRect(tile, 8, 8)

        self._icon(p, tile, t, dark)
        p.setPen(Qt.PenStyle.NoPen)

        # ── Interrupteur ──
        sw_w, sw_h = 36.0, 20.0
        sw = QRectF(self.width() - 14 - sw_w, (self.height() - sw_h) / 2, sw_w, sw_h)
        track_off = QColor(UI["faint"] if dark else UI["border_hover"])
        p.setBrush(self._mix(track_off, accent, t))
        p.drawRoundedRect(sw, sw_h / 2, sw_h / 2)
        knob_d = sw_h - 4
        kx = sw.left() + 2 + (sw_w - 4 - knob_d) * t
        knob = QRectF(kx, sw.top() + 2, knob_d, knob_d)
        shadow = QColor(0, 0, 0, 60)
        p.setBrush(shadow)
        p.drawEllipse(knob.translated(0, 0.8))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(knob)

        # ── Textes ──
        x = tile.right() + 12
        w = sw.left() - 14 - x
        f = QFont(self.font()); f.setPixelSize(13); f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(UI["text"]))
        title_h = QFontMetrics(f).height()
        fs = QFont(self.font()); fs.setPixelSize(11)
        sub_h = QFontMetrics(fs).height() if self._subtitle else 0
        gap = 2 if self._subtitle else 0
        top = (self.height() - title_h - gap - sub_h) / 2
        p.drawText(QRectF(x, top, w, title_h), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f).elidedText(self._title, Qt.ElideRight, int(w)))
        if self._subtitle:
            p.setFont(fs)
            p.setPen(QColor(UI["muted"]))
            p.drawText(QRectF(x, top + title_h + gap, w, sub_h), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(fs).elidedText(self._subtitle, Qt.ElideRight, int(w)))
        p.end()


# ── Main window ───────────────────────────────────────────────────────────────

from pongedit.version import VERSION as APP_VERSION
from pongedit import keymap
from pongedit.keymap import label as KL


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            f"Ping Pong Live Edit 🏓  v{APP_VERSION}"
            + ("" if INSTANCE_SLOT == "1" else f" — fenêtre {INSTANCE_SLOT}")
        )
        self.resize(1200, 720)
        # En dessous, le lecteur devient trop petit pour arbitrer quoi que ce soit.
        self.setMinimumSize(1000, 540)
        # Décale les fenêtres secondaires pour qu'elles ne se superposent pas.
        try:
            _off = (int(INSTANCE_SLOT) - 1) * 48
        except ValueError:
            _off = 48
        if _off:
            self.move(80 + _off, 80 + _off)

        self.actions_data: list[Action] = []
        # Historique par instantanés : une action posée peut désormais en
        # remplacer plusieurs (fusion des intervalles), donc dépiler la
        # dernière ne suffirait plus à revenir en arrière.
        self._syncing_list = False      # garde-fou : liste et lecture se répondent
        self._list_follow_id: str | None = None
        self._undo_stack: list[list[Action]] = []
        self._redo_stack: list[list[Action]] = []
        self.first_server: int = 1
        self.match_format: int = 5  # best of N (3, 5 or 7)
        self.is_cutting   = False
        self.cut_start: float | None = None
        self.rot_angle: int = 90
        self.rot_start: float | None = None
        self.video_path: str | None  = None
        self._last_export: str | None = None   # dernier montage exporté (traînée)
        self._trail_worker = None
        self._video_hash_val: str | None = None
        self.duration    = 0.0
        self.current_time = 0.0
        self._playback_speed = 1.0
        self._speed_presets = [1.0, 1.5, 2.0, 3.0]
        self._muted = False
        self._recent_videos: list[str] = []
        self._history_cycle_idx = -1
        # Mode highlight : décisions (par bornes de segment) + état de la revue.
        self._highlights: list[dict] = []
        self._hl_review = False
        self._hl_segments: list[tuple[float, float]] = []
        self._hl_status: list[bool | None] = []
        self._hl_marks: list[tuple[float, float, bool]] = []
        self._hl_idx = -1
        self._hl_waiting = False
        self._hl_seek_t0 = 0.0
        self._export_is_highlights = False
        self._export_ignored = 0
        # Carte d'intro : valeurs validées dans le dialogue + préremplissage de
        # l'époque (cf. intro.effective_values) ; None = tout préremplissage.
        self._intro_saved: dict | None = None
        self._intro_date_cache: dict[str, str] = {}

        self._build_ui()
        self._apply_export_prefs()
        self._update_srv_btn_labels()
        self._setup_player()
        self._apply_mute_state()
        self._apply_styles()
        QApplication.instance().installEventFilter(self)

        # Restore last opened video (propre à la fenêtre) + historique partagé,
        # les deux lus dans le fichier d'état unique (hors iCloud).
        state = _read_state()
        windows = state.get("windows", {})
        last = windows.get(INSTANCE_SLOT, "") if isinstance(windows, dict) else ""
        if not isinstance(last, str):
            last = ""
        self._recent_videos = _merge_recent(_read_shared_recent(), [])
        self._refresh_history_combo()
        if last and Path(last).exists():
            QTimer.singleShot(0, lambda: self._load_video(last))

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # ── Left ──
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        tb = QWidget(); tb.setFixedHeight(50); tb.setObjectName("toolbar")
        themed(tb, lambda: f"QWidget#toolbar{{background:{UI['surface']};border-bottom:1px solid {UI['border']};}}")
        tbl = QHBoxLayout(tb); tbl.setContentsMargins(10, 0, 10, 0); tbl.setSpacing(4)
        brand = QLabel("PONG EDIT")
        themed(brand, lambda: (
            f"color:{UI['text']}; font-size:12px; font-weight:800; letter-spacing:2px;"
            "background:transparent; padding-right:6px;"
        ))
        self.brand_lbl = brand
        tbl.addWidget(brand)
        btn = self.open_btn = QPushButton("Ouvrir une vidéo")
        btn.setProperty("variant", "primary")
        btn.setProperty("inToolbar", True)   # même hauteur (34 px) que le reste de la barre
        btn.setToolTip("Choisir un fichier vidéo à monter")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self._open_file)
        tbl.addWidget(btn)
        self.history_combo = QComboBox()
        # Souple : c'est elle qui absorbe en premier la largeur perdue.
        self.history_combo.setMinimumWidth(140)
        self.history_combo.setMaximumWidth(320)
        self.history_combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                         QSizePolicy.Policy.Fixed)
        self.history_combo.setToolTip("Rouvrir une vidéo récente (session restaurée automatiquement)")
        self.history_combo.setCursor(Qt.PointingHandCursor)
        self.history_combo.currentIndexChanged.connect(self._load_history_selection)
        tbl.addWidget(self.history_combo)
        tbl.addSpacing(6)
        self.new_win_btn = QPushButton("Nouvelle fenêtre")
        self.new_win_btn.setProperty("variant", "toolbar")
        self.new_win_btn.setToolTip("Ouvrir une fenêtre indépendante pour monter un autre match en parallèle")
        self.new_win_btn.setCursor(Qt.PointingHandCursor)
        self.new_win_btn.clicked.connect(self._open_new_window)
        tbl.addWidget(self.new_win_btn)
        self.merge_btn = QPushButton("Fusionner")
        self.merge_btn.setProperty("variant", "toolbar")
        self.merge_btn.setToolTip(
            "Coller plusieurs vidéos bout à bout en un seul fichier, ouvert "
            "ensuite dans l'éditeur"
        )
        self.merge_btn.setCursor(Qt.PointingHandCursor)
        self.merge_btn.clicked.connect(self._open_merge_dialog)
        tbl.addWidget(self.merge_btn)
        self.hl_btn = QPushButton("Mode highlight")
        self.hl_btn.setObjectName("hlBtn")
        self.hl_btn.setProperty("variant", "toolbar")
        self.hl_btn.setCheckable(True)
        self.hl_btn.setToolTip(
            "Trier les séquences conservées : chaque séquence est lue puis mise en pause.\n"
            f"{KL('I')} garder  ·  {KL('N')} jeter  ·  ⇧← ⇧→ séquence préc./suiv.  ·  Espace revoir  ·  Échap quitter\n"
            "Rouvrable après le tri pour corriger une décision."
        )
        self.hl_btn.setCursor(Qt.PointingHandCursor)
        self.hl_btn.clicked.connect(self._hl_toggle)
        tbl.addWidget(self.hl_btn)
        tbl.addStretch()
        self.version_btn = QPushButton(f"v{APP_VERSION}")
        self.version_btn.setProperty("variant", "toolbar")
        self.version_btn.setToolTip("Version installée — À propos, notes de version, mises à jour")
        self.version_btn.setCursor(Qt.PointingHandCursor)
        self.version_btn.clicked.connect(self._check_updates)
        tbl.addWidget(self.version_btn)
        self.theme_btn = QPushButton()
        self.theme_btn.setProperty("variant", "toolbar")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setFixedSize(38, 34)
        self.theme_btn.clicked.connect(self._toggle_theme)
        self._update_theme_btn()
        tbl.addWidget(self.theme_btn)
        self.mute_btn = QPushButton("Son")
        self.mute_btn.setProperty("variant", "toolbar")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setToolTip(f"Couper / rétablir le son  ({KL('M')})")
        self.mute_btn.setCursor(Qt.PointingHandCursor)
        self.mute_btn.clicked.connect(self._toggle_mute)
        tbl.addWidget(self.mute_btn)
        self.speed_lbl = QLabel("1×")
        self.speed_lbl.setObjectName("badge")
        self.speed_lbl.setToolTip("Vitesse de lecture  ( ↓ ralentir · ↑ accélérer )")
        self.speed_lbl.setAlignment(Qt.AlignCenter)
        self.speed_lbl.setMinimumWidth(58)
        self.speed_lbl.setFixedHeight(34)
        tbl.addWidget(self.speed_lbl)
        # Même gabarit pour tout le haut : 34 px, centré verticalement.
        for w_ in (self.open_btn, self.history_combo, self.new_win_btn, self.merge_btn,
                   self.hl_btn, self.version_btn, self.theme_btn, self.mute_btn, self.speed_lbl):
            w_.setFixedHeight(34)
        tbl.setAlignment(Qt.AlignVCenter)
        lv.addWidget(tb)

        self.video_container = VideoContainer()
        lv.addWidget(self.video_container, 1)

        self.timeline = TimelineWidget()
        lv.addWidget(self.timeline)

        # Raccourcis : chips « touche » + libellé discret. Une page par mode
        # (montage / highlight) dans une pile : même hauteur, rien ne bouge
        # quand on bascule (cf. _set_hints_mode).
        hint_pages = [
            [   # montage
                [(KL("A"), "point J1"), (KL("S"), "point J2"), (KL("C"), "couper (tenir)"),
                 (KL("F"), "changer service"), (KL("R"), "rotation"), ("↑ ↓", "vitesse")],
                [(KL("Z"), "annuler"), ("⇧" + KL("Z"), "rétablir"), ("Espace", "lecture / pause"),
                 (KL("M"), "muet"), ("← →", "±5 s")],
            ],
            [   # mode highlight
                [(KL("I"), "garder séquence"), (KL("N"), "jeter séquence"),
                 ("Espace", "revoir séquence"), ("⇧← ⇧→", "séq. préc. / suiv.")],
                [(KL("Z"), "annuler"), (KL("M"), "muet"), ("← →", "±5 s"), ("↑ ↓", "vitesse"),
                 ("Échap", "quitter highlight")],
            ],
        ]
        self.hints_stack = QStackedWidget()
        for rows in hint_pages:
            hints = QWidget()
            hints.setObjectName("hints")
            themed(hints, lambda: f"QWidget#hints{{background:{UI['bg']};border:none;}}")
            hv = QVBoxLayout(hints)
            hv.setContentsMargins(12, 6, 12, 8)
            hv.setSpacing(4)
            for row in rows:
                hl = QHBoxLayout(); hl.setSpacing(6); hl.setContentsMargins(0, 0, 0, 0)
                for key, desc in row:
                    k = QLabel(key); k.setObjectName("kbd")
                    d = QLabel(desc); d.setObjectName("hint")
                    hl.addWidget(k); hl.addWidget(d); hl.addSpacing(8)
                hl.addStretch()
                hv.addLayout(hl)
            self.hints_stack.addWidget(hints)
        lv.addWidget(self.hints_stack)

        # ── Right ──
        # Largeur souple entre deux bornes plutôt que figée : le panneau suit la
        # fenêtre, et son contenu défile quand la hauteur ne suffit plus.
        right = QWidget()
        right.setMinimumWidth(340)   # largeur mini du contenu + la barre de défilement
        right.setMaximumWidth(440)
        right.setObjectName("rightPane")
        themed(right, lambda: f"QWidget#rightPane{{background:{UI['bg']};border-left:1px solid {UI['border']};}}")
        right_outer = QVBoxLayout(right)
        right_outer.setContentsMargins(0, 0, 0, 0)
        right_outer.setSpacing(0)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        right_outer.addWidget(right_scroll)

        right_panel = QWidget()
        right_panel.setObjectName("rightPanel")
        themed(right_panel, lambda: f"QWidget#rightPanel{{background:{UI['bg']};}}")
        right_scroll.setWidget(right_panel)
        rv = QVBoxLayout(right_panel)
        rv.setContentsMargins(12, 12, 12, 12)
        rv.setSpacing(10)

        # Le panneau est une pile de cartes ; `rv` reste la pile, `cv` la carte courante.
        card, cv = _card("Match", spacing=10)
        rv.addWidget(card)
        self.scoreboard = ScoreboardWidget()
        self.scoreboard.names_changed.connect(self._update_scores)
        self.scoreboard.names_changed.connect(self._save_session)
        self.scoreboard.names_changed.connect(self._update_export_placeholder)
        cv.addWidget(self.scoreboard)
        cv.addWidget(_sep())

        def seg_ctrl(items, callbacks, tooltip=""):
            w = QWidget()
            w.setObjectName("segGroup")
            hl = QHBoxLayout(w); hl.setContentsMargins(3,3,3,3); hl.setSpacing(2)
            btns = []
            for text, cb in zip(items, callbacks):
                btn = QPushButton(text)
                btn.setCheckable(True)
                btn.setProperty("variant", "seg")
                btn.setCursor(Qt.PointingHandCursor)
                if tooltip:
                    btn.setToolTip(tooltip)
                btn.clicked.connect(cb)
                hl.addWidget(btn); btns.append(btn)
            return w, btns

        srv_row = QHBoxLayout()
        srv_lbl = _field_label("1er service", 68)
        srv_lbl.setToolTip("Qui sert en premier au début du match")
        srv_row.addWidget(srv_lbl)
        srv_row.addStretch()
        srv_ctrl, self._srv_btns = seg_ctrl(
            ["", ""],
            [lambda _, p=1: self._set_first_server(p),
             lambda _, p=2: self._set_first_server(p)],
            tooltip="Premier serveur du match (sert au calcul des stats de service)",
        )
        self._srv_btns[0].setChecked(True)
        srv_row.addWidget(srv_ctrl)
        cv.addLayout(srv_row)
        self.scoreboard.names_changed.connect(self._update_srv_btn_labels)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(_field_label("Format", 68))
        fmt_row.addStretch()
        fmt_ctrl, self._fmt_btns = seg_ctrl(
            ["Bo3", "Bo5", "Bo7"],
            [lambda _, n=3: self._set_match_format(n),
             lambda _, n=5: self._set_match_format(n),
             lambda _, n=7: self._set_match_format(n)],
            tooltip="Format du match : meilleur des 3, 5 ou 7 sets",
        )
        self._fmt_btns[1].setChecked(True)
        fmt_row.addWidget(fmt_ctrl)
        cv.addLayout(fmt_row)

        card, cv = _card("Actions")
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        rv.addWidget(card, 1)
        self.action_list = QListWidget()
        self.action_list.setToolTip(
            "Points, coupes et rotations, dans l'ordre de la vidéo.\n"
            "Sélectionner une ligne place la lecture dessus, et la lecture "
            "sélectionne la ligne en cours.")
        self.action_list.setMinimumHeight(140)
        self.action_list.setItemDelegate(ActionDelegate(self.action_list))
        self.action_list.setMouseTracking(True)
        self.action_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        # Défilement de haut en bas uniquement : jamais de glissement latéral.
        self.action_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.action_list.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.action_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.action_list.setWordWrap(False)
        cv.addWidget(self.action_list, 1)

        self.action_list.currentItemChanged.connect(self._seek_to_action)

        del_btn = QPushButton("Supprimer la sélection")
        del_btn.setProperty("variant", "danger")
        del_btn.setToolTip("Supprimer le point ou la coupe sélectionné dans la liste")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self._delete_selected)
        cv.addWidget(del_btn)

        card, cv = _card("Export")
        rv.addWidget(card)

        self.overlay_spin = QDoubleSpinBox()  # kept for API compat, hidden
        self.overlay_spin.setValue(2.0); self.overlay_spin.hide()

        name_row = QHBoxLayout()
        name_row.addWidget(_field_label("Fichier", 48))
        self.export_name_input = QLineEdit()
        self.export_name_input.setPlaceholderText("Player_1_vs_Player_2")
        self.export_name_input.setToolTip("Nom du fichier exporté (.mp4) — vide : nom généré depuis les joueurs")
        self.export_name_input.setStyleSheet("font-size:12px;")
        name_row.addWidget(self.export_name_input)
        # Nom propre à chaque vidéo : enregistré dans sa session.
        self.export_name_input.textChanged.connect(self._save_session)
        cv.addLayout(name_row)

        # Options du montage : lignes à interrupteur empilées (chacune reste un
        # QCheckBox pour le reste du code : isChecked / setChecked / toggled).
        opts = QVBoxLayout()
        opts.setContentsMargins(0, 0, 0, 0)
        opts.setSpacing(8)

        self.trail_chk = ToggleRow("Traînée de balle", self._trail_hint_text(),
                                   icon=ToggleRow.icon_comet)
        self.trail_chk.setToolTip(
            "Enchaîne, après le montage, une passe de détection de la balle "
            "(BlurBall) qui dessine sa traînée en comète lumineuse, façon TV. "
            "Compte environ 12× la durée de la vidéo montée ; sur OFF, "
            "le montage sort tel quel."
        )
        opts.addWidget(self.trail_chk)

        # Tableau récap de fin (carte de stats) dans le montage.
        # Clic droit : ouvre toujours les statistiques détaillées du match.
        self.stats_card_btn = ToggleRow("Tableau de fin", "Carte de stats · clic droit : détails",
                                        icon=ToggleRow.icon_podium,
                                        on_context=lambda: self._show_stats())
        self.stats_card_btn.setObjectName("statsCardBtn")
        self.stats_card_btn.setChecked(True)
        self.stats_card_btn.setToolTip(
            "Tableau récap de fin dans le montage. Sur OFF, le score "
            "reste incrusté jusqu'au bout.\nClic droit : statistiques détaillées du match."
        )
        opts.addWidget(self.stats_card_btn)

        # Carte d'intro « duel » : clic = ON/OFF, clic droit = dialogue de saisie.
        self.intro_btn = ToggleRow("Intro", "Face-à-face · clic droit : éditer",
                                   icon=ToggleRow.icon_intro,
                                   on_context=lambda: self._open_intro_dialog())
        self.intro_btn.setObjectName("introBtn")
        self.intro_btn.setChecked(True)
        self.intro_btn.setToolTip(
            "Carte d'intro au début du montage et des highlights : les deux "
            "joueurs face à face sur la 1re image figée, puis le match démarre.\n"
            "Clic droit : prénoms, noms, classements, clubs, occasion, lieu, date.")
        self.intro_btn.toggled.connect(self._on_intro_toggled)
        opts.addWidget(self.intro_btn)
        cv.addLayout(opts)

        self.export_btn = QPushButton("Générer le montage")
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.setToolTip("Exporter la vidéo montée (coupes retirées, score incrusté) vers Desktop/pong_exports")
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.clicked.connect(self._start_export)
        cv.addWidget(self.export_btn)

        self.hl_summary_lbl = QLabel("Highlights : —")
        themed(self.hl_summary_lbl, lambda: f"color:{UI['muted']};font-size:11px;padding-top:4px;")
        cv.addWidget(self.hl_summary_lbl)

        self.hl_export_btn = QPushButton("Exporter les highlights")
        self.hl_export_btn.setProperty("variant", "ghost")
        self.hl_export_btn.setToolTip(
            "Même montage (score incrusté, carte de fin) limité aux séquences gardées "
            "avec I en mode highlight  →  <nom>_highlights.mp4"
        )
        self.hl_export_btn.setCursor(Qt.PointingHandCursor)
        self.hl_export_btn.setEnabled(False)
        self.hl_export_btn.clicked.connect(lambda: self._start_export(highlights_only=True))
        # Outil secondaire à côté : les options ayant pris des lignes pleine
        # largeur, on regagne ici la hauteur (le panneau ne défile pas plus).
        sec_row = QHBoxLayout()
        sec_row.setSpacing(6)
        sec_row.addWidget(self.hl_export_btn, 1)
        timecodes_btn = QPushButton("Chapitres YouTube")
        timecodes_btn.setProperty("variant", "ghost")
        timecodes_btn.setToolTip("Copier les timecodes des sets (format chapitres YouTube) dans le presse-papiers")
        timecodes_btn.setCursor(Qt.PointingHandCursor)
        timecodes_btn.clicked.connect(self._copy_youtube_timecodes)
        sec_row.addWidget(timecodes_btn, 1)
        cv.addLayout(sec_row)

        self.cancel_btn = QPushButton("Annuler l'export")
        self.cancel_btn.setProperty("variant", "danger")
        self.cancel_btn.setToolTip("Interrompre l'export en cours")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel_export)
        self.cancel_btn.hide()
        cv.addWidget(self.cancel_btn)


        self.progress_bar = QProgressBar(); self.progress_bar.setTextVisible(True)
        self.progress_bar.setRange(0, 1000)      # 1/10 de % de résolution
        self.progress_bar.setFormat("Export…  0.0%")
        self.progress_bar.hide(); cv.addWidget(self.progress_bar)

        self.export_time_lbl = QLabel()
        themed(self.export_time_lbl, lambda: f"color:{UI['muted']};font-size:11px;padding-top:2px;")
        self.export_time_lbl.hide(); cv.addWidget(self.export_time_lbl)

        self.export_status = QLabel(); self.export_status.setWordWrap(True)
        self.export_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.export_status.setStyleSheet("font-size:11px;"); self.export_status.hide()
        cv.addWidget(self.export_status)



        # Rotation : option de niche, repliée par défaut et reléguée tout en bas
        # du panneau, derrière un en-tête
        # cliquable (même typo que les titres de section). Le raccourci R et les
        # rotations posées marchent section repliée ; une rotation commencée la
        # déplie (cf. _toggle_rotation).
        card, card_v = _card()
        rv.addWidget(card)
        self.rot_toggle = QPushButton()
        self.rot_toggle.setObjectName("sectionToggle")
        self.rot_toggle.setCheckable(True)
        self.rot_toggle.setCursor(Qt.PointingHandCursor)
        self.rot_toggle.setToolTip("Afficher / masquer la rotation (le raccourci " + KL("R") + " marche toujours)")
        self.rot_toggle.toggled.connect(self._set_rot_expanded)
        card_v.addWidget(self.rot_toggle)
        self.rot_body = QWidget()
        # Sélecteur par objectName : un « background:transparent » nu s'appliquait
        # à tous les enfants et effaçait le fond du sens coché (90° ↻ illisible).
        self.rot_body.setObjectName("rotBody")
        self.rot_body.setStyleSheet("QWidget#rotBody{background:transparent;}")
        cv = QVBoxLayout(self.rot_body)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(8)
        card_v.addWidget(self.rot_body)

        rot_row = QHBoxLayout()
        rot_row.addWidget(_field_label("Sens", 68))
        rot_row.addStretch()
        rot_ctrl, self._rot_btns = seg_ctrl(
            ["90° ↻", "90° ↺", "180°"],
            [lambda _, a=90:  self._set_rot_angle(a),
             lambda _, a=270: self._set_rot_angle(a),
             lambda _, a=180: self._set_rot_angle(a)],
            tooltip="Sens de redressement appliqué à l'intervalle marqué",
        )
        self._rot_btns[0].setChecked(True)
        rot_row.addWidget(rot_ctrl)
        cv.addLayout(rot_row)

        self.rot_btn = QPushButton(f"Début de rotation  ·  {KL('R')}")
        self._style_rot_btn("init")
        self.rot_btn.setToolTip(
            "Marque le début de l'intervalle à redresser, puis reclique (ou R) "
            "à la fin. Utile quand le téléphone a été tourné en cours de match."
        )
        self.rot_btn.setCursor(Qt.PointingHandCursor)
        self.rot_btn.clicked.connect(self._toggle_rotation)
        cv.addWidget(self.rot_btn)

        self.rot_end_btn = QPushButton("D'ici jusqu'à la fin")
        self.rot_end_btn.setProperty("variant", "ghost")
        self.rot_end_btn.setToolTip("Redresse tout ce qui suit la position de lecture")
        self.rot_end_btn.setCursor(Qt.PointingHandCursor)
        self.rot_end_btn.clicked.connect(self._rotate_to_end)
        cv.addWidget(self.rot_end_btn)
        self._set_rot_expanded(False)
        rv.addStretch(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)   # ni le lecteur ni le panneau ne disparaissent
        splitter.setHandleWidth(1)
        themed(splitter, lambda: f"QSplitter::handle{{background:{UI['border']};}}")
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)   # tout l'espace gagné va au lecteur
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([876, 324])
        rl.addWidget(splitter)
        self._splitter = splitter

    def resizeEvent(self, event):
        # Sous ~1080 px, les libellés de la barre ne tiennent plus côte à côte :
        # ils tombent à l'icône seule, l'info-bulle prenant le relais.
        super().resizeEvent(event)
        if hasattr(self, "open_btn"):   # un resize peut précéder la construction de l'UI
            self._set_compact_toolbar(self.width() < 1500)
            self.brand_lbl.setVisible(self.width() >= 1100)

    def _set_compact_toolbar(self, compact: bool):
        if getattr(self, "_compact_toolbar", None) == compact:
            return
        self._compact_toolbar = compact
        for btn, full, short in ((self.open_btn,    "Ouvrir une vidéo", "Ouvrir"),
                                 (self.new_win_btn, "Nouvelle fenêtre", "+ Fenêtre"),
                                 (self.merge_btn,   "Fusionner",        "Fusionner"),
                                 (self.hl_btn,      "Mode highlight",   "Highlights")):
            btn.setText(short if compact else full)
        self._apply_mute_state()
        self._fit_toolbar()

    def _fit_toolbar(self):
        """Chaque bouton garde au moins la largeur de son texte (jamais tronqué) :
        c'est la liste d'historique qui encaisse le manque de place."""
        for b in (self.open_btn, self.new_win_btn, self.merge_btn, self.hl_btn, self.mute_btn):
            b.setMinimumWidth(0)
            b.setMinimumWidth(b.sizeHint().width())
        self.speed_lbl.setMinimumWidth(max(48, self.speed_lbl.sizeHint().width()))
        self.history_combo.setMinimumWidth(90)

    # ── Player ────────────────────────────────────────────────────────────────

    def _setup_player(self):
        self.player = QMediaPlayer()
        self.audio_out = QAudioOutput(); self.audio_out.setVolume(1.0)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video_container.video_item)
        self.player.positionChanged.connect(lambda ms: self._on_position(ms / 1000.0))
        self.player.durationChanged.connect(self._on_duration)
        self.timeline.seek_requested.connect(self._seek)

    def _on_duration(self, ms: int):
        self.duration = ms / 1000.0
        # La session se charge avant que le lecteur annonce la durée : les
        # séquences (donc le résumé highlight) ne sont calculables qu'ici.
        self._hl_refresh_summary()

    def _seek(self, t: float, *, user: bool = True):
        """Point d'entrée unique des déplacements. En revue highlight, un seek
        « utilisateur » recible la séquence sous la tête de lecture."""
        t = max(0.0, min(self.duration, t) if self.duration else t)
        if self._hl_review and user:
            self._hl_idx = self._hl_index_at(t)
            self._hl_waiting = False
            self._hl_seek_t0 = time.monotonic()
            self._hl_update_hud()
        self.player.setPosition(int(t * 1000))

    def _sync_preview_rotation(self, t: float | None = None):
        """Angle actif au temps courant. En cas de recouvrement, même gagnant qu'à
        l'export : les branches y sont empilées par angle croissant."""
        if t is None:
            t = self.current_time
        angles = [a.angle for a in self.actions_data
                  if isinstance(a, RotateAction) and a.start <= t < a.end]
        self.video_container.set_preview_rotation(max(angles) if angles else 0)

    def _on_position(self, t: float):
        self.current_time = t
        self._sync_preview_rotation(t)
        self._sync_list_to_playhead(t)
        if self._hl_review:
            self._hl_on_position(t)
        self.timeline.refresh(self.actions_data, self.duration, t,
                               self.cut_start if self.is_cutting else None,
                               highlights=self._hl_marks,
                               review_span=self._hl_review_span())

    def _apply_mute_state(self):
        if hasattr(self, "audio_out"):
            self.audio_out.setVolume(0.0 if self._muted else 1.0)
        if hasattr(self, "mute_btn"):
            self.mute_btn.setChecked(self._muted)
            self.mute_btn.setText("Muet" if self._muted else "Son")

    def _toggle_mute(self):
        self._muted = not self._muted
        self._apply_mute_state()

    # ── File open ─────────────────────────────────────────────────────────────

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", str(Path.home()),
            "Videos (*.mp4 *.mov *.avi *.mkv *.m4v *.webm);;All (*)"
        )
        if not path: return
        self._load_video(path)

    def _update_export_placeholder(self):
        p1 = re.sub(r"\s+", "_", self.scoreboard.p1_name().strip()) or "Player_1"
        p2 = re.sub(r"\s+", "_", self.scoreboard.p2_name().strip()) or "Player_2"
        self.export_name_input.setPlaceholderText(f"{p1}_vs_{p2}")

    def _history_export_name_for_path(self, path: str) -> str:
        try:
            video_hash = _video_hash(path)
            session_path = SESSIONS_DIR / f"{video_hash}.json"
            if session_path.exists():
                data = json.loads(session_path.read_text())
                if str(data.get("export_name", "")).strip():
                    return str(data["export_name"]).strip()
                p1 = re.sub(r"\s+", "_", str(data.get("p1_name", "")).strip()) or "Player_1"
                p2 = re.sub(r"\s+", "_", str(data.get("p2_name", "")).strip()) or "Player_2"
                return f"{p1}_vs_{p2}"
        except Exception:
            pass
        return Path(path).stem

    def _refresh_history_combo(self):
        if not hasattr(self, "history_combo"):
            return
        items = [p for p in self._recent_videos[:RECENT_MAX] if Path(p).exists()]
        self.history_combo.blockSignals(True)
        self.history_combo.clear()
        self.history_combo.addItem("Vidéos récentes…")
        for path in items:
            label = f"{Path(path).name}  →  {self._history_export_name_for_path(path)}"
            self.history_combo.addItem(label, path)
        current_idx = 0
        if self.video_path:
            for i, path in enumerate(items, start=1):
                if path == self.video_path:
                    current_idx = i
                    break
        self.history_combo.setCurrentIndex(current_idx)
        self.history_combo.setEnabled(bool(items))
        self.history_combo.blockSignals(False)

    def _load_history_selection(self, index: int):
        if index <= 0:
            return
        path = self.history_combo.itemData(index)
        if not path or path == self.video_path:
            return
        self._load_video(path)

    def _persist_recent_videos(self):
        """Écrit ma vidéo courante et l'historique dans le fichier d'état commun.

        Relecture juste avant écriture : les autres fenêtres écrivent dans le même
        fichier, on ne doit effacer ni leur vidéo courante ni ce qu'elles viennent
        d'ouvrir.
        """
        try:
            state = _read_state()
            windows = state.get("windows")
            if not isinstance(windows, dict):
                windows = {}
            if self.video_path:
                windows[INSTANCE_SLOT] = self.video_path
            else:
                windows.pop(INSTANCE_SLOT, None)
            state["windows"] = windows
            state["history"] = _merge_recent(self._recent_videos, _read_shared_recent())
            _write_state(state)
        except Exception:
            pass

    def _record_recent_video(self, path: str):
        cleaned = [p for p in self._recent_videos if p != path and Path(p).exists()]
        self._recent_videos = _merge_recent([path, *cleaned], _read_shared_recent())
        self._history_cycle_idx = -1
        self._refresh_history_combo()

    def _load_video(self, path: str, record_history: bool = True):
        self.video_path = path
        self._video_hash_val = _video_hash(path)
        self._update_export_placeholder()
        self.actions_data.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        # Le nom de la vidéo précédente ne doit pas déborder sur celle-ci.
        self.export_name_input.blockSignals(True)
        self.export_name_input.clear()
        self.export_name_input.blockSignals(False)
        self.rot_start = None
        self._reset_rot_btn()
        self._update_scores()
        self._refresh_log()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        if self._hl_review:
            self._hl_exit()
        self._highlights = []
        self._intro_saved = None
        self._load_session()
        if record_history:
            self._record_recent_video(path)   # rafraîchit déjà le combo
        self._persist_recent_videos()
        if not record_history:
            # Sinon on rafraîchissait DEUX fois : chaque passage recalcule un hash et
            # relit un JSON de session pour chacune des 5 vidéos récentes.
            self._refresh_history_combo()

    # ── Key handling ──────────────────────────────────────────────────────────

    def _shortcuts_active(self) -> bool:
        """Raccourcis du montage (A, S, C, Z…) seulement quand l'éditeur a la main.

        Le filtre est posé sur toute l'application : sans ce garde, taper dans un
        dialogue (carte d'intro, fusion…) ajoutait des points derrière.
        """
        if QApplication.activeModalWidget() is not None:
            return False
        if QApplication.activeWindow() is not self:
            return False
        fw = QApplication.focusWidget()
        return not isinstance(fw, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)) and not (
            isinstance(fw, QComboBox) and fw.isEditable())

    _INPUT_TYPES = (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)

    def _focused_input(self):
        """Le champ de saisie qui a le focus dans CETTE fenêtre (hors dialogue), sinon None."""
        if QApplication.activeModalWidget() is not None or QApplication.activeWindow() is not self:
            return None
        fw = QApplication.focusWidget()
        if isinstance(fw, self._INPUT_TYPES) or (isinstance(fw, QComboBox) and fw.isEditable()):
            return fw
        return None

    def _leave_field_on_enter(self) -> bool:
        """Entrée valide la saisie et REND LE FOCUS à la fenêtre : les raccourcis (A, S, F…)
        redeviennent actifs au lieu d'écrire des lettres dans le champ."""
        fw = self._focused_input()
        if fw is None or isinstance(fw, (QTextEdit, QPlainTextEdit)):   # Entrée = saut de ligne
            return False
        fw.clearFocus()          # déclenche editingFinished : la valeur est prise en compte
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _release_focus_on_outside_click(self, e):
        """Un clic en dehors du champ en cours de saisie libère le focus (sinon il restait
        collé jusqu'à ce qu'on clique sur un autre champ ou bouton précis)."""
        fw = self._focused_input()
        if fw is None:
            return
        w = QApplication.widgetAt(e.globalPosition().toPoint())
        while w is not None:
            if w is fw or isinstance(w, self._INPUT_TYPES):
                return
            w = w.parentWidget()
        fw.clearFocus()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if e.type() == QEvent.Type.KeyPress and not e.isAutoRepeat():
            k  = e.key()
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._leave_field_on_enter():
                return True
            lk = keymap.logical_key(e)   # touche visée par son EMPLACEMENT (QWERTY/AZERTY…)
            # Une option à interrupteur atteinte au Tab garde Espace / Entrée
            # pour elle (cocher), au lieu de lancer la lecture.
            if (k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and isinstance(QApplication.focusWidget(), ToggleRow)):
                return super().eventFilter(obj, e)
            if self._shortcuts_active():
                if lk == "A":     self._add_point(1); return True
                if lk == "S":     self._add_point(2); return True
                if lk == "F":     self._add_serve_swap(); return True
                if lk == "C" and not self.is_cutting:
                                          self._start_cut(); return True
                if lk == "Z":
                    if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        self._redo()
                    else:
                        self._undo()
                    return True
                shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if k == Qt.Key.Key_Left:
                    if self._hl_review and shift:
                        self._hl_step(-1); return True
                    self._seek(self.player.position() / 1000.0 - 5.0); return True
                if k == Qt.Key.Key_Right:
                    if self._hl_review and shift:
                        self._hl_step(1); return True
                    self._seek(self.player.position() / 1000.0 + 5.0); return True
                if lk == "I":     self._hl_decide(True);  return True
                if lk == "N":     self._hl_decide(False); return True
                if self._hl_review:
                    if k == Qt.Key.Key_Space:  self._hl_replay(); return True
                    if k == Qt.Key.Key_Escape: self._hl_exit();   return True
                if k == Qt.Key.Key_Space:
                    st = self.player.playbackState()
                    (self.player.pause() if st == QMediaPlayer.PlaybackState.PlayingState
                     else self.player.play())
                    return True
                if lk == "M":
                    self._toggle_mute()
                    return True
                if lk == "R":
                    self._toggle_rotation()
                    return True
                if k == Qt.Key.Key_Down:
                    idx = self._speed_presets.index(self._playback_speed) if self._playback_speed in self._speed_presets else 3
                    self._set_speed(self._speed_presets[max(0, idx - 1)]); return True
                if k == Qt.Key.Key_Up:
                    idx = self._speed_presets.index(self._playback_speed) if self._playback_speed in self._speed_presets else 3
                    self._set_speed(self._speed_presets[min(len(self._speed_presets) - 1, idx + 1)]); return True
        if e.type() == QEvent.Type.MouseButtonPress:
            self._release_focus_on_outside_click(e)
        if e.type() == QEvent.Type.KeyRelease and not e.isAutoRepeat():
            # Relâcher C ne concerne que la coupe en cours : sinon on laisse
            # passer l'évènement (un « c » tapé dans un dialogue).
            if keymap.logical_key(e) == "C" and self.is_cutting:
                self._end_cut(); return True
        return super().eventFilter(obj, e)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _add_point(self, player: int):
        if not self.video_path: return
        # Qt remet la position du lecteur a 0 quand la lecture atteint la fin
        # du fichier, sans que la barre bouge visiblement. Une touche pressee
        # a ce moment creait un point au timecode 0 : la carte de stats se
        # declenchait 2,5 s apres le DEBUT du match et recouvrait toute la
        # video (bug du 18/09/2026). Le garde-fou protege d'un timecode faux,
        # pas d'un probleme d'ordre : il reste utile maintenant que l'ordre
        # des points suit la timeline.
        if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            print("point ignore : lecture terminee, position du lecteur remise a 0")
            return
        _pts = [a for a in self.actions_data if isinstance(a, PointAction)]
        if _pts and self.current_time <= 0.05 and max(p.timecode for p in _pts) > 1.0:
            print("point ignore : timecode 0 alors que le match est deja commence")
            return
        self._push_history()
        completed, (cur_p1, cur_p2) = compute_sets(self.actions_data)
        new_p1 = cur_p1 + (1 if player == 1 else 0)
        new_p2 = cur_p2 + (1 if player == 2 else 0)
        self.actions_data.append(PointAction(
            id=gen_id(), player=player, timecode=self.current_time,
            score=f"{new_p1}-{new_p2}",
            set_num=len(completed),
            completed_sets=list(completed),
        ))
        self._normalize_actions()
        self._update_scores()
        self._refresh_log()
        self._refresh_timeline()
        self.video_container.show_flash(player)
        self._save_session()

    def _start_cut(self):
        if not self.video_path: return
        self.is_cutting = True
        self.cut_start  = self.current_time
        self.player.setPlaybackRate(2.0)
        self.video_container.set_cutting(True)

    def _set_speed(self, rate: float):
        self._playback_speed = rate
        self.player.setPlaybackRate(rate)
        self.speed_lbl.setText(f"{rate:g}×")

    def _end_cut(self):
        if not self.is_cutting: return
        self.is_cutting = False
        self.player.setPlaybackRate(self._playback_speed)
        self.video_container.set_cutting(False)
        if self.cut_start is not None and self.current_time > self.cut_start:
            self._push_history()
            self.actions_data.append(
                CutAction(id=gen_id(), start=self.cut_start, end=self.current_time)
            )
            # Une coupe qui en recouvre une autre n'en fait plus qu'une.
            self.actions_data = _merge_overlapping_spans(self.actions_data)
            self._normalize_actions()
            self._refresh_log()
            self._refresh_timeline()
            self._save_session()
        self.cut_start = None

    def _set_rot_angle(self, angle: int):
        self.rot_angle = angle
        for btn, a in zip(self._rot_btns, (90, 270, 180)):
            btn.setChecked(a == angle)

    def _add_rotation(self, start: float, end: float):
        if not self.video_path or end - start < 0.05:
            return
        self._push_history()
        self.actions_data.append(
            RotateAction(id=gen_id(), start=start, end=end, angle=self.rot_angle)
        )
        self.actions_data = _merge_overlapping_spans(self.actions_data)
        self._normalize_actions()
        self._refresh_log()
        self._refresh_timeline()
        self._save_session()

    def _set_rot_expanded(self, on: bool):
        """Déplie / replie la section Rotation."""
        if self.rot_toggle.isChecked() != on:
            self.rot_toggle.setChecked(on)      # rappelle cette méthode via toggled
            return
        self.rot_toggle.setText(f"{'▾' if on else '▸'}  ROTATION")
        self.rot_body.setVisible(on)

    def _toggle_rotation(self):
        """Premier appel : début de l'intervalle. Second : fin, et l'effet est posé."""
        if not self.video_path:
            return
        if self.rot_start is None:
            self._set_rot_expanded(True)        # rotation en cours : on la montre
            self.rot_start = self.current_time
            self.rot_btn.setText(f"⏹  Fin de rotation  ({KL('R')})")
            self._style_rot_btn("active")
            return
        start, self.rot_start = self.rot_start, None
        self._reset_rot_btn()
        lo, hi = sorted((start, self.current_time))
        self._add_rotation(lo, hi)

    def _rotate_to_end(self):
        if not self.video_path or self.duration <= 0:
            return
        start = self.rot_start if self.rot_start is not None else self.current_time
        self.rot_start = None
        self._reset_rot_btn()
        self._add_rotation(start, self.duration)

    def _reset_rot_btn(self):
        self.rot_btn.setText(f"↻  Début de rotation  ({KL('R')})")
        self._style_rot_btn("idle")

    def _style_rot_btn(self, state: str):
        """Style du bouton rotation : « init » (bordé), « idle », « active »."""
        def css():
            if state == "active":
                return (f"QPushButton{{background:{UI['rot_on']};color:{UI['rot_on_fg']};font-weight:bold;}}"
                        f"QPushButton:hover{{background:{UI['rot_on_hover']};}}")
            edge = f"border:1px solid {UI['rot_edge']};" if state == "init" else ""
            return (f"QPushButton{{background:{UI['rot_bg']};color:{UI['rot_fg']};{edge}}}"
                    f"QPushButton:hover{{background:{UI['rot_edge']};color:{UI['rot_fg_hover']};}}")
        themed(self.rot_btn, css)

    def _update_srv_btn_labels(self):
        self._srv_btns[0].setText(self.scoreboard.p1_name()[:10])
        self._srv_btns[1].setText(self.scoreboard.p2_name()[:10])

    def _set_first_server(self, p: int):
        self.first_server = p
        self._srv_btns[0].setChecked(p == 1)
        self._srv_btns[1].setChecked(p == 2)
        self._update_scores()

    def _add_serve_swap(self):
        """F : inverse de force le serveur à partir de l'instant courant (match
        amical où l'on s'est trompé de serveur). Un 2e marqueur ré-inverse ;
        Z annule, et le marqueur se supprime dans la liste comme un point."""
        if not self.video_path:
            return
        self._push_history()
        self.actions_data.append(ServeSwapAction(id=gen_id(), timecode=self.current_time))
        self._normalize_actions()
        self._update_scores()
        self._refresh_log()
        self._refresh_timeline()
        self._save_session()

    def _set_match_format(self, n: int):
        self.match_format = n
        for i, btn in enumerate(self._fmt_btns):
            btn.setChecked((3, 5, 7)[i] == n)

    def _compute_match_stats(self) -> dict:
        return _compute_stats_from_dicts(self._actions_as_dicts(), self.first_server)

    def _show_stats(self):
        stats = self._compute_match_stats()
        p1n = self.scoreboard.p1_name()
        p2n = self.scoreboard.p2_name()
        p1r = self.scoreboard.p1_rank()
        p2r = self.scoreboard.p2_rank()

        dlg = QDialog(self)
        dlg.setWindowTitle("Statistiques du match")
        dlg.setWindowFlag(Qt.WindowType.Window, True)
        dlg.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        dlg.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        dlg.resize(620, 560)
        dlg.setMinimumSize(360, 280)
        themed(dlg, lambda: f"QDialog{{background:{UI['bg']}; color:{UI['text']};}}")

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        themed(scroll, lambda: f"QScrollArea{{background:{UI['bg']}; border:none;}}")
        outer.addWidget(scroll)

        body = QWidget()
        body.setObjectName("statsBody")
        themed(body, lambda: f"QWidget#statsBody{{background:{UI['bg']};}}")
        scroll.setWidget(body)

        content = QVBoxLayout(body)
        content.setSpacing(10)
        content.setContentsMargins(20, 20, 20, 20)

        def pct(n, d):
            return f"{n}/{d}  ({100*n//d}%)" if d else "—"

        def sec_lbl(text):
            label = QLabel(text)
            themed(label, lambda: f"color:{UI['faint']};font-size:10px;font-weight:bold;letter-spacing:2px;margin-top:6px;")
            return label

        def hline():
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.HLine)
            themed(frame, lambda: f"color:{UI['surface']};")
            return frame

        if not stats:
            empty = QLabel("Aucun point enregistré.")
            themed(empty, lambda: f"font-size:13px; color:{UI['muted']};")
            content.addWidget(empty)
        else:
            title = QLabel("MATCH TERMINÉ")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            themed(title, lambda: f"color:{UI['dim']};font-size:10px;font-weight:bold;letter-spacing:4px;")
            content.addWidget(title)

            score = QLabel()
            def _score_css(score=score):
                score.setText(
                    f"<span style='color:{UI['p1']}'>{p1n}</span>"
                    f"&nbsp;&nbsp;<b>{stats['p1_sets']} – {stats['p2_sets']}</b>&nbsp;&nbsp;"
                    f"<span style='color:{UI['p2']}'>{p2n}</span>"
                )
                return "font-size:20px;font-weight:bold;padding:4px 0 2px 0;"
            score.setAlignment(Qt.AlignmentFlag.AlignCenter)
            themed(score, _score_css)
            content.addWidget(score)
            if p1r or p2r:
                ranks = QLabel()
                def _ranks_css(ranks=ranks):
                    ranks.setText(
                        f"<span style='color:{UI['muted']}'>{p1r or '—'}</span>"
                        f"&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;"
                        f"<span style='color:{UI['muted']}'>{p2r or '—'}</span>"
                    )
                    return "font-size:11px;padding:0 0 8px 0;"
                ranks.setAlignment(Qt.AlignmentFlag.AlignCenter)
                themed(ranks, _ranks_css)
                ranks.setToolTip("Classement saisi sous chaque nom dans le panneau Match")
                content.addWidget(ranks)
            content.addWidget(hline())

            content.addWidget(sec_lbl("STATS GLOBALES"))
            p1_wins = stats["p1_sets"] > stats["p2_sets"]
            global_rows = [
                ("Sets gagnés",
                 str(stats["p1_sets"]), "p1" if p1_wins else "text",
                 str(stats["p2_sets"]), "p2" if not p1_wins else "text"),
                ("Points totaux", str(stats["p1_pts"]), "p1", str(stats["p2_pts"]), "p2"),
                ("Service gagné",
                 pct(stats["p1_won_srv"], stats["p1_tot_srv"]), "p1",
                 pct(stats["p2_won_srv"], stats["p2_tot_srv"]), "p2"),
                ("Retour gagné",
                 pct(stats["p1_pts"] - stats["p1_won_srv"], stats["p2_tot_srv"]), "p1",
                 pct(stats["p2_pts"] - stats["p2_won_srv"], stats["p1_tot_srv"]), "p2"),
            ]
            for label, v1, c1, v2, c2 in global_rows:
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(0, 0, 0, 0)
                left = QLabel(label)
                themed(left, lambda: f"color:{UI['muted']};font-size:12px;")
                r1 = QLabel(v1)
                themed(r1, lambda c1=c1: f"color:{UI[c1]};font-size:12px;font-weight:bold;")
                vs = QLabel("  vs  ")
                themed(vs, lambda: f"color:{UI['faint']};font-size:11px;")
                r2 = QLabel(v2)
                themed(r2, lambda c2=c2: f"color:{UI[c2]};font-size:12px;font-weight:bold;")
                layout.addWidget(left)
                layout.addStretch()
                layout.addWidget(r1)
                layout.addWidget(vs)
                layout.addWidget(r2)
                content.addWidget(row)

            content.addWidget(hline())
            content.addWidget(sec_lbl("DÉTAIL PAR SET"))
            for i, sd in enumerate(stats["sets"]):
                sp1, sp2 = sd["score"]
                header_color = "p1" if sp1 > sp2 else "p2"
                first_srv = p1n if sd["fsrv"] == 1 else p2n
                hdr = QLabel(f"Set {i + 1}  ·  {sp1} – {sp2}  ·  1er service : {first_srv}")
                themed(hdr, lambda hc=header_color: f"color:{UI[hc]};font-size:12px;font-weight:bold;margin-top:4px;")
                content.addWidget(hdr)
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(0, 0, 0, 0)
                left = QLabel("  Service gagné")
                themed(left, lambda: f"color:{UI['dim']};font-size:11px;")
                r1 = QLabel(f"{p1n}  {pct(sd['p1_won_srv'], sd['p1_tot_srv'])}")
                themed(r1, lambda: f"color:{UI['p1']};font-size:11px;")
                r2 = QLabel(f"{p2n}  {pct(sd['p2_won_srv'], sd['p2_tot_srv'])}")
                themed(r2, lambda: f"color:{UI['p2']};font-size:11px;")
                layout.addWidget(left)
                layout.addStretch()
                layout.addWidget(r1)
                layout.addWidget(QLabel("  "))
                layout.addWidget(r2)
                content.addWidget(row)

        card_png_btn = QPushButton("Exporter le tableau (PNG)")
        card_png_btn.setProperty("variant", "ghost")
        card_png_btn.setToolTip("Enregistre le tableau de fin en image dans pong_exports, "
                                "pour le partager")
        card_png_btn.setCursor(Qt.PointingHandCursor)
        card_png_btn.clicked.connect(lambda: self._export_card_png(card_png_btn))
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(card_png_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(dlg.accept)
        themed(close_btn, lambda: (
            f"QPushButton{{background:{UI['surface']};color:{UI['text']};border:none;border-radius:6px;"
            "padding:8px 28px;font-size:13px;margin:12px 20px 20px 20px;}"
            f"QPushButton:hover{{background:{UI['border']};}}"
        ))
        outer.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        # Non modale, comme la fenêtre de fusion : elle peut rester ouverte à côté.
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show(); dlg.raise_(); dlg.activateWindow()

    def _seek_to_action(self, item, _previous=None):
        """Sélection dans la liste → la lecture se place au début de l'action."""
        if item is None or self._syncing_list:
            return
        a = self._action_by_id(item.data(Qt.ItemDataRole.UserRole))
        if a is None:
            return
        self._list_follow_id = a.id
        self._seek(self._action_time(a))

    def _covers(self, a, t: float) -> bool:
        """L'action est-elle celle qu'on est en train de regarder ?"""
        if isinstance(a, (PointAction, ServeSwapAction)):
            return abs(a.timecode - t) < 0.5
        return a.start <= t < a.end

    def _action_at_time(self, t: float):
        inside = [a for a in self.actions_data
                  if isinstance(a, (CutAction, RotateAction)) and a.start <= t < a.end]
        if inside:
            return max(inside, key=self._action_time)
        before = [a for a in self.actions_data if self._action_time(a) <= t]
        return max(before, key=self._action_time) if before else None

    def _sync_list_to_playhead(self, t: float):
        """Lecture → la liste met en évidence l'action en cours."""
        if self._syncing_list or self.action_list.count() == 0:
            return
        cur = self.action_list.currentItem()
        if cur is not None:
            a_cur = self._action_by_id(cur.data(Qt.ItemDataRole.UserRole))
            # Une sélection volontaire reste tant qu'elle décrit encore l'image.
            if a_cur is not None and self._covers(a_cur, t):
                self._list_follow_id = a_cur.id
                return
        a = self._action_at_time(t)
        aid = a.id if a is not None else None
        if aid == self._list_follow_id:
            return
        self._list_follow_id = aid
        row = next((i for i in range(self.action_list.count())
                    if self.action_list.item(i).data(Qt.ItemDataRole.UserRole) == aid), -1)
        self._syncing_list = True
        self.action_list.setCurrentRow(row)
        if row >= 0:
            self.action_list.scrollToItem(self.action_list.item(row))
        self._syncing_list = False

    def _push_history(self):
        """Mémorise l'état courant avant une modification."""
        self._undo_stack.append(list(self.actions_data))
        del self._undo_stack[:-200]      # borne mémoire
        self._redo_stack.clear()

    def _undo(self):
        if self._undo_stack:
            self._redo_stack.append(list(self.actions_data))
            self.actions_data = self._undo_stack.pop()
            self._after_history_move()

    def _redo(self):
        if self._redo_stack:
            self._undo_stack.append(list(self.actions_data))
            self.actions_data = self._redo_stack.pop()
            self._after_history_move()

    def _after_history_move(self):
        self._normalize_actions()
        self._update_scores()
        self._refresh_log()
        self._refresh_timeline()
        self._save_session()

    # ── Mode highlight ────────────────────────────────────────────────────────

    def _hl_cut_dicts(self) -> list[dict]:
        return [{"start": a.start, "end": a.end}
                for a in self.actions_data if isinstance(a, CutAction)]

    def _hl_resync(self):
        """Recalcule les séquences et leur statut d'après les coupes courantes."""
        prev = self._hl_segments
        if self.video_path and self.duration > 0:
            self._hl_segments = _build_kept_segments(self._hl_cut_dicts(), self.duration)
        else:
            self._hl_segments = []
        self._hl_status = _highlight_status(self._hl_segments, self._highlights)
        self._hl_marks = [(s, e, st) for (s, e), st in zip(self._hl_segments, self._hl_status)
                          if st is not None]
        # En revue : si les séquences ont changé (coupe ajoutée/retirée), on
        # recible d'après la tête de lecture ; sinon l'index courant reste valable.
        if self._hl_review and self._hl_idx >= 0 and self._hl_segments != prev:
            self._hl_idx = self._hl_index_at(self.current_time)
            self._hl_waiting = False

    def _trail_hint_text(self) -> str:
        """Sous-titre de l'option traînée : durée de calcul estimée (~2× le montage)."""
        if sys.platform != "darwin":      # l'estimation ci-dessous est mesurée sur puce Apple
            return "Comète TV · calcul long (1ʳᵉ fois : installation du module, plusieurs Go)"
        dur = getattr(self, "duration", 0.0) or 0.0
        if not getattr(self, "video_path", None) or dur <= 0:
            return "Comète TV · calcul ≈ 2× le film"
        cuts = [{"start": a.start, "end": a.end}
                for a in getattr(self, "actions_data", []) if isinstance(a, CutAction)]
        kept = _kept_duration(cuts, dur) if cuts else dur
        # Mesuré : Neural Engine ~88 img/s + rendu matériel ≈ 1,7× le film (ancien 12× = MPS)
        mins = max(1, round(kept * 2 / 60))
        h, m = divmod(mins, 60)
        est = f"{m} min" if not h else (f"{h} h" if not m else f"{h} h {m:02d}")
        return f"Comète TV · calcul ≈ {est}"

    def _hl_refresh_summary(self):
        self._hl_resync()
        if not hasattr(self, "hl_summary_lbl"):
            return
        if hasattr(self, "trail_chk"):
            self.trail_chk.setSubtitle(self._trail_hint_text())
        if not self._hl_segments:
            self.hl_summary_lbl.setText("Highlights : —")
            self.hl_export_btn.setEnabled(False)
            return
        kept, rej, todo = _highlight_counts(self._hl_status)
        self.hl_summary_lbl.setText(
            f"Highlights : {kept} gardée{'s' if kept > 1 else ''} · "
            f"{rej} rejetée{'s' if rej > 1 else ''} · {todo} à trier")
        busy = ((getattr(self, "_worker", None) is not None and self._worker.isRunning())
                or (getattr(self, "_trail_worker", None) is not None
                    and self._trail_worker.isRunning()))
        self.hl_export_btn.setEnabled(kept > 0 and not busy)
        if self._hl_review:
            self._hl_update_hud()

    def _hl_review_span(self):
        if self._hl_review and 0 <= self._hl_idx < len(self._hl_segments):
            return self._hl_segments[self._hl_idx]
        return None

    def _hl_index_at(self, t: float) -> int:
        for i, (s, e) in enumerate(self._hl_segments):
            if s - HL_TOL <= t < e + HL_TOL:
                return i
        return -1

    def _hl_next_undecided(self, after: int) -> int:
        n = len(self._hl_status)
        for k in range(1, n + 1):
            i = (after + k) % n if n else -1
            if i >= 0 and self._hl_status[i] is None:
                return i
        return -1

    def _hl_index_near(self, t: float) -> int:
        """Séquence sous la tête de lecture, sinon la plus proche (0 si aucune)."""
        i = self._hl_index_at(t)
        if i >= 0 or not self._hl_segments:
            return i if i >= 0 else -1
        return min(range(len(self._hl_segments)),
                   key=lambda j: min(abs(t - self._hl_segments[j][0]),
                                     abs(t - self._hl_segments[j][1])))

    def _hl_step(self, delta: int):
        """Séquence précédente / suivante pendant la revue (⇧← / ⇧→)."""
        n = len(self._hl_segments)
        if not self._hl_review or n == 0:
            return
        cur = self._hl_idx if self._hl_idx >= 0 else self._hl_index_near(self.current_time)
        if cur < 0:
            cur = 0 if delta > 0 else n - 1
            self._hl_goto(cur)
            return
        self._hl_goto((cur + delta) % n)

    def _hl_toggle(self):
        if self._hl_review:
            self._hl_exit()
        else:
            self._hl_enter()

    def _hl_enter(self):
        if not self.video_path or self.duration <= 0:
            self.hl_btn.setChecked(False)
            return
        self._hl_refresh_summary()
        if not self._hl_segments:
            self.hl_btn.setChecked(False)
            self._hl_status_msg("Aucune séquence à trier — ajoute d'abord des coupes.", "warn")
            return
        idx = self._hl_next_undecided(-1)
        if idx < 0:
            # Tout est déjà trié : on rouvre quand même la revue pour corriger,
            # en repartant de la séquence sous la tête de lecture.
            idx = max(0, self._hl_index_near(self.current_time))
            self._hl_status_msg("Toutes les séquences sont triées — I / N pour corriger, "
                                "⇧← ⇧→ pour changer de séquence.", "warn")
        self._hl_review = True
        self.hl_btn.setChecked(True)
        self._set_hints_mode(True)
        self._hl_goto(idx)

    def _hl_goto(self, idx: int):
        if not (0 <= idx < len(self._hl_segments)):
            return
        self._hl_idx = idx
        self._hl_waiting = False
        self._hl_seek_t0 = time.monotonic()
        self.current_time = self._hl_segments[idx][0]   # le lecteur confirmera au prochain tick
        self._seek(self._hl_segments[idx][0], user=False)
        self.player.play()
        self._hl_update_hud()
        self._refresh_timeline()

    def _hl_decide(self, keep: bool):
        if not self._hl_segments:
            self._hl_resync()
        idx = self._hl_idx if self._hl_review else -1
        if idx < 0:
            idx = self._hl_index_at(self.current_time)
        if not (0 <= idx < len(self._hl_segments)):
            return
        was_decided = self._hl_status[idx] is not None if idx < len(self._hl_status) else False
        s, e = self._hl_segments[idx]
        self._highlights = [d for d in self._highlights
                            if _match_highlight((s, e), [d]) is None]
        self._highlights.append({"start": float(s), "end": float(e), "keep": bool(keep)})
        self._save_session()
        self._hl_refresh_summary()
        self._refresh_timeline()
        if not self._hl_review:
            return
        if was_decided:
            # Correction d'un tri existant : on reste sur la séquence.
            self._hl_update_hud()
            return
        nxt = self._hl_next_undecided(idx)
        if nxt >= 0:
            self._hl_goto(nxt)
        else:
            self._hl_exit(summary=True)

    def _hl_replay(self):
        if self._hl_idx >= 0:
            self._hl_goto(self._hl_idx)

    def _set_hints_mode(self, highlight: bool):
        """Bandeau des raccourcis : page du mode highlight ou du montage."""
        if hasattr(self, "hints_stack"):
            self.hints_stack.setCurrentIndex(1 if highlight else 0)

    def _hl_exit(self, summary: bool = False):
        self._hl_review = False
        self._set_hints_mode(False)
        self._hl_waiting = False
        self._hl_idx = -1
        if hasattr(self, "hl_btn"):
            self.hl_btn.setChecked(False)
        self.video_container.set_highlight_hud(None)
        if summary:
            kept, rej, _ = _highlight_counts(self._hl_status)
            self._hl_status_msg(f"✓ Tri terminé : {kept} gardée{'s' if kept > 1 else ''} · "
                                f"{rej} rejetée{'s' if rej > 1 else ''}", "success")
        self._refresh_timeline()

    def _hl_status_msg(self, text: str, color: str):
        """`color` : clé de la palette UI (success, warn, danger, danger_text)."""
        self.export_status.setText(text)
        self._status_color(color)
        self.export_status.show()

    def _hl_update_hud(self):
        if not self._hl_review:
            self.video_container.set_highlight_hud(None)
            return
        n = len(self._hl_segments)
        if not (0 <= self._hl_idx < n):
            self.video_container.set_highlight_hud(
                "MODE HIGHLIGHT  ·  clique une séquence sur la timeline  ·  Échap quitter", "pending")
            return
        st = self._hl_status[self._hl_idx]
        txt = f"SÉQUENCE {self._hl_idx + 1} / {n}  ·  {KL('I')} garder  ·  {KL('N')} jeter  ·  ⇧← ⇧→ naviguer"
        state = "pending"
        if st is True:
            txt += "  ·  ✓ GARDÉE"; state = "keep"
        elif st is False:
            txt += "  ·  ✗ REJETÉE"; state = "reject"
        if self._hl_waiting:
            txt = "FIN DE SÉQUENCE — " + txt + "  ·  Espace revoir"
        self.video_container.set_highlight_hud(txt, state)

    def _hl_on_position(self, t: float):
        """Appelé à chaque position pendant la revue : pause en fin de séquence."""
        if time.monotonic() - self._hl_seek_t0 < 0.25:
            return   # position périmée juste après un seek programmé
        if self._hl_idx < 0:
            idx = self._hl_index_at(t)
            if idx >= 0:
                self._hl_idx = idx
                self._hl_waiting = False
                self._hl_update_hud()
            return
        if self._hl_idx >= len(self._hl_segments):
            self._hl_idx = -1
            return
        s, e = self._hl_segments[self._hl_idx]
        if t < s - 0.05 or t > e + 0.6:
            self._hl_idx = self._hl_index_at(t)
            self._hl_waiting = False
            self._hl_update_hud()
            return
        if self.is_cutting:
            return
        if not self._hl_waiting and t >= e - 0.02:
            self.player.pause()
            self._hl_waiting = True
            self._hl_update_hud()

    def _delete_selected(self):
        item = self.action_list.currentItem()
        if not item: return
        aid = item.data(Qt.ItemDataRole.UserRole)
        self._push_history()
        self.actions_data = [a for a in self.actions_data if a.id != aid]
        self._normalize_actions()
        self._update_scores()
        self._refresh_log()
        self._refresh_timeline()
        self._save_session()

    # ── Score helpers ─────────────────────────────────────────────────────────

    def _compute_current_server(self) -> int | None:
        if not self.video_path:
            return self.first_server
        points = [a for a in self.actions_data if isinstance(a, PointAction)]
        if not points:
            return server_at(self.first_server, 0, 0, 0, flips_before(self._swap_times()))
        completed, (cur_p1, cur_p2) = compute_sets(self.actions_data)
        return server_at(self.first_server, len(completed), cur_p1, cur_p2,
                         flips_before(self._swap_times()))

    def _swap_times(self) -> list[float]:
        return [a.timecode for a in self.actions_data if isinstance(a, ServeSwapAction)]

    def _update_scores(self):
        completed, (cur_p1, cur_p2) = compute_sets(self.actions_data)
        self.scoreboard.update_scores(completed, cur_p1, cur_p2)
        self.scoreboard.set_serving(self._compute_current_server())
        self.video_container.update_score(
            self.scoreboard.p1_name(), self.scoreboard.p2_name(),
            completed, cur_p1, cur_p2,
        )

    # ── UI refresh ────────────────────────────────────────────────────────────

    @staticmethod
    def _action_time(a) -> float:
        return action_time(a)

    def _normalize_actions(self):
        """Remet la liste dans l'ordre de la video et recalcule les scores.

        A appeler apres toute modification de actions_data : c'est la timeline,
        et elle seule, qui definit l'ordre des points.
        """
        self.actions_data = recompute_point_fields(sort_actions(self.actions_data))

    def _action_by_id(self, aid):
        return next((a for a in self.actions_data if a.id == aid), None)

    @staticmethod
    def _fill_item(item, kind, title, sub, when):
        item.setData(ROLE_KIND, kind); item.setData(ROLE_TITLE, title)
        item.setData(ROLE_SUB, sub); item.setData(ROLE_TIME, when)

    def _refresh_log(self):
        # La liste est reconstruite : la sélection saute, mais ce n'est pas
        # l'utilisateur qui l'a demandé, donc pas de déplacement de la lecture.
        self._syncing_list = True
        self.action_list.clear()
        entries = []
        for a in self.actions_data:
            if isinstance(a, CutAction):
                entries.append(("cut", a))
            elif isinstance(a, RotateAction):
                entries.append(("rotate", a))
            elif isinstance(a, ServeSwapAction):
                entries.append(("swap", a))
            else:
                # Le score et le numéro de set sont recalculés en ordre
                # chronologique à chaque modification : on les lit, plus besoin
                # de les ré-accumuler ici (et l'étiquette collait à l'ordre de
                # pose alors que la liste, elle, s'affiche dans l'ordre vidéo).
                entries.append(("point", a, a.set_num))

        # Ordre de la vidéo, pas ordre de pose : c'est celui qu'on a en tête
        # quand on cherche quelle coupe garder. actions_data est déjà trié, ce
        # tri n'est plus qu'une ceinture. L'annulation, elle, reste dans l'ordre
        # de pose puisqu'elle travaille sur une pile d'instantanés.
        entries.sort(key=lambda e: self._action_time(e[1]))

        for entry in entries:
            if entry[0] == "cut":
                a = entry[1]
                item = QListWidgetItem()
                self._fill_item(item, "cut", "Coupe", f"jusqu'à {fmt_time(a.end)}  ·  {a.end-a.start:.1f} s",
                                fmt_time(a.start))
            elif entry[0] == "rotate":
                a = entry[1]
                item = QListWidgetItem()
                self._fill_item(item, "rot", f"Rotation {ROT_LABELS.get(a.angle, a.angle)}",
                                f"jusqu'à {fmt_time(a.end)}", fmt_time(a.start))
            elif entry[0] == "swap":
                a = entry[1]
                item = QListWidgetItem()
                self._fill_item(item, "swap", "Service inversé", "", fmt_time(a.timecode))
            else:
                _, a, set_n = entry
                name = self.scoreboard.p1_name() if a.player == 1 else self.scoreboard.p2_name()
                item = QListWidgetItem()
                self._fill_item(item, "p1" if a.player == 1 else "p2", f"Point {name}",
                                f"Score {a.score}  ·  set {set_n+1}", fmt_time(a.timecode))
            item.setData(Qt.ItemDataRole.UserRole, a.id)
            self.action_list.addItem(item)

        self.action_list.setCurrentRow(-1)
        self._list_follow_id = None
        self._syncing_list = False
        self._sync_list_to_playhead(self.current_time)

    def _refresh_timeline(self):
        self._hl_resync()
        self.timeline.refresh(self.actions_data, self.duration, self.current_time,
                               self.cut_start if self.is_cutting else None,
                               highlights=self._hl_marks,
                               review_span=self._hl_review_span())
        self._sync_preview_rotation()   # une rotation ajoutée/annulée se voit tout de suite
        self._hl_refresh_summary()

    # ── Export ────────────────────────────────────────────────────────────────

    def _free_space_warning(self) -> str | None:
        """Message d'alerte si le disque ne peut visiblement pas tenir l'export."""
        try:
            src_size = os.path.getsize(self.video_path)
            free = shutil.disk_usage(str(EXPORTS_DIR.parent)).free
        except OSError:
            return None
        cuts = [{"start": a.start, "end": a.end}
                for a in self.actions_data if isinstance(a, CutAction)]
        kept = _kept_duration(cuts, self.duration) if cuts else self.duration
        share = (kept / self.duration) if self.duration > 0 else 1.0
        # 1,3× la part conservée : le ré-encodage vise le débit de la source, plus
        # la carte de stats et une marge pour le fichier temporaire.
        needed = int(src_size * share * 1.3) + 300_000_000
        if free >= needed:
            return None
        return (f"Espace disque insuffisant : {free / 1e9:.1f} Go libres, "
                f"il en faut environ {needed / 1e9:.1f} Go. "
                f"Libère de l'espace sur le disque, puis relance l'export.")

    def _actions_as_dicts(self) -> list[dict]:
        # Un seul passage chronologique : le score porté par chaque point vaut
        # après son propre point, et les sets terminés avec lui. (Avant, chaque
        # point relançait compute_sets sur une tranche prise par index, donc sur
        # l'ordre de pose : un point re-marqué après coup faussait l'export.)
        completed: list[tuple[int, int]] = []
        cur_p1 = cur_p2 = 0
        out: list[dict] = []
        for a in self.actions_data:
            if isinstance(a, PointAction):
                if a.player == 1: cur_p1 += 1
                else:             cur_p2 += 1
                if max(cur_p1, cur_p2) >= 11 and abs(cur_p1 - cur_p2) >= 2:
                    completed.append((cur_p1, cur_p2))
                    cur_p1 = cur_p2 = 0
                out.append({
                    "type": "point", "player": a.player,
                    "timecode": a.timecode, "score": a.score,
                    "completed_sets": list(completed),
                    "cur_p1": cur_p1, "cur_p2": cur_p2,
                })
            elif isinstance(a, RotateAction):
                out.append({"type": "rotate", "start": a.start, "end": a.end,
                            "angle": a.angle})
            elif isinstance(a, ServeSwapAction):
                out.append({"type": "swap", "timecode": a.timecode})
            else:
                out.append({"type": "cut", "start": a.start, "end": a.end})
        return out

    def _start_export(self, highlights_only: bool = False):
        if not self.video_path or not self.actions_data: return

        warning = self._free_space_warning()
        if warning:
            self._status_color("danger_text")
            self.export_status.setText(warning)
            self.export_status.show()
            return

        actions = self._actions_as_dicts()
        export_name = self.export_name_input.text().strip() or self.export_name_input.placeholderText() or "montage"
        self._export_is_highlights = False
        self._export_ignored = 0
        if highlights_only:
            # Même pipeline que le montage complet : on ajoute simplement une coupe
            # par séquence rejetée (ou non triée). Les points restent tous là, donc
            # le score et la carte de fin sont ceux du match entier.
            self._hl_resync()
            kept, _rej, todo = _highlight_counts(self._hl_status)
            if kept == 0:
                self._hl_status_msg("Aucune séquence gardée : passe en Mode highlight "
                                    "et appuie sur I sur les échanges à garder.", "danger_text")
                return
            actions += [{"type": "cut", "start": s, "end": e}
                        for (s, e), st in zip(self._hl_segments, self._hl_status)
                        if st is not True]
            export_name = f"{export_name}_highlights"
            self._export_is_highlights = True
            self._export_ignored = todo

        self.export_btn.setEnabled(False)
        self.hl_export_btn.setEnabled(False)
        self.export_btn.setText("⏳  Export en cours…")
        self.cancel_btn.setEnabled(True); self.cancel_btn.show()
        # Pendant la prépa : barre "indéterminée" (animation défilante) — l'attente vit.
        self._encode_started = False
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("Préparation…")
        self.progress_bar.show()
        self.export_time_lbl.setText("🎬  Préparation du montage…")
        self.export_time_lbl.show()
        self.export_status.hide()

        self._worker = ExportWorker(
            video_path=self.video_path,
            actions=actions,
            p1n=self.scoreboard.p1_name(),
            p2n=self.scoreboard.p2_name(),
            overlay_dur=self.overlay_spin.value(),
            font_size=44,
            duration=self.duration,
            export_name=export_name,
            first_server=self.first_server,
            p1_rank=self.scoreboard.p1_rank(),
            p2_rank=self.scoreboard.p2_rank(),
            stats_card=self.stats_card_btn.isChecked(),
            intro_card=self._intro_effective() if self.intro_btn.isChecked() else None,
            match_format=self.match_format,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.encode_started.connect(self._on_encode_started)
        self._worker.stage.connect(self._export_stage)
        self._worker.done.connect(self._export_done)
        self._worker.error.connect(self._export_error)
        self._worker.canceled.connect(self._export_canceled)
        self._worker.start()

    def _export_stage(self, text: str):
        # Messages d'étape pendant la préparation (avant l'encodage).
        if not self._encode_started:
            self.export_time_lbl.setText(text)

    def _cancel_export(self):
        # Le même bouton sert aux deux traitements : on annule celui qui tourne.
        tw = getattr(self, "_trail_worker", None)
        if tw is not None and tw.isRunning():
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("✖  Annulation…")
            tw.cancel()
            return
        if getattr(self, "_worker", None) is not None:
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("✖  Annulation…")
            self._worker.cancel()

    def _reset_export_ui(self):
        self.export_btn.setEnabled(True)
        self.export_btn.setText("🎬  Générer le montage")
        self._hl_refresh_summary()              # réactive « Exporter les highlights » si possible
        self.cancel_btn.hide(); self.cancel_btn.setText("✖  Annuler l'export")
        self.progress_bar.setRange(0, 1000)     # restaure le mode déterminé (1/10 de %)
        self.progress_bar.setFormat("Export…  0.0%")
        self.progress_bar.hide()
        self.export_time_lbl.hide()

    def _on_encode_started(self):
        # Fin de la prépa : la barre passe de "indéterminée" au pourcentage réel, et on
        # masque le libellé d'étape. Pendant l'encodage, on ne montre QUE le %.
        if not self._encode_started:
            self._encode_started = True
            # Résolution au 1/10 de % (Qt n'affiche que des entiers via %p%, donc on
            # élargit la plage et on écrit le texte nous-mêmes).
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("Export…  0.0%")
            self.export_time_lbl.hide()

    def _on_progress(self, pct: float):
        # Pendant la prépa la barre reste indéterminée ("Préparation…") : on n'écrase pas.
        if not self._encode_started:
            return
        self.progress_bar.setValue(int(pct * 10))
        self.progress_bar.setFormat(f"Export…  {pct:.1f}%")

    def _export_done(self, path: str):
        self._reset_export_ui()
        self._last_export = path      # source par défaut de la traînée
        if self.trail_chk.isChecked():
            # Le montage d'abord, la traînée ensuite : on ne détecte la balle
            # que sur les images qui restent au montage.
            self._start_trail(chained=True)
            return
        if self._export_is_highlights:
            extra = ""
            if self._export_ignored:
                n = self._export_ignored
                extra = f"\n({n} séquence{'s' if n > 1 else ''} non triée{'s' if n > 1 else ''} ignorée{'s' if n > 1 else ''})"
            self.export_status.setText(f"✓ Highlights enregistrés :\n{path}{extra}")
            self._status_color("success")
            self.export_status.show()
            return
        self.export_status.setText(f"✓ Montage enregistré :\n{path}")
        self._status_color("success")
        self.export_status.show()

    def _export_error(self, msg: str):
        self._reset_export_ui()
        self.export_status.setText(f"✗ {msg}")
        self._status_color("danger")
        self.export_status.show()

    def _export_canceled(self):
        self._reset_export_ui()
        self.export_status.setText("✖ Export annulé.")
        self._status_color("warn")
        self.export_status.show()

    # ── Traînée de balle ──────────────────────────────────────────────────────

    def _start_trail(self, chained: bool = False):
        # `chained` : appelé depuis _export_done, où le thread d'export vient
        # d'émettre son signal mais n'est pas encore tout à fait terminé.
        if (not chained and getattr(self, "_worker", None) is not None
                and self._worker.isRunning()):
            return
        if getattr(self, "_trail_worker", None) is not None and self._trail_worker.isRunning():
            return
        # Le montage d'abord, la traînée ensuite : si un export vient d'être
        # fait dans cette fenêtre, c'est lui qu'on décore.
        src = getattr(self, "_last_export", None)
        if not src or not Path(src).exists():
            src = self.video_path
        if not src:
            return
        out_name = f"{Path(src).stem}_trail"

        self.trail_chk.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.hl_export_btn.setEnabled(False)
        self.export_btn.setText("⏳  Traînée en cours…")
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("✖  Annuler la traînée")
        self.cancel_btn.show()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Traînée…  0.0%")
        self.progress_bar.show()
        self.export_time_lbl.setText(f"🎾  Source : {Path(src).name}")
        self.export_time_lbl.show()
        self.export_status.hide()

        self._trail_host = ""
        self._trail_worker = TrailWorker(str(src), out_name)
        self._trail_worker.machine.connect(self._on_trail_machine)
        self._trail_worker.progress.connect(self._on_trail_progress)
        self._trail_worker.stage.connect(self._on_trail_stage)
        self._trail_worker.done.connect(self._trail_done)
        self._trail_worker.error.connect(self._trail_error)
        self._trail_worker.canceled.connect(self._trail_canceled)
        self._trail_worker.start()

    def _on_trail_machine(self, name: str):
        # Savoir *où* ça calcule reste affiché dans la barre : tout tourne
        # désormais sur le Mac, mais l'étiquette dit ce qui travaille.
        self._trail_host = name
        self.progress_bar.setFormat(f"Traînée · {name}…  0.0%")

    def _on_trail_progress(self, pct: float):
        self.progress_bar.setValue(int(pct * 10))
        host = getattr(self, "_trail_host", "")
        label = f"Traînée · {host}" if host else "Traînée"
        self.progress_bar.setFormat(f"{label}…  {pct:.1f}%")

    def _on_trail_stage(self, text: str):
        if text:
            self.export_time_lbl.setText(text)

    def _reset_trail_ui(self):
        self.trail_chk.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.export_btn.setText("🎬  Générer le montage")
        self._hl_refresh_summary()
        self.cancel_btn.hide(); self.cancel_btn.setText("✖  Annuler l'export")
        self.progress_bar.setFormat("Export…  0.0%")
        self.progress_bar.hide()
        self.export_time_lbl.hide()

    def _trail_done(self, path: str):
        self._reset_trail_ui()
        self.export_status.setText(f"✓ Traînée enregistrée :\n{path}")
        self._status_color("success")
        self.export_status.show()

    def _trail_error(self, msg: str):
        self._reset_trail_ui()
        self.export_status.setText(f"✗ {msg}")
        self._status_color("danger")
        self.export_status.show()

    def _trail_canceled(self):
        self._reset_trail_ui()
        self.export_status.setText("✖ Traînée annulée.")
        self._status_color("warn")
        self.export_status.show()

    # ── Tableau de fin en image ───────────────────────────────────────────────

    def _export_card_png(self, btn: QPushButton | None = None):
        """Tableau de fin en image (PNG 4K, fond opaque), pour le partager."""
        from PIL import Image
        from pongedit.match.scoring import _compute_stats_from_dicts
        from pongedit.export.cards import _make_stats_card_png
        actions = self._actions_as_dicts()
        stats = _compute_stats_from_dicts(actions, self.first_server)
        if not stats:
            if btn is not None:
                btn.setText("Aucun point saisi")
            return
        points = sorted((a for a in actions if a["type"] == "point"), key=lambda p: p["timecode"])
        name = (self.export_name_input.text().strip()
                or self.export_name_input.placeholderText() or "match")
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = _reserve_output_path(EXPORTS_DIR / f"{name}_tableau.png")
        tmp = tempfile.NamedTemporaryFile(prefix="pong_card_", suffix=".png", delete=False)
        tmp.close()
        try:
            # Rendu à l'échelle d'une vidéo 4K : image nette pour un partage.
            _make_stats_card_png(stats, self.scoreboard.p1_name(), self.scoreboard.p2_name(),
                                 tmp.name, 3840, 2160,
                                 self.scoreboard.p1_rank(), self.scoreboard.p2_rank(),
                                 points_seq=[p["player"] for p in points])
            card = Image.open(tmp.name).convert("RGBA")
            flat = Image.new("RGBA", card.size, (11, 14, 20, 255))   # fond du tableau
            Image.alpha_composite(flat, card).convert("RGB").save(out)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        if btn is not None:
            btn.setText("✓ Enregistré dans pong_exports")
            btn.setToolTip(str(out))

    # ── Réglages d'export mémorisés ──────────────────────────────────────────

    def _export_toggles(self) -> dict[str, ToggleRow]:
        return {"stats_card": self.stats_card_btn,
                "intro": self.intro_btn,
                "trail": self.trail_chk}

    def _apply_export_prefs(self):
        """Remet les interrupteurs d'export tels que laissés au dernier lancement."""
        prefs = _read_export_prefs()
        for key, w in self._export_toggles().items():
            w.blockSignals(True)
            w.setChecked(prefs[key])
            w.blockSignals(False)
            w.update()
        for w in self._export_toggles().values():
            w.toggled.connect(self._save_export_prefs)

    def _save_export_prefs(self, *_):
        _write_export_prefs({k: w.isChecked() for k, w in self._export_toggles().items()})

    def _open_merge_dialog(self):
        """Fusion de plusieurs sources, puis chargement du résultat dans l'éditeur."""
        dlg = getattr(self, "_merge_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.showNormal(); dlg.raise_(); dlg.activateWindow()
            return

        # Non modale : une fusion dure parfois plusieurs minutes, on doit pouvoir
        # la réduire et continuer à travailler dans l'éditeur pendant ce temps.
        dlg = MergeDialog(self, initial_path=self.video_path)
        self._merge_dlg = dlg

        def _finished(code):
            path = dlg.result_path
            self._merge_dlg = None
            dlg.deleteLater()
            if code == QDialog.Accepted and path:
                self._load_video(path)

        dlg.finished.connect(_finished)
        dlg.show(); dlg.raise_(); dlg.activateWindow()

    def _check_updates(self):
        from pongedit import update_ui
        update_ui.version_menu(self, self.version_btn)

    def _open_new_window(self):
        try:
            _spawn_new_window()
        except Exception as e:
            self.export_status.setText(f"✗ Impossible d'ouvrir une nouvelle fenêtre : {e}")
            self._status_color("danger")
            self.export_status.show()

    def closeEvent(self, event):
        # Fermer la fenêtre coupe son export en cours (ffmpeg) au lieu de le laisser
        # tourner en orphelin et saturer le CPU. Pour laisser un export finir : garde
        # la fenêtre ouverte.
        w = getattr(self, "_worker", None)
        if w is not None and w.isRunning():
            w.cancel()
        tw = getattr(self, "_trail_worker", None)
        if tw is not None and tw.isRunning():
            tw.cancel()
            w.wait(3000)
        super().closeEvent(event)

    # ── Carte d'intro ─────────────────────────────────────────────────────────

    def _on_intro_toggled(self, _on: bool):
        self._save_session()

    def _intro_video_date(self) -> str:
        p = self.video_path or ""
        if p and p not in self._intro_date_cache:
            self._intro_date_cache[p] = _intro.video_date(p)
        return self._intro_date_cache.get(p, "")

    def _intro_auto(self) -> dict:
        """Préremplissage : tableau de score, mémoire (club, occasion), date."""
        return _intro.auto_values(self.scoreboard.p1_name(), self.scoreboard.p2_name(),
                                  self.scoreboard.p1_rank(), self.scoreboard.p2_rank(),
                                  _read_intro_memory(), self._intro_video_date())

    def _intro_effective(self) -> dict:
        return _intro.effective_values(self._intro_saved, self._intro_auto())

    def _open_intro_dialog(self):
        auto = self._intro_auto()
        dlg = IntroCardDialog(self, _intro.effective_values(self._intro_saved, auto), auto,
                              _read_intro_memory())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        self._intro_saved = {"values": vals, "auto": auto}
        _remember_intro(vals, [("p1", self.scoreboard.p1_name()),
                               ("p2", self.scoreboard.p2_name())])
        self._save_session()

    def _copy_youtube_timecodes(self):
        # Intro ON : le montage commence par l'image figée de l'intro, les
        # chapitres (sauf « Set 1 » à 0:00) sont décalés d'autant.
        text = _youtube_chapters_from_actions(
            self.actions_data,
            self.duration,
            self.overlay_spin.value(),
            offset=_intro.INTRO_DUR if self.intro_btn.isChecked() else 0.0,
        )
        if not text:
            self.export_status.setText("✗ Ajoute au moins un point pour générer les timecodes.")
            self._status_color("danger")
            self.export_status.show()
            return
        QApplication.clipboard().setText(text)
        self.export_status.setText(f"✓ Timecodes copiés :\n{text}")
        self._status_color("success")
        self.export_status.show()

    # ── Session persistence ───────────────────────────────────────────────────

    def _session_path(self) -> Path | None:
        if not self._video_hash_val: return None
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        return SESSIONS_DIR / f"{self._video_hash_val}.json"

    def _save_session(self):
        sp = self._session_path()
        if not sp: return
        data = {
            "version": 4,
            "video_filename": Path(self.video_path).name,
            "p1_name": self.scoreboard.p1_name(),
            "p2_name": self.scoreboard.p2_name(),
            "p1_rank": self.scoreboard.p1_rank(),
            "p2_rank": self.scoreboard.p2_rank(),
            "first_server": self.first_server,
            "match_format": self.match_format,
            "export_name": self.export_name_input.text().strip(),
            "actions": [
                {
                    "type": "point", "player": a.player,
                    "timecode": a.timecode, "score": a.score,
                    "set_num": a.set_num,
                    "completed_sets": [list(s) for s in a.completed_sets],
                } if isinstance(a, PointAction) else
                {"type": "rotate", "start": a.start, "end": a.end, "angle": a.angle}
                if isinstance(a, RotateAction) else
                {"type": "swap", "timecode": a.timecode}
                if isinstance(a, ServeSwapAction) else
                {"type": "cut", "start": a.start, "end": a.end}
                for a in self.actions_data
            ],
            # Tri highlight (v4) : par bornes de segment conservé.
            "highlights": [
                {"start": float(d["start"]), "end": float(d["end"]), "keep": bool(d["keep"])}
                for d in self._highlights
            ],
            # Carte d'intro (absente avant) : ON/OFF + saisie du dialogue.
            "intro": {"enabled": self.intro_btn.isChecked(),
                      **(self._intro_saved or {})},
        }
        try:
            _atomic_write_text(sp, json.dumps(data, indent=2))
        except Exception as e:
            print(f"Session save failed: {e}")

    def _load_session(self):
        sp = self._session_path()
        if not sp or not sp.exists(): return
        try:
            data = json.loads(sp.read_text())
            _inputs = (
                (self.scoreboard.p1_input, data.get("p1_name", "Player 1")),
                (self.scoreboard.p2_input, data.get("p2_name", "Player 2")),
                (self.scoreboard.p1_rank_input, str(data.get("p1_rank", "") or "")),
                (self.scoreboard.p2_rank_input, str(data.get("p2_rank", "") or "")),
            )
            for _inp, _val in _inputs:
                _inp.blockSignals(True)
                _inp.setText(_val)
                _inp.blockSignals(False)
            self._set_first_server(data.get("first_server", 1))
            self.export_name_input.blockSignals(True)
            self.export_name_input.setText(str(data.get("export_name", "") or ""))
            self.export_name_input.blockSignals(False)
            self._set_match_format(data.get("match_format", 5))
            self.actions_data.clear()
            for a in data.get("actions", []):
                if a["type"] == "point":
                    self.actions_data.append(PointAction(
                        id=gen_id(), player=a["player"],
                        timecode=a["timecode"], score=a["score"],
                        set_num=a.get("set_num", 0),
                        completed_sets=[tuple(s) for s in a.get("completed_sets", [])],
                    ))
                elif a["type"] == "cut":
                    self.actions_data.append(CutAction(
                        id=gen_id(), start=a["start"], end=a["end"],
                    ))
                elif a["type"] == "rotate":
                    self.actions_data.append(RotateAction(
                        id=gen_id(), start=a["start"], end=a["end"],
                        angle=int(a.get("angle", 90)),
                    ))
                elif a["type"] == "swap":
                    self.actions_data.append(ServeSwapAction(
                        id=gen_id(), timecode=a["timecode"],
                    ))
            # Les sessions enregistrées avant la fusion peuvent contenir des
            # intervalles qui se recouvrent : on les uniformise au chargement.
            self.actions_data = _merge_overlapping_spans(self.actions_data)
            # Les sessions d'avant le tri chronologique gardent l'ordre de pose,
            # donc des scores faux dès qu'un point avait été re-marqué : on les
            # remet d'aplomb ici, et la prochaine sauvegarde les réécrit propres.
            self._normalize_actions()
            intro = data.get("intro")
            if isinstance(intro, dict):
                vals, snap = intro.get("values"), intro.get("auto")
                if isinstance(vals, dict) and isinstance(snap, dict):
                    self._intro_saved = {"values": {k: str(vals.get(k, "") or "") for k in _intro.FIELDS},
                                         "auto": {k: str(snap.get(k, "") or "") for k in _intro.FIELDS}}
            self._highlights = []
            for d in data.get("highlights", []):   # absent en v3 et avant
                try:
                    self._highlights.append({"start": float(d["start"]),
                                             "end": float(d["end"]),
                                             "keep": bool(d["keep"])})
                except (KeyError, TypeError, ValueError):
                    continue
            self._update_scores()
            self._refresh_log()
            self._refresh_timeline()
            self._update_export_placeholder()
            self._update_srv_btn_labels()
            print(f"Session loaded: {len(self.actions_data)} actions")
        except Exception as e:
            print(f"Session load failed: {e}")

    # ── Style ─────────────────────────────────────────────────────────────────

    def _status_color(self, key: str):
        """Couleur du message d'état (clé de la palette UI), suivie au changement de thème."""
        themed(self.export_status, lambda: f"color:{UI[key]};font-size:11px;")

    @staticmethod
    def _theme_icon(dark: bool) -> QIcon:
        """Soleil (thème sombre actif → aller au clair) ou croissant de lune."""
        from PySide6.QtGui import QPixmap, QPainterPath
        from PySide6.QtCore import QPointF
        dpr, sz = 2, 20
        pm = QPixmap(sz * dpr, sz * dpr); pm.setDevicePixelRatio(dpr); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        col = QColor(UI["text"]); c = QPointF(sz / 2, sz / 2)
        if dark:
            p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap)); p.setBrush(col)
            p.drawEllipse(c, 3.6, 3.6)
            for i in range(8):
                a = math.radians(i * 45)
                p.drawLine(QPointF(c.x() + 6.2 * math.cos(a), c.y() + 6.2 * math.sin(a)),
                           QPointF(c.x() + 8.4 * math.cos(a), c.y() + 8.4 * math.sin(a)))
        else:
            moon = QPainterPath(); moon.addEllipse(c, 7.5, 7.5)
            cut = QPainterPath(); cut.addEllipse(QPointF(c.x() + 4, c.y() - 3.2), 6.4, 6.4)
            p.setPen(Qt.NoPen); p.setBrush(col); p.drawPath(moon.subtracted(cut))
        p.end()
        return QIcon(pm)

    def _update_theme_btn(self):
        dark = is_dark()
        self.theme_btn.setIcon(self._theme_icon(dark))
        self.theme_btn.setIconSize(QSize(20, 20))
        self.theme_btn.setToolTip("Passer en thème clair" if dark else "Passer en thème sombre")

    def _toggle_theme(self):
        """Bascule sombre/clair avec un révélateur circulaire parti du bouton :
        l'ancien rendu reste affiché par-dessus et un trou circulaire grandissant
        laisse apparaître le nouveau thème."""
        if getattr(self, "_theme_anim", None) is not None:
            return
        from PySide6.QtCore import QVariantAnimation, QEasingCurve, QPoint
        from PySide6.QtGui import QRegion
        old = self.grab()
        center = self.theme_btn.mapTo(self, self.theme_btn.rect().center())
        new_dark = not is_dark()
        save_theme_choice(new_dark)
        apply_palette(new_dark)
        self.apply_theme()
        self._update_theme_btn()

        ov = QLabel(self)
        ov.setPixmap(old)
        ov.setGeometry(self.rect())
        ov.setAttribute(Qt.WA_TransparentForMouseEvents)
        ov.show(); ov.raise_()
        w, h = self.width(), self.height()
        r_max = int(max(math.hypot(center.x() - x, center.y() - y)
                        for x in (0, w) for y in (0, h))) + 2

        def step(r):
            r = int(r)
            hole = QRegion(center.x() - r, center.y() - r, 2 * r, 2 * r, QRegion.Ellipse)
            mask = QRegion(0, 0, w, h).subtracted(hole)
            if mask.isEmpty():
                # Un masque vide = « pas de masque » pour Qt : l'ancien rendu
                # réapparaîtrait en plein écran une image avant la fin.
                ov.hide()
            else:
                ov.setMask(mask)

        def done():
            ov.hide(); ov.deleteLater()
            self._theme_anim = None

        anim = QVariantAnimation(self)
        anim.setStartValue(0.0); anim.setEndValue(float(r_max))
        anim.setDuration(650)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.valueChanged.connect(step)
        anim.finished.connect(done)
        self._theme_anim = anim
        step(0)
        anim.start()

    def apply_theme(self):
        """Ré-applique toutes les couleurs après un changement de `UI` (thème système).
        Seules les couleurs changent : aucune taille ni marge n'est touchée."""
        self.setUpdatesEnabled(False)
        try:
            self._apply_styles()
            restyle_registered()
            if hasattr(self, "theme_btn"):
                self._update_theme_btn()
            self.timeline.theme_changed()
            self.action_list.viewport().update()
            self.timeline.update()
        finally:
            self.setUpdatesEnabled(True)
        self.update()

    def _recolor_log(self):
        """Recolore les lignes de la liste d'actions sans la reconstruire
        (sélection et défilement conservés)."""
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            a = self._action_by_id(item.data(Qt.ItemDataRole.UserRole))
            if isinstance(a, CutAction):
                item.setForeground(QColor(UI["danger"]))
            elif isinstance(a, RotateAction):
                item.setForeground(QColor(UI["rot"]))
            elif isinstance(a, ServeSwapAction):
                item.setForeground(QColor(UI["muted"]))
            elif isinstance(a, PointAction):
                item.setForeground(QColor(UI["p1"]) if a.player == 1 else QColor(UI["p2"]))

    def _apply_styles(self):
        # Palette : jetons UI du thème actif (sombre ou clair, cf. style.py).
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background:{UI['bg']}; color:{UI['text']}; font-size:13px;
            }}
            QDialog {{ background:{UI['bg']}; }}

            QToolTip {{
                background:{UI['raised']}; color:{UI['text']};
                border:1px solid {UI['border']}; border-radius:6px;
                padding:6px 9px; font-size:12px;
            }}

            /* ── Cartes de section ── */
            QFrame#card {{
                background:{UI['surface']}; border:1px solid {UI['border']};
                border-radius:10px;
            }}
            QLabel {{ background:transparent; }}
            QWidget#scoreboard, QWidget#scoreboard > QWidget {{ background:transparent; }}
            QLabel#sectionTitle {{
                color:{UI['muted']}; font-size:10px; font-weight:700;
                letter-spacing:2px; background:transparent; padding:0;
            }}
            QPushButton#sectionToggle {{
                background:transparent; border:none; padding:0; min-height:0;
                color:{UI['muted']}; font-size:10px; font-weight:700;
                letter-spacing:2px; text-align:left;
            }}
            QPushButton#sectionToggle:hover {{ color:{UI['text']}; background:transparent; }}
            QLabel#fieldLabel {{
                color:{UI['muted']}; font-size:11px; background:transparent;
            }}
            QLabel#kbd {{
                background:{UI['raised']}; color:{UI['text']}; border:1px solid {UI['border']};
                border-radius:4px; padding:1px 5px; font-size:10px; font-weight:700;
            }}
            QLabel#hint {{ color:{UI['muted']}; font-size:11px; background:transparent; }}
            QLabel#badge {{
                background:{UI['raised']}; color:{UI['muted']}; border:1px solid {UI['border']};
                border-radius:8px; padding:0 14px; font-size:12px; font-weight:700;
            }}

            /* ── Boutons ── */
            QPushButton {{
                background:{UI['raised']}; color:{UI['text']}; border:1px solid {UI['border']};
                padding:7px 14px; border-radius:8px; min-height:20px;
            }}
            QPushButton:hover    {{ background:{UI['hover']}; border-color:{UI['border_hover']}; }}
            QPushButton:pressed  {{ background:{UI['surface']}; }}
            QPushButton:disabled {{ background:{UI['surface']}; color:{UI['faint']}; border-color:{UI['border']}; }}

            QPushButton[variant="primary"] {{
                background:{UI['accent']}; color:{UI['on_accent']}; border:none;
                font-weight:700; padding:9px 16px;
            }}
            /* Bouton bleu dans la barre d'outils : sa marge verticale de 9 px le rendait
               plus haut (38 px) que ses voisins (34 px). */
            QPushButton[variant="primary"][inToolbar="true"] {{ padding:0 16px; }}
            QPushButton[variant="primary"]:hover    {{ background:{UI['accent_hover']}; }}
            QPushButton[variant="primary"]:pressed  {{ background:{UI['accent_pressed']}; }}
            QPushButton[variant="primary"]:disabled {{ background:{UI['accent_dis_bg']}; color:{UI['accent_dis_fg']}; }}

            QPushButton[variant="danger"] {{
                background:transparent; color:{UI['danger']};
                border:1px solid {UI['danger_bg']}; font-size:12px;
            }}
            QPushButton[variant="danger"]:hover   {{ background:{UI['danger_bg']}; color:{UI['danger_soft']}; }}
            QPushButton[variant="danger"]:pressed {{ background:{UI['danger_pressed']}; }}

            QPushButton[variant="ghost"] {{
                background:transparent; color:{UI['muted']};
                border:1px solid {UI['border']}; font-size:12px;
            }}
            QPushButton[variant="ghost"]:hover   {{ background:{UI['raised']}; color:{UI['text']}; }}
            QPushButton[variant="ghost"]:pressed {{ background:{UI['surface']}; }}

            QPushButton[variant="toolbar"] {{
                background:{UI['raised']}; color:{UI['muted']}; border:1px solid {UI['border']};
                padding:0 10px; border-radius:8px;
            }}
            QPushButton[variant="toolbar"]:hover   {{ background:{UI['raised']}; color:{UI['text']}; }}
            QPushButton[variant="toolbar"]:checked {{ background:{UI['danger_bg']}; color:{UI['danger_text']}; border-color:{UI['danger_edge']}; }}
            QPushButton#hlBtn:checked {{ background:{UI['success_bg']}; color:{UI['success']}; border-color:{UI['success_edge']}; }}

            QPushButton[variant="seg"] {{
                background:transparent; color:{UI['muted']}; border:none;
                border-radius:6px; font-size:11px; padding:4px 10px; min-height:20px;
            }}
            QPushButton[variant="seg"]:hover   {{ color:{UI['text']}; background:{UI['hover']}; }}
            QPushButton[variant="seg"]:checked {{ background:{UI['accent']}; color:{UI['on_accent']}; font-weight:700; }}
            QWidget#segGroup {{ background:{UI['raised']}; border:1px solid {UI['border']}; border-radius:8px; }}

            /* ── Champs ── */
            QLineEdit {{
                background:{UI['raised']}; border:1px solid {UI['border']};
                border-radius:8px; padding:6px 10px; color:{UI['text']};
                selection-background-color:{UI['accent']}; selection-color:{UI['on_accent']};
            }}
            QLineEdit:hover {{ border-color:{UI['border_hover']}; }}
            QLineEdit:focus {{ border:1px solid {UI['accent']}; }}
            QLineEdit::placeholder {{ color:{UI['faint']}; }}

            QSpinBox, QDoubleSpinBox {{
                background:{UI['raised']}; border:1px solid {UI['border']};
                border-radius:8px; padding:5px 8px; color:{UI['text']};
            }}
            QSpinBox:focus, QDoubleSpinBox:focus {{ border-color:{UI['accent']}; }}
            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {{ width:0; border:none; }}

            QCheckBox {{ color:{UI['muted']}; font-size:12px; spacing:8px; background:transparent; }}
            QCheckBox::indicator {{
                width:16px; height:16px; border-radius:5px;
                border:1px solid {UI['border']}; background:{UI['raised']};
            }}
            QCheckBox::indicator:hover {{ border-color:{UI['accent']}; }}
            QCheckBox::indicator:checked {{ background:{UI['accent']}; border-color:{UI['accent']}; }}

            /* ── Liste déroulante ── */
            QComboBox {{
                background:{UI['raised']}; color:{UI['text']};
                border:1px solid {UI['border']}; border-radius:8px;
                padding:6px 28px 6px 12px; min-height:18px;
            }}
            QComboBox:hover    {{ border-color:{UI['border_hover']}; }}
            QComboBox:disabled {{ color:{UI['faint']}; }}
            QComboBox::drop-down {{ border:none; width:24px; }}
            QComboBox::down-arrow {{
                image:none; width:0; height:0;
                border-left:4px solid transparent; border-right:4px solid transparent;
                border-top:5px solid {UI['muted']}; margin-right:10px;
            }}
            QComboBox QAbstractItemView {{
                background:{UI['raised']}; color:{UI['text']};
                border:1px solid {UI['border']}; border-radius:8px;
                selection-background-color:{UI['accent']}; selection-color:{UI['on_accent']};
                outline:none; padding:4px;
            }}

            /* ── Listes ── */
            QListWidget {{
                background:{UI['bg']}; border:1px solid {UI['border']}; border-radius:8px;
                font-size:12px; padding:4px; outline:none;
            }}
            QListWidget::item {{
                padding:6px 10px; border-radius:6px; border-left:3px solid transparent;
                color:{UI['text']};
            }}
            QListWidget::item:hover    {{ background:{UI['surface']}; }}
            QListWidget::item:selected {{
                background:{UI['selected']}; border-left:3px solid {UI['accent']}; color:{UI['text']};
            }}

            /* ── Barre de progression ── */
            QProgressBar {{
                border:none; border-radius:4px; background:{UI['raised']};
                text-align:center; color:{UI['text']}; font-size:11px; font-weight:700;
                min-height:18px; max-height:18px;
            }}
            QProgressBar::chunk {{ background:{UI['accent']}; border-radius:4px; }}

            /* ── Ascenseurs ── */
            QScrollBar:vertical {{ background:transparent; width:8px; margin:2px; }}
            QScrollBar::handle:vertical {{ background:{UI['border']}; border-radius:4px; min-height:24px; }}
            QScrollBar::handle:vertical:hover {{ background:{UI['border_hover']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
            QScrollBar:horizontal {{ background:transparent; height:8px; margin:2px; }}
            QScrollBar::handle:horizontal {{ background:{UI['border']}; border-radius:4px; min-width:24px; }}
            QScrollBar::handle:horizontal:hover {{ background:{UI['border_hover']}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}

            QSplitter::handle {{ background:{UI['border']}; }}
        """ + ("" if is_dark() else
               # Thème clair : coin entre les deux ascenseurs, sinon carré blanc visible.
               f"QAbstractScrollArea::corner {{ background:{UI['bg']}; border:none; }}"))
