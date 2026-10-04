"""Actions de la timeline (points, coupes, rotations, inversions de service) et tri."""

from dataclasses import dataclass, field


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class PointAction:
    id: str
    player: int
    timecode: float
    score: str                              # current-set score  "4-5"
    set_num: int = 0                        # 0-indexed set number
    completed_sets: list = field(default_factory=list)  # [(p1,p2), ...]

@dataclass
class CutAction:
    id: str
    start: float
    end: float

@dataclass
class RotateAction:
    """Rotation appliquée à un intervalle de la vidéo, comme une coupe.

    Sert au cas « j'ai tourné le téléphone en cours de match » : le morceau filmé
    dans l'autre sens est redressé, à l'export, sur le même cadre que le reste.
    """
    id: str
    start: float
    end: float
    angle: int          # 90 (horaire), 270 (anti-horaire), 180

@dataclass
class ServeSwapAction:
    """Inversion forcée du service à partir de cet instant (touche F).

    Pour les matchs amicaux où l'on se trompe de serveur : à partir du marqueur,
    le serveur calculé est inversé (jusqu'au marqueur suivant, qui ré-inverse).
    """
    id: str
    timecode: float

Action = PointAction | CutAction | RotateAction | ServeSwapAction

ROT_LABELS = {90: "90° ↻", 270: "90° ↺", 180: "180°"}
ROT_COLOR  = "#B08CFF"

def action_time(a) -> float:
    """Instant de l'action sur la timeline : un point a un timecode, une coupe un debut."""
    return a.timecode if isinstance(a, (PointAction, ServeSwapAction)) else a.start


def sort_actions(actions: list) -> list:
    """Remet les actions dans l'ordre de la video.

    Tri stable : deux actions au meme instant gardent leur ordre de pose.
    """
    return sorted(actions, key=action_time)
