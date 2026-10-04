"""Dialogues : montage multi-vidéos, carte d'intro."""

import shutil, re
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
    QListWidget, QListWidgetItem, QDoubleSpinBox, QLineEdit, QProgressBar,
    QComboBox, QSizePolicy, QFrame, QDialog, QScrollArea, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPixmap, QImage, QPainterPath

from pongedit.utils import fmt_time
from pongedit.export.merge import (
    CANVAS_CHOICES, FIT_CHOICES, MergeWorker, _merge_canvas, _probe_clip_info,
    _segment_display_size,
)
from pongedit.ui.style import UI, UI_DARK, _field_label, _section, _sep, is_dark, themed


class MergeDialog(QDialog):
    """Recolle une liste de segments (fichier + intervalle) en une seule vidéo."""

    VIDEO_FILTER = "Vidéos (*.mp4 *.mov *.avi *.mkv *.m4v *.webm);;Tous (*)"

    def __init__(self, parent=None, initial_path: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Fusionner des vidéos")
        # Fenêtre à part entière plutôt que boîte de dialogue : on récupère les
        # boutons réduire et agrandir du système, utiles pendant une longue fusion.
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(660, 620)
        self.setMinimumSize(430, 320)
        # Styles via themed() : ré-appliqués à chaud au changement de thème
        # (MainWindow.apply_theme → restyle_registered).
        themed(self, lambda: f"QDialog{{background:{UI['bg']}; color:{UI['text']};}}")

        self.segments: list[dict] = []
        self.result_path: str | None = None
        self._worker: MergeWorker | None = None
        self._loading = False

        self._build_ui()
        self._wrap_in_scroll()
        if initial_path and Path(initial_path).exists():
            self._add_paths([initial_path])

    # ── UI ────────────────────────────────────────────────────────────────────

    def _wrap_in_scroll(self):
        """Met tout le contenu dans une zone défilante.

        Sans ça, la largeur minimale des contrôles empêche de rétrécir la fenêtre
        en dessous de sa taille d'ouverture.
        """
        inner = QWidget()
        inner.setLayout(self.layout())      # la mise en page passe dans le contenu
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        themed(scroll, lambda: f"QScrollArea{{background:{UI['bg']};border:none;}}")
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        intro = QLabel(
            "Colle plusieurs vidéos bout à bout, dans l'ordre de ton choix, en "
            "coupant au besoin le début ou la fin de chacune. Le résultat s'ouvre "
            "dans l'éditeur, où les rotations se posent comme les coupes."
        )
        intro.setWordWrap(True)
        themed(intro, lambda: f"color:{UI['muted']};font-size:11px;")
        root.addWidget(intro)

        root.addWidget(_section("Segments"))
        self.seg_list = QListWidget()
        self.seg_list.setToolTip("Les segments sont collés dans cet ordre")
        self.seg_list.setStyleSheet(
            'QListWidget{font-family:"Menlo","SF Mono",monospace; font-size:11px;}'
        )
        self.seg_list.currentRowChanged.connect(lambda _: self._sync_editor())
        root.addWidget(self.seg_list, 1)

        tools = QHBoxLayout(); tools.setSpacing(6)
        add_btn = QPushButton("Ajouter des vidéos")
        add_btn.setProperty("variant", "primary")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setToolTip("Ajouter un ou plusieurs fichiers à la suite")
        add_btn.clicked.connect(self._pick_files)
        tools.addWidget(add_btn)
        for label, tip, slot in (
            ("⧉", "Dupliquer le segment sélectionné", self._duplicate),
            ("↑", "Monter dans l'ordre", lambda: self._move(-1)),
            ("↓", "Descendre dans l'ordre", lambda: self._move(1)),
            ("✕", "Retirer le segment", self._remove),
        ):
            b = QPushButton(label)
            b.setProperty("variant", "ghost")
            b.setFixedWidth(42)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            tools.addWidget(b)
        root.addLayout(tools)

        root.addWidget(_sep())
        root.addWidget(_section("Segment sélectionné"))

        self.seg_title = QLabel("—")
        # Sombre : gris clair d'origine (hors palette), conservé à l'identique.
        themed(self.seg_title, lambda: (
            f"color:{'#C3C9D6' if is_dark() else UI['text']};font-size:12px;"))
        root.addWidget(self.seg_title)

        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(self._mini_lbl("Début"))
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setDecimals(2); self.start_spin.setSingleStep(0.5)
        self.start_spin.setSuffix(" s"); self.start_spin.setMaximum(999999.0)
        self.start_spin.setToolTip("Début du segment dans le fichier source")
        self.start_spin.valueChanged.connect(lambda v: self._edit("start", v))
        row.addWidget(self.start_spin)
        row.addWidget(self._mini_lbl("Fin"))
        self.end_spin = QDoubleSpinBox()
        self.end_spin.setDecimals(2); self.end_spin.setSingleStep(0.5)
        self.end_spin.setSuffix(" s"); self.end_spin.setMaximum(999999.0)
        self.end_spin.setToolTip("Fin du segment dans le fichier source")
        self.end_spin.valueChanged.connect(lambda v: self._edit("end", v))
        row.addWidget(self.end_spin)
        root.addLayout(row)

        row2 = QHBoxLayout(); row2.setSpacing(8)
        row2.addWidget(self._mini_lbl("Scinder"))
        self.split_spin = QDoubleSpinBox()
        self.split_spin.setDecimals(2); self.split_spin.setSingleStep(1.0)
        self.split_spin.setSuffix(" s"); self.split_spin.setMaximum(999999.0)
        self.split_spin.setToolTip("Instant où couper le segment en deux")
        row2.addWidget(self.split_spin)
        split_btn = QPushButton("✂  Scinder ici")
        split_btn.setProperty("variant", "ghost")
        split_btn.setCursor(Qt.PointingHandCursor)
        split_btn.setToolTip("Couper le segment en deux à cet instant, pour réordonner ou retirer un morceau")
        split_btn.clicked.connect(self._split)
        row2.addWidget(split_btn)
        row2.addStretch()
        root.addLayout(row2)

        root.addWidget(_sep())
        root.addWidget(_section("Sortie"))

        row3 = QHBoxLayout(); row3.setSpacing(8)
        row3.addWidget(self._mini_lbl("Format"))
        self.canvas_combo = QComboBox()
        for label, mode in CANVAS_CHOICES:
            self.canvas_combo.addItem(label, mode)
        self.canvas_combo.setToolTip("Orientation et proportions de la vidéo finale")
        self.canvas_combo.setCursor(Qt.PointingHandCursor)
        self.canvas_combo.currentIndexChanged.connect(self._refresh_summary)
        row3.addWidget(self.canvas_combo, 1)
        row3.addWidget(self._mini_lbl("Cadrage"))
        self.fit_combo = QComboBox()
        for label, mode in FIT_CHOICES:
            self.fit_combo.addItem(label, mode)
        self.fit_combo.setToolTip("Que faire d'un segment qui ne remplit pas le cadre")
        self.fit_combo.setCursor(Qt.PointingHandCursor)
        row3.addWidget(self.fit_combo, 1)
        root.addLayout(row3)

        row4 = QHBoxLayout(); row4.setSpacing(8)
        row4.addWidget(self._mini_lbl("Fichier"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("fusion")
        self.name_input.setToolTip("Nom du fichier produit dans Desktop/pong_exports")
        row4.addWidget(self.name_input, 1)
        root.addLayout(row4)

        self.open_chk = QCheckBox("Ouvrir le résultat dans l'éditeur")
        self.open_chk.setChecked(True)
        self.open_chk.setCursor(Qt.PointingHandCursor)
        themed(self.open_chk, lambda: f"color:{UI['muted']};font-size:11px;")
        root.addWidget(self.open_chk)

        self.summary = QLabel("Aucun segment.")
        self.summary.setWordWrap(True)
        themed(self.summary, lambda: f"color:{UI['dim']};font-size:11px;")
        root.addWidget(self.summary)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Fusion…  0.0%")
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

        self.status = QLabel(); self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._status_error: bool | None = None     # None : pas encore de message
        themed(self.status, self._status_css); self.status.hide()
        root.addWidget(self.status)

        actions = QHBoxLayout(); actions.setSpacing(8)
        actions.addStretch()
        self.close_btn = QPushButton("Fermer")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)
        actions.addWidget(self.close_btn)
        self.cancel_btn = QPushButton("✖  Annuler la fusion")
        themed(self.cancel_btn, lambda: (
            f"QPushButton{{background:{UI['danger_bg']};color:{UI['danger_text']};}}"
            f"QPushButton:hover{{background:{UI['danger_edge']};color:{UI['danger_soft']};}}"))
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.hide()
        actions.addWidget(self.cancel_btn)
        self.run_btn = QPushButton("🔗  Fusionner")
        self.run_btn.setProperty("variant", "primary")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.clicked.connect(self._start)
        actions.addWidget(self.run_btn)
        root.addLayout(actions)

        self._sync_editor()

    def _mini_lbl(self, text: str) -> QLabel:
        return _field_label(text, 52)

    # ── Segments ──────────────────────────────────────────────────────────────

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Ajouter des vidéos", str(Path.home()), self.VIDEO_FILTER)
        if paths:
            self._add_paths(paths)

    def _add_paths(self, paths: list[str]):
        added = 0
        for path in paths:
            info = _probe_clip_info(path)
            if not info["ok"]:
                self._say(f"Illisible, ignoré : {Path(path).name}", error=True)
                continue
            self.segments.append({
                "path": path, "start": 0.0,
                "end": round(info["duration"], 2) or 1.0, "rotation": 0,
            })
            added += 1
        if added:
            if not self.name_input.text().strip():
                self.name_input.setPlaceholderText(
                    f"{re.sub(r'[^A-Za-z0-9_-]+', '_', Path(self.segments[0]['path']).stem)}_fusion")
            self._refresh_list(select=len(self.segments) - 1)

    def _current(self) -> int:
        return self.seg_list.currentRow()

    def _duplicate(self):
        i = self._current()
        if i < 0:
            return
        self.segments.insert(i + 1, dict(self.segments[i]))
        self._refresh_list(select=i + 1)

    def _remove(self):
        i = self._current()
        if i < 0:
            return
        self.segments.pop(i)
        self._refresh_list(select=min(i, len(self.segments) - 1))

    def _move(self, delta: int):
        i = self._current()
        j = i + delta
        if i < 0 or not (0 <= j < len(self.segments)):
            return
        self.segments[i], self.segments[j] = self.segments[j], self.segments[i]
        self._refresh_list(select=j)

    def _split(self):
        i = self._current()
        if i < 0:
            return
        seg = self.segments[i]
        t = self.split_spin.value()
        if not (seg["start"] + 0.1 < t < seg["end"] - 0.1):
            self._say("Le point de coupe doit tomber à l'intérieur du segment.", error=True)
            return
        tail = dict(seg)
        tail["start"] = round(t, 2)
        seg["end"] = round(t, 2)
        self.segments.insert(i + 1, tail)
        self._refresh_list(select=i + 1)

    def _edit(self, key: str, value):
        i = self._current()
        if self._loading or i < 0:
            return
        seg = self.segments[i]
        seg[key] = value
        if key == "start" and seg["start"] > seg["end"] - 0.1:
            seg["end"] = round(seg["start"] + 0.1, 2)
        if key == "end" and seg["end"] < seg["start"] + 0.1:
            seg["start"] = round(max(0.0, seg["end"] - 0.1), 2)
        self._refresh_list(select=i, keep_editor=True)

    # ── Affichage ─────────────────────────────────────────────────────────────

    def _label(self, n: int, seg: dict) -> str:
        w, h = _segment_display_size(seg)
        return (f"{n + 1}.  {Path(seg['path']).name}\n"
                f"      {fmt_time(seg['start'])} → {fmt_time(seg['end'])}   ·   {w}×{h}")

    def _refresh_list(self, select: int = -1, keep_editor: bool = False):
        self._loading = True
        self.seg_list.clear()
        for n, seg in enumerate(self.segments):
            self.seg_list.addItem(QListWidgetItem(self._label(n, seg)))
        if 0 <= select < len(self.segments):
            self.seg_list.setCurrentRow(select)
        self._loading = False
        if not keep_editor:
            self._sync_editor()
        self._refresh_summary()

    def _sync_editor(self):
        i = self._current()
        has = i >= 0
        for w in (self.start_spin, self.end_spin, self.split_spin):
            w.setEnabled(has)
        if not has:
            self.seg_title.setText("—")
            return
        seg = self.segments[i]
        info = _probe_clip_info(seg["path"])
        self._loading = True
        self.seg_title.setText(
            f"{Path(seg['path']).name}  ·  source {info['width']}×{info['height']}"
            f"  ·  {fmt_time(info['duration'])}")
        for spin in (self.start_spin, self.end_spin, self.split_spin):
            spin.setMaximum(max(1.0, info["duration"]))
        self.start_spin.setValue(seg["start"])
        self.end_spin.setValue(seg["end"])
        self.split_spin.setValue(round((seg["start"] + seg["end"]) / 2, 2))
        self._loading = False

    def _refresh_summary(self):
        if not self.segments:
            self.summary.setText("Aucun segment.")
            return
        total = sum(max(0.0, s["end"] - s["start"]) for s in self.segments)
        w, h = _merge_canvas(self.segments, self.canvas_combo.currentData())
        self.summary.setText(
            f"{len(self.segments)} segment(s)  ·  durée finale {fmt_time(total)}"
            f"  ·  sortie {w}×{h}")

    def _status_css(self) -> str:
        if self._status_error is None:
            return "font-size:11px;"
        if self._status_error:
            return f"font-size:11px;color:{UI['danger_text']};"
        # Sombre : vert clair d'origine (hors palette), conservé à l'identique.
        return f"font-size:11px;color:{'#7FE9BE' if is_dark() else UI['success']};"

    def _say(self, text: str, error: bool = False):
        self.status.setText(text)
        self._status_error = error
        self.status.setStyleSheet(self._status_css())
        self.status.show()

    # ── Fusion ────────────────────────────────────────────────────────────────

    def _start(self):
        if self._worker is not None:
            return
        if not self.segments:
            self._say("Ajoute au moins une vidéo.", error=True)
            return
        if shutil.which("ffmpeg") is None:
            self._say("ffmpeg introuvable dans le PATH.", error=True)
            return
        name = self.name_input.text().strip() or self.name_input.placeholderText() or "fusion"

        self.run_btn.hide(); self.close_btn.hide(); self.cancel_btn.show()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Fusion…  0.0%")
        self.progress_bar.show()
        self._say("🔗  Fusion et encodage…")

        self._worker = MergeWorker(self.segments, self.canvas_combo.currentData(),
                                   self.fit_combo.currentData(), name)
        self._worker.progress.connect(self._on_progress)
        self._worker.stage.connect(lambda t: self._say(t))
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.canceled.connect(self._on_canceled)
        self._worker.start()

    def _on_progress(self, pct: float):
        self.progress_bar.setValue(int(pct * 10))
        self.progress_bar.setFormat(f"Fusion…  {pct:.1f}%")

    def _reset_ui(self):
        self._worker = None
        self.cancel_btn.hide()
        self.cancel_btn.setEnabled(True)
        self.run_btn.show(); self.close_btn.show()
        self.progress_bar.hide()

    def _on_done(self, path: str):
        self._reset_ui()
        self.result_path = path
        self._say(f"✅  {Path(path).name} — dans Desktop/pong_exports")
        if self.open_chk.isChecked():
            self.accept()

    def _on_error(self, msg: str):
        self._reset_ui()
        self._say(msg, error=True)

    def _on_canceled(self):
        self._reset_ui()
        self._say("Fusion annulée.", error=True)

    def _cancel(self):
        if self._worker is not None:
            self.cancel_btn.setEnabled(False)
            self._worker.cancel()

    def closeEvent(self, event):
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(4000)
        super().closeEvent(event)


class IntroCardDialog(QDialog):
    """Saisie de la carte d'intro « duel » : joueurs, événement, aperçu.

    Préremplie par la fenêtre (tableau de score découpé en prénom / nom,
    classement, club et occasion mémorisés, date de la vidéo). L'aperçu est
    la carte telle qu'elle sera incrustée (image fixe de la tenue), sur un
    fond neutre qui figure la vidéo dézoomée : il ne suit pas le thème.
    """

    PREVIEW = (560, 315)

    def __init__(self, parent, values: dict, auto: dict, memory: dict | None = None):
        super().__init__(parent)
        from pongedit.export import intro as _intro
        self._intro = _intro
        self._auto = dict(auto)
        memory = memory or {}
        self.setWindowTitle("Carte d'intro")
        self.setMinimumWidth(660)
        themed(self, lambda: f"QDialog{{background:{UI['bg']}; color:{UI['text']};}}")

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)

        hint = QLabel("Présentation des deux joueurs au début du montage et des highlights, "
                      "sur la 1re image figée. Les champs vides n'apparaissent pas sur la carte.")
        hint.setWordWrap(True)
        themed(hint, lambda: f"color:{UI['muted']};font-size:11px;")
        v.addWidget(hint)

        self.preview = QLabel()
        self.preview.setFixedSize(*self.PREVIEW)
        self.preview.setAlignment(Qt.AlignCenter)
        themed(self.preview, lambda: f"border:1px solid {UI['border']};border-radius:8px;"
                                     f"background:{UI['surface']};")
        v.addWidget(self.preview, 0, Qt.AlignHCenter)

        from PySide6.QtWidgets import QGridLayout, QCompleter
        self._edits: dict[str, QLineEdit] = {}
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        clubs = [c for c in memory.get("recent_clubs", []) if isinstance(c, str)]
        for col, (k, title, tok) in enumerate((("p1", "Joueur 1", "p1"), ("p2", "Joueur 2", "p2"))):
            head = QLabel(title.upper())
            themed(head, lambda tok=tok: f"color:{UI[tok]};font-size:10px;font-weight:700;"
                                         f"letter-spacing:2px;")
            grid.addWidget(head, 0, col * 2, 1, 2)
            for row, (f, lbl, ph) in enumerate((("first", "Prénom", "Prénom"),
                                                 ("last", "Nom", "Nom de famille"),
                                                 ("rank", "Classement", "ex. 721 pts"),
                                                 ("club", "Club", "optionnel")), start=1):
                grid.addWidget(_field_label(lbl, 70), row, col * 2)
                e = QLineEdit()
                e.setPlaceholderText(ph)
                if f == "club" and clubs:
                    cpl = QCompleter(clubs, e)
                    cpl.setCaseSensitivity(Qt.CaseInsensitive)
                    e.setCompleter(cpl)
                grid.addWidget(e, row, col * 2 + 1)
                self._edits[f"{k}_{f}"] = e
        v.addLayout(grid)

        v.addWidget(_sep())
        ev = QLabel("ÉVÉNEMENT")
        themed(ev, lambda: f"color:{UI['muted']};font-size:10px;font-weight:700;letter-spacing:2px;")
        v.addWidget(ev)
        erow = QGridLayout()
        erow.setHorizontalSpacing(10)
        erow.setVerticalSpacing(6)
        erow.addWidget(_field_label("Occasion", 70), 0, 0)
        self.occ = QComboBox()
        self.occ.setEditable(True)
        self.occ.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        seen, items = set(), []
        for o in [*memory.get("recent_occasions", []), *_intro.OCCASION_SUGGESTIONS]:
            if isinstance(o, str) and o.strip() and o.lower() not in seen:
                seen.add(o.lower()); items.append(o)
        self.occ.addItems(items)
        self.occ.lineEdit().setPlaceholderText("optionnel : tournoi, entraînement…")
        erow.addWidget(self.occ, 0, 1)
        erow.addWidget(_field_label("Date", 70), 0, 2)
        self._edits["lieu"] = QLineEdit()
        self._edits["lieu"].setPlaceholderText("optionnel")
        lieux = [x for x in memory.get("recent_lieux", []) if isinstance(x, str)]
        if lieux:
            cpl = QCompleter(lieux, self._edits["lieu"])
            cpl.setCaseSensitivity(Qt.CaseInsensitive)
            self._edits["lieu"].setCompleter(cpl)
        self._edits["date"] = QLineEdit()
        self._edits["date"].setPlaceholderText("date de la vidéo")
        erow.addWidget(self._edits["date"], 0, 3)
        erow.addWidget(_field_label("Lieu", 70), 1, 0)
        erow.addWidget(self._edits["lieu"], 1, 1, 1, 3)
        erow.setColumnStretch(1, 1)
        erow.setColumnStretch(3, 1)
        v.addLayout(erow)

        brow = QHBoxLayout()
        reset = QPushButton("Reprendre le tableau de score")
        reset.setProperty("variant", "ghost")
        reset.setToolTip("Remet les champs préremplis : noms et classements du tableau de "
                         "score, club et occasion mémorisés, date de la vidéo.")
        reset.clicked.connect(lambda: self._fill(self._auto))
        brow.addWidget(reset)
        brow.addStretch()
        cancel = QPushButton("Annuler")
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.reject)
        brow.addWidget(cancel)
        ok = QPushButton("Valider")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        brow.addWidget(ok)
        v.addLayout(brow)

        # Aperçu recalculé peu après la frappe (≈ 50 ms de rendu).
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render)
        self._fill(values)
        for e in self._edits.values():
            e.textChanged.connect(lambda _t: self._timer.start())
        self.occ.editTextChanged.connect(lambda _t: self._timer.start())

    def _fill(self, values: dict):
        for k, e in self._edits.items():
            e.setText(str(values.get(k, "") or ""))
        self.occ.setEditText(str(values.get("occasion", "") or ""))
        self._render()

    def values(self) -> dict:
        out = {k: " ".join(e.text().split()) for k, e in self._edits.items()}
        out["occasion"] = " ".join(self.occ.currentText().split())
        return out

    def _backdrop(self, w: int, h: int):
        """Fond neutre figurant la vidéo : fond sombre flou + image dézoomée."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (w, h))
        top, bot = (46, 52, 64), (20, 23, 30)
        d = ImageDraw.Draw(img)
        for y in range(h):
            k = y / max(1, h - 1)
            d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bot)) + (255,))
        z = self._intro.ZOOM_MIN
        fw, fh = int(w * z), int(h * z)
        x0, y0 = (w - fw) // 2, (h - fh) // 2
        for y in range(fh):
            k = y / max(1, fh - 1)
            c = tuple(int(a + (b - a) * k) for a, b in zip((104, 112, 124), (70, 76, 86)))
            d.line([(x0, y0 + y), (x0 + fw - 1, y0 + y)], fill=c + (255,))
        return img

    def _render(self):
        dpr = self.devicePixelRatio() or 1.0
        pw, ph = self.PREVIEW
        w, h = int((pw - 2) * dpr) // 2 * 2, int((ph - 2) * dpr) // 2 * 2
        try:
            img = self._backdrop(w, h)
            img.alpha_composite(self._intro.render_intro_still(self.values(), w, h, t=2.0))
        except Exception as e:           # un aperçu raté ne doit rien bloquer
            self.preview.setText(f"Aperçu indisponible : {e}")
            return
        qimg = QImage(img.tobytes("raw", "RGBA"), w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
        pm = QPixmap.fromImage(qimg)
        pm.setDevicePixelRatio(dpr)
        self.preview.setPixmap(pm)
