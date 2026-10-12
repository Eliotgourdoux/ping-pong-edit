"""Verre liquide « Cockpit » : voile de fenêtre (flou natif macOS sinon voile peint) et halos.

Règles : aucune animation en boucle ; le voile est mis en cache dans un pixmap (repeint
uniquement au redimensionnement) ; un seul QGraphicsDropShadowEffect par widget, jamais
sur la vidéo ni la timeline. Variable d'environnement PONGEDIT_NO_GLASS=1 : fond opaque.
"""

import os
import sys

import time

from PySide6.QtCore import Qt, QEvent, QObject, QRectF, QPointF, QRect, QTimer
from PySide6.QtGui import (QColor, QPainter, QPixmap, QRadialGradient, QBrush, QLinearGradient,
                           QPainterPath, QPen, QRegion, QImage)
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget, QFrame

VIOLET = "#7F93B5"       # neutralisé (bleu-gris) : la palette n'a plus de violet
ACCENT = QColor(92, 200, 255)   # seule couleur d'accent (cyan-bleu)


def add_glow(w: QWidget, color: str = VIOLET, radius: int = 18, alpha: int = 140, dy: int = 0):
    """Halo néon : UN effet par widget (c'est rendu hors écran à chaque repaint)."""
    c = QColor(color); c.setAlpha(alpha)
    e = QGraphicsDropShadowEffect(w)
    e.setColor(c); e.setBlurRadius(radius); e.setOffset(0, dy)
    w.setGraphicsEffect(e)
    return e


class Backdrop(QWidget):
    """Voile gris-bleu nuit + 2 taches de lumière neutres très faibles, mises en cache."""

    def __init__(self, parent: QWidget, opacity: float = 0.82):
        super().__init__(parent)
        self._opacity = opacity
        self._cache: QPixmap | None = None
        self._cache_key = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, opacity >= 0.999)
        self.setGeometry(parent.rect())
        self.lower()
        parent.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
            self.lower()
        return False

    def _render(self, w: int, h: int, dpr: float) -> QPixmap:
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.fillRect(0, 0, w, h, QColor(18, 21, 28, int(255 * self._opacity)))
        for fx, fy, rad, col in ((0.14, 0.04, 0.62, QColor(120, 150, 190, 24)),
                                 (0.92, 0.96, 0.55, QColor(90, 110, 140, 18))):
            r = max(w, h) * rad
            g = QRadialGradient(w * fx, h * fy, r)
            g.setColorAt(0, col); g.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
            p.fillRect(0, 0, w, h, QBrush(g))
        p.end()
        return pm

    def paintEvent(self, _):
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        key = (w, h, dpr)
        if self._cache is None or self._cache_key != key:
            self._cache, self._cache_key = self._render(w, h, dpr), key
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawPixmap(0, 0, self._cache)


def glass_dialog(dlg: QWidget) -> None:
    """Fenêtre secondaire : voile peint opaque (robuste, aucun flou)."""
    Backdrop(dlg, opacity=1.0)


def glass_window(win: QWidget) -> str:
    """Fenêtre principale : fond translucide + flou natif macOS (NSVisualEffectView sous le
    contenu Qt) ; sinon voile peint plus opaque. Renvoie "native", "painted" ou "opaque"."""
    if os.environ.get("PONGEDIT_NO_GLASS") == "1":
        Backdrop(win, opacity=1.0)
        return "opaque"
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    if sys.platform == "darwin":
        try:
            import objc
            from AppKit import (NSVisualEffectView, NSVisualEffectBlendingModeBehindWindow,
                                NSVisualEffectStateActive, NSAppearance, NSViewWidthSizable,
                                NSViewHeightSizable, NSWindowBelow)
            win.winId()   # force la création de la fenêtre native
            ns_view = objc.objc_object(c_void_p=int(win.winId()))
            host = ns_view.superview()
            if host is not None:
                fx = NSVisualEffectView.alloc().initWithFrame_(host.bounds())
                fx.setMaterial_(13)  # HUDWindow
                fx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
                fx.setState_(NSVisualEffectStateActive)
                fx.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
                fx.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                host.addSubview_positioned_relativeTo_(fx, NSWindowBelow, ns_view)
                win._glass_fx = fx
                Backdrop(win, opacity=0.55)
                return "native"
        except Exception as e:  # pragma: no cover
            print(f"Verre natif indisponible : {e}")
    Backdrop(win, opacity=0.9)
    return "painted"



