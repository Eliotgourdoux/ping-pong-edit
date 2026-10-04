"""Widget tableau de score."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QLineEdit, QFrame
from PySide6.QtCore import Qt, Signal

from pongedit.ui.style import UI, themed


# ── Scoreboard widget (right panel) ───────────────────────────────────────────

class ScoreboardWidget(QWidget):
    """Tableau de score : bande couleur | nom + classement | sets passés | score courant."""
    names_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("scoreboard")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(2, 1)   # la colonne nom s'étire

        self._bars: dict[int, QFrame] = {}
        self._srv_lbls: dict[int, QLabel] = {}
        self.p1_input = QLineEdit("Player 1")
        self.p2_input = QLineEdit("Player 2")
        self.p1_rank_input = QLineEdit()
        self.p2_rank_input = QLineEdit()

        for row_i, (p, name_inp, rank_inp) in enumerate(
            ((1, self.p1_input, self.p1_rank_input), (2, self.p2_input, self.p2_rank_input))
        ):
            pk = "p1" if p == 1 else "p2"
            bar = QFrame()
            bar.setFixedWidth(4)
            themed(bar, lambda pk=pk: f"background:{UI[pk]}; border-radius:2px;")
            self._bars[p] = bar
            self._grid.addWidget(bar, row_i, 0)

            srv = QLabel("●")
            srv.setFixedWidth(12)
            srv.setAlignment(Qt.AlignCenter)
            srv.setToolTip("Au service")
            self._srv_lbls[p] = srv
            self._grid.addWidget(srv, row_i, 1)

            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            name_inp.setPlaceholderText(f"Joueur {p}")
            name_inp.setToolTip("Nom du joueur (repris dans l'incrustation et l'export)")
            themed(name_inp, lambda pk=pk: (
                "QLineEdit{"
                f"color:{UI[pk]}; font-weight:700; font-size:15px;"
                "border:none; border-radius:4px; background:transparent; padding:0 4px;"
                "}"
                f"QLineEdit:hover{{ background:{UI['raised']}; }}"
                f"QLineEdit:focus{{ background:{UI['raised']}; }}"
            ))
            rank_inp.setPlaceholderText("Classement (ex : 1245 pts, N°350)")
            rank_inp.setToolTip("Classement affiché sous le nom dans la vidéo (texte libre)")
            themed(rank_inp, lambda: (
                "QLineEdit{"
                f"color:{UI['muted']}; font-size:11px;"
                "border:none; border-radius:4px; background:transparent; padding:0 4px;"
                "}"
                f"QLineEdit:hover{{ background:{UI['raised']}; }}"
                f"QLineEdit:focus{{ background:{UI['raised']}; color:{UI['text']}; }}"
            ))
            name_inp.textChanged.connect(self.names_changed)
            rank_inp.textChanged.connect(self.names_changed)
            v.addWidget(name_inp)
            v.addWidget(rank_inp)
            self._grid.addWidget(box, row_i, 2)

        self._set_labels: list[tuple[QLabel, QLabel]] = []
        self.set_serving(None)
        self._refresh_grid([], 0, 0)

    def update_scores(self, completed: list[tuple[int,int]], cur_p1: int, cur_p2: int):
        self._refresh_grid(completed, cur_p1, cur_p2)

    def set_serving(self, player: int | None):
        for p, lbl in self._srv_lbls.items():
            themed(lbl, lambda on=(player == p):
                   f"color:{UI['gold'] if on else 'transparent'}; font-size:9px;")

    def _refresh_grid(self, completed, cur_p1, cur_p2):
        for a, b in self._set_labels:
            a.deleteLater(); b.deleteLater()
        self._set_labels.clear()

        all_sets = list(completed) + [(cur_p1, cur_p2)]
        n = len(all_sets)
        for col_i, (s1, s2) in enumerate(all_sets):
            is_cur = col_i == n - 1
            l1 = QLabel(str(s1)); l2 = QLabel(str(s2))
            for lbl, p, mine, other in ((l1, 1, s1, s2), (l2, 2, s2, s1)):
                lbl.setAlignment(Qt.AlignCenter)
                if is_cur:
                    pk = "p1" if p == 1 else "p2"
                    themed(lbl, lambda pk=pk: (
                        f"background:{UI[pk]}; color:{UI['on_player']}; font-size:20px; font-weight:800;"
                        "min-width:40px; min-height:28px; border-radius:6px;"
                    ))
                else:
                    won = mine > other
                    themed(lbl, lambda won=won: (
                        f"background:{UI['raised']}; color:{UI['text'] if won else UI['faint']};"
                        f"font-size:13px; font-weight:{'700' if won else '500'};"
                        "min-width:26px; min-height:22px; border-radius:5px;"
                    ))
            self._grid.addWidget(l1, 0, col_i + 3)
            self._grid.addWidget(l2, 1, col_i + 3)
            self._set_labels.append((l1, l2))

    def p1_name(self) -> str: return self.p1_input.text()
    def p2_name(self) -> str: return self.p2_input.text()
    def p1_rank(self) -> str: return self.p1_rank_input.text().strip()
    def p2_rank(self) -> str: return self.p2_rank_input.text().strip()