# ── Verre peint ──────────────────────────────────────────────────────────────
EDGE_ALPHA = int(os.environ.get("PONGEDIT_GLASS_EDGE", "16"))   # liseré uniforme très discret


def paint_glass(p: QPainter, r: QRectF, radius: float, glow: float = 1.0, edge: float = 1.0,
                outline: bool = True):
    """Une vitre : voile sombre neutre, dégradé blanc 0.07 -> 0.01, lueur d'accent douce depuis
    le coin haut gauche, et une LUMIÈRE douce qui tombe du bord supérieur (dégradé 0.06 -> 0 sur
    ~35 % de la hauteur, plus vive vers le coin haut gauche) — pas de filet de reflet.
    Liseré uniforme à peine visible pour dessiner la forme. Sert à remplir un pixmap en cache."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = min(radius, r.height() / 2, r.width() / 2)
    path = QPainterPath(); path.addRoundedRect(r, radius, radius)
    p.fillPath(path, QColor(6, 9, 16, 84))
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, QColor(255, 255, 255, 18)); g.setColorAt(1, QColor(255, 255, 255, 3))
    p.fillPath(path, QBrush(g))
    p.save(); p.setClipPath(path)
    if glow > 0:
        c = QColor(ACCENT); c.setAlpha(int(16 * glow))
        c0 = QColor(ACCENT); c0.setAlpha(0)
        rg = QRadialGradient(r.topLeft() + QPointF(r.width() * 0.08, 0), max(r.width() * 0.75, 60))
        rg.setColorAt(0, c); rg.setColorAt(1, c0)
        p.fillRect(r, QBrush(rg))
    # lumière du haut : nappe verticale douce + halo plus vif près du coin haut gauche
    hl = max(18.0, min(r.height() * 0.36, 140.0))
    lg = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + hl))
    lg.setColorAt(0, QColor(255, 255, 255, int(16 * edge)))
    lg.setColorAt(0.45, QColor(255, 255, 255, int(6 * edge)))
    lg.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(r.left(), r.top(), r.width(), hl), QBrush(lg))
    sh = QRadialGradient(QPointF(r.left() + r.width() * 0.22, r.top()), max(r.width() * 0.5, 50))
    sh.setColorAt(0, QColor(255, 255, 255, int(14 * edge))); sh.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(r, QBrush(sh))   # tout le rect : le dégradé radial s'éteint seul (pas de couture)
    p.restore()
    if outline and EDGE_ALPHA > 0:
        p.setPen(QPen(QColor(255, 255, 255, EDGE_ALPHA), 1.0)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class GlassPanel(QFrame):
    """Vitre arrondie peinte à la main ; pixmap en cache, repeint seulement au redimensionnement."""

    def __init__(self, radius: float = 22, glow: float = 1.0, parent=None):
        super().__init__(parent)
        self._radius, self._glow = radius, glow
        self._cache: QPixmap | None = None
        self._key = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def _render(self, w, h, dpr) -> QPixmap:
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr); pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm); paint_glass(q, QRectF(0, 0, w, h), self._radius, self._glow); q.end()
        return pm

    def paintEvent(self, _):
        key = (self.width(), self.height(), self.devicePixelRatioF())
        if self._cache is None or self._key != key:
            self._cache, self._key = self._render(*key), key
        QPainter(self).drawPixmap(0, 0, self._cache)


class GlassCard(GlassPanel):
    def __init__(self, parent=None):
        super().__init__(radius=22, glow=1.0, parent=parent)


class _Corner(QWidget):
    """Un « coin inversé » posé sur un coin de l'image : carré R×R moins le quart de disque de
    rayon R, rempli (anti-aliasé) avec le fond exact du cadre (vitre + lumière d'ambiance) à cet
    endroit. Le fond est une petite tuile en cache, refaite seulement quand la lumière change
    (≤ 8 Hz) ou au redimensionnement. Pas de masque, pas de QGraphicsEffect."""

    def __init__(self, stage, corner: int):
        super().__init__(stage)
        self._stage, self._corner = stage, corner      # 0 HG, 1 HD, 2 BG, 3 BD
        self._tile: QImage | None = None
        self._path: QPainterPath | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_rect(self, r: QRect):
        self.setGeometry(r)
        R = float(r.width())
        sq = QPainterPath(); sq.addRect(QRectF(0, 0, R, R))
        cx = R if self._corner in (0, 2) else 0.0
        cy = R if self._corner in (0, 1) else 0.0
        disc = QPainterPath(); disc.addEllipse(QPointF(cx, cy), R, R)
        self._path = sq.subtracted(disc)
        self.refresh()

    def refresh(self):
        r = self.geometry()
        if r.width() <= 0:
            return
        dpr = self.devicePixelRatioF()
        img = QImage(max(1, round(r.width() * dpr)), max(1, round(r.height() * dpr)),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.translate(-r.x(), -r.y())
        self._stage.paint_background(q, r)
        q.end()
        self._tile = img
        self.update()

    def paintEvent(self, _):
        if self._tile is None or self._path is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._tile))
        p.drawPath(self._path)


class VideoStage(GlassPanel):
    """Vitre arrondie autour de la vidéo + lumière d'ambiance (ambilight sobre).

    - Le conteneur vidéo est ajusté au format de l'image : opaque, sans bandes noires, sans
      transparence (aucun surcoût de repaint en lecture).
    - La lumière suit les couleurs de l'image : une miniature (32 px de large) de la frame
      courante, prise sur le QVideoSink de l'item (aucun second décodage), est lissée dans le
      temps, désaturée, étendue par réplication des bords puis posée en halo flou autour de
      l'image au travers d'un masque de dégradé pré-calculé au redimensionnement. Mise à jour
      à ~8 Hz seulement (et seulement tant que ça bouge) ; en pause, à la frame affichée.
      Aucun QGraphicsEffect, aucun flou par image."""

    PAD = 6                    # vitre -> image
    OUTX = 8                  # marge hors vitre (gauche/droite) où le halo peut déborder
    OUTY = 6                   # idem haut/bas
    RADIUS = 12                # coins de l'image ; vitre = RADIUS + PAD (concentriques)
    TW = 32                    # largeur de la miniature
    TICK_MS = 125              # ~8 Hz
    STRENGTH = 0.75            # opacité maxi du halo (additif : ne peut qu'éclairer)

    def __init__(self, inner: QWidget, parent=None):
        super().__init__(radius=self.RADIUS + self.PAD, glow=0.7, parent=parent)
        self._inner = inner
        inner.setParent(self)
        inner.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)   # Qt ne repeint pas la vitre sous l'image
        self._ratio = 16 / 9
        self._corners = [_Corner(self, i) for i in range(4)]
        self._amb: QImage | None = None        # halo rendu (basse résolution, déjà masqué)
        self._amb_rect = QRectF()
        self._mask_cache: QImage | None = None
        self._mask_key = None
        self._cur: QImage | None = None        # miniature lissée (courante)
        self._target: QImage | None = None
        self._last_frame = None
        self._dirty = False
        self._timer = QTimer(self); self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._fallback = QColor(82, 106, 140)
        self.setMinimumSize(300, 200)

    # ── lien avec la vidéo ──
    def attach(self, video_item):
        sink = video_item.videoSink()
        sink.videoFrameChanged.connect(self._on_frame)

    def _on_frame(self, frame):
        # Appelé à chaque image : on ne fait que mémoriser (très bon marché).
        self._last_frame = frame
        self._dirty = True
        if not self._timer.isActive():
            self._timer.start()

    def _thumb_size(self):
        return self.TW, max(2, round(self.TW / self._ratio))

    def _sample(self):
        fr, self._last_frame = self._last_frame, None
        if fr is None or not fr.isValid():
            return None
        img = fr.toImage()
        if img.isNull():
            return None
        tw, th = self._thumb_size()
        img = img.scaled(tw * 4, th * 4, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.FastTransformation)
        img = img.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        # sobriété : on mélange avec un gris-bleu (désaturation ~30 %) ; le plafond de
        # luminosité vient de STRENGTH + du mode additif
        q = QPainter(img); q.fillRect(img.rect(), QColor(104, 112, 128, 80)); q.end()
        return img

    def _tick(self):
        tw, th = self._thumb_size()
        if self._dirty:
            self._dirty = False
            t = self._sample()
            if t is not None:
                self._target = t
        if self._target is None:
            self._target = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
            self._target.fill(self._fallback)
        if self._cur is None or self._cur.size() != self._target.size():
            self._cur = QImage(self._target)
            converged = True
        else:
            # lissage temporel : cur <- cur + 0.5 (target - cur), ~0,3 s pour s'installer
            q = QPainter(self._cur); q.setOpacity(0.5); q.drawImage(0, 0, self._target); q.end()
            converged = self._close(self._cur, self._target)
        self._render_amb()
        self._refresh_corners()
        self.update()
        if converged and not self._dirty and self._last_frame is None:
            self._idle = getattr(self, "_idle", 0) + 1
            if self._idle >= 3:
                self._idle = 0
                self._timer.stop()
        else:
            self._idle = 0

    @staticmethod
    def _close(a: QImage, b: QImage) -> bool:
        # comparaison grossière sur 4 pixels
        for x, y in ((0, 0), (a.width() - 1, 0), (0, a.height() - 1), (a.width() // 2, a.height() // 2)):
            ca, cb = QColor(a.pixel(x, y)), QColor(b.pixel(x, y))
            if abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue()) > 6:
                return False
        return True

    # ── géométrie ──
    def set_native_size(self, w: float, h: float):
        if w > 0 and h > 0 and abs(w / h - self._ratio) > 1e-3:
            self._ratio = w / h
            self._cur = self._target = None
            self._relayout()

    def _glass_rect(self) -> QRectF:
        """La vitre épouse l'image (+ PAD) : pas de vitre vide quand il reste de la hauteur."""
        ir = self._inner_rect()
        return QRectF(ir).adjusted(-self.PAD, -self.PAD, self.PAD, self.PAD)

    def _inner_rect(self) -> QRect:
        m = self.PAD
        a = self.rect().adjusted(self.OUTX + m, self.OUTY + m, -self.OUTX - m, -self.OUTY - m)
        if a.width() <= 0 or a.height() <= 0:
            return a
        w = a.width(); h = round(w / self._ratio)
        if h > a.height():
            h = a.height(); w = round(h * self._ratio)
        return QRect(a.x() + (a.width() - w) // 2, a.y() + (a.height() - h) // 2, w, h)

    def _relayout(self):
        ir = self._inner_rect()
        self._inner.setGeometry(ir)
        rad = self.RADIUS
        x0, y0, x1, y1 = ir.x(), ir.y(), ir.x() + ir.width() - rad, ir.y() + ir.height() - rad
        for c, (x, y) in zip(self._corners, ((x0, y0), (x1, y0), (x0, y1), (x1, y1))):
            c.setVisible(rad > 0)
            c.raise_()
        self._mask_cache = None
        self._amb = None
        if self._timer.isActive() or self._cur is not None:
            self._render_amb()
        elif self.isVisible():
            self._timer.start()      # premier rendu (repli neutre), puis arrêt tout seul
        if rad > 0:
            for c, (x, y) in zip(self._corners, ((x0, y0), (x1, y0), (x0, y1), (x1, y1))):
                c.set_rect(QRect(x, y, rad, rad))
        self.update()

    def _refresh_corners(self):
        if self.RADIUS > 0:
            for c in self._corners:
                c.refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def showEvent(self, e):
        super().showEvent(e)
        if self._amb is None and not self._timer.isActive():
            self._timer.start()

    # ── halo ──
    K = 3.0   # facteur de réduction du halo (basse résolution, rendu lissé à l'affichage)

    def _halo_geom(self):
        """Renvoie (p, pad_logique, rect_halo) : pad de réplication de la miniature (en cases) et
        rectangle logique du halo = image + p cases de chaque côté."""
        ir = self._inner_rect()
        tw, th = self._thumb_size()
        cell_x = ir.width() / tw; cell_y = ir.height() / th
        p = 1
        halo = QRectF(ir.x() - p * cell_x, ir.y() - p * cell_y,
                      ir.width() + 2 * p * cell_x, ir.height() + 2 * p * cell_y)
        return p, halo, QRectF(ir)

    def _mask(self, halo: QRectF, ir: QRectF) -> QImage:
        key = (round(halo.width()), round(halo.height()), round(ir.width()), round(ir.height()),
               self.width(), self.height())
        if self._mask_cache is not None and self._mask_key == key:
            return self._mask_cache
        k = self.K
        W, H = max(1, int(halo.width() / k)), max(1, int(halo.height() / k))
        m = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        m.fill(Qt.GlobalColor.transparent)
        q = QPainter(m); q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        r = QRectF((ir.x() - halo.x()) / k, (ir.y() - halo.y()) / k, ir.width() / k, ir.height() / k)
        # le halo s'éteint avant le bord du widget (pas de coupure nette)
        room = min(ir.x(), ir.y(), self.width() - ir.right(), self.height() - ir.bottom()) - 1
        T = max(min(r.x(), r.y(), room / k), 1.0)
        steps = 16
        for i in range(steps, -1, -1):          # de l'extérieur vers l'intérieur
            d = T * i / steps
            a = (1 - i / steps) ** 2
            q.setPen(Qt.PenStyle.NoPen); q.setBrush(QColor(255, 255, 255, int(255 * a)))
            rad = self.RADIUS / k + d
            q.drawRoundedRect(r.adjusted(-d, -d, d, d), rad, rad)
        q.end()
        self._mask_cache, self._mask_key = m, key
        return m

    def _render_amb(self):
        if self._cur is None:
            return
        p, halo, ir = self._halo_geom()
        k = self.K
        W, H = max(1, int(halo.width() / k)), max(1, int(halo.height() / k))
        tw, th = self._cur.width(), self._cur.height()
        # miniature étendue : bords répliqués sur p cases
        pad = QImage(tw + 2 * p, th + 2 * p, QImage.Format.Format_ARGB32_Premultiplied)
        pad.fill(Qt.GlobalColor.transparent)
        q = QPainter(pad)
        q.drawImage(p, p, self._cur)
        for i in range(p):
            q.drawImage(QRect(i, p, 1, th), self._cur, QRect(0, 0, 1, th))
            q.drawImage(QRect(p + tw + i, p, 1, th), self._cur, QRect(tw - 1, 0, 1, th))
        for i in range(p):
            q.drawImage(QRect(0, i, tw + 2 * p, 1), pad, QRect(0, p, tw + 2 * p, 1))
            q.drawImage(QRect(0, p + th + i, tw + 2 * p, 1), pad, QRect(0, p + th - 1, tw + 2 * p, 1))
        q.end()
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img); q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        q.drawImage(QRectF(0, 0, W, H), pad.scaled(pad.width() * 4, pad.height() * 4,
                    Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation))
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        q.drawImage(0, 0, self._mask(halo, ir))
        q.end()
        self._amb, self._amb_rect = img, halo

    # ── peinture ──
    def paint_background(self, p: QPainter, area: QRect | None = None):
        """Vitre + lumière d'ambiance sur `area` (coordonnées du cadre) : on ne dessine que la
        zone demandée, pour que les petits repaints des coins restent quasi gratuits."""
        area = area or self.rect()
        pm = self._glass_pixmap()
        d = pm.devicePixelRatio()
        p.drawPixmap(area.topLeft(), pm, QRect(int(area.x() * d), int(area.y() * d),
                                               int(area.width() * d), int(area.height() * d)))
        if self._amb is None:
            return
        ar = self._amb_rect
        inter = QRectF(area).intersected(ar)
        if inter.isEmpty():
            return
        sx = self._amb.width() / ar.width(); sy = self._amb.height() / ar.height()
        src = QRectF((inter.x() - ar.x()) * sx, (inter.y() - ar.y()) * sy, inter.width() * sx, inter.height() * sy)
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setOpacity(self.STRENGTH)
        p.drawImage(inter, self._amb, src)
        p.restore()

    def _glass_pixmap(self) -> QPixmap:
        gr = self._glass_rect()
        key = (self.width(), self.height(), self.devicePixelRatioF(), gr.x(), gr.y(), gr.width(), gr.height())
        if self._cache is None or self._key != key:
            w, h, dpr = key[:3]
            pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
            pm.setDevicePixelRatio(dpr); pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm); q.setRenderHint(QPainter.RenderHint.Antialiasing)
            # base OPAQUE sous la vitre vidéo : les coins inversés posés sur l'image ont ainsi
            # exactement la couleur du cadre (sinon la vidéo transparaîtrait dans les coins)
            base = QPainterPath(); rad = min(self._radius, gr.height() / 2, gr.width() / 2)
            base.addRoundedRect(gr, rad, rad)
            q.fillPath(base, QColor(20, 23, 30))
            # pas de liseré ici : sur la base opaque il dessinait un 3e contour entre le bord
            # de la vitre et l'image (coin « en plus »). Seuls restent le bord de la vitre et l'image.
            paint_glass(q, gr, self._radius, self._glow, outline=False); q.end()
            self._cache, self._key = pm, key
        return self._cache

    def paintEvent(self, e):
        p = QPainter(self)
        for r in e.region():      # les repaints des coins ne touchent que 4 petits rectangles
            self.paint_background(p, r)
