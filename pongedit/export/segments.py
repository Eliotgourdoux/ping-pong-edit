"""Segments conservés, temps ajustés, chapitres YouTube."""

from dataclasses import replace

from pongedit.utils import _fmt_chapter_time
from pongedit.match.actions import Action, CutAction, PointAction, RotateAction


def _merge_overlapping_spans(actions: list) -> list:
    """Fusionne les intervalles qui se chevauchent ou se touchent, en une passe.

    Les coupes entre elles, les rotations entre elles à angle égal. Une seule
    action par intervalle réellement affecté : la liste dit alors exactement ce
    que fera le montage, et plus aucun calcul en aval n'a à gérer le recouvrement.

    L'action fusionnée garde l'identifiant et la place de la première du groupe,
    pour que la liste ne se réorganise pas sous les yeux de l'utilisateur.
    """
    def _merge(group: list) -> dict:
        kept: list = []
        for a in sorted(group, key=lambda x: x.start):
            if kept and a.start <= kept[-1].end + 1e-6:
                if a.end > kept[-1].end:
                    kept[-1] = replace(kept[-1], end=a.end)
            else:
                kept.append(a)
        return {a.id: a for a in kept}

    merged: dict = {}
    merged.update(_merge([a for a in actions if isinstance(a, CutAction)]))
    angles = {a.angle for a in actions if isinstance(a, RotateAction)}
    for angle in angles:
        merged.update(_merge([a for a in actions
                              if isinstance(a, RotateAction) and a.angle == angle]))

    out = []
    for a in actions:
        if isinstance(a, (CutAction, RotateAction)):
            if a.id in merged:
                out.append(merged[a.id])
        else:
            out.append(a)
    return out


def _adjusted_time(timecode: float, cuts: list[dict], duration: float) -> float:
    """Timecode source → timecode du montage, coupes retirées.

    Les coupes qui se chevauchent sont fusionnées au passage : sans ça, la partie
    commune à deux coupes était retranchée deux fois et tout ce qui suit (score,
    rotations) se retrouvait décalé, de la durée du chevauchement.
    """
    removed = 0.0
    cursor  = 0.0   # fin de la dernière coupe déjà comptée
    for cut in sorted(cuts, key=lambda x: x["start"]):
        s = max(cut["start"], cursor, 0.0)
        e = min(cut["end"], duration)
        if e <= s:
            continue
        if e <= timecode:   removed += e - s
        elif s < timecode:  removed += timecode - s
        cursor = e
    return max(0.0, timecode - removed)


def _build_kept_segments(cuts: list[dict], duration: float) -> list[tuple[float, float]]:
    kept: list[tuple[float, float]] = []
    cursor = 0.0
    for cut in sorted(cuts, key=lambda x: x["start"]):
        start = max(cut["start"], 0.0)
        end = min(cut["end"], duration)
        if start > cursor:
            kept.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        kept.append((cursor, duration))
    return kept or [(0.0, duration)]


def _kept_duration(cuts: list[dict], duration: float) -> float:
    return sum(end - start for start, end in _build_kept_segments(cuts, duration))


def _normalize_actions_for_kept_timeline(actions: list[dict], duration: float) -> tuple[list[dict], float]:
    cuts = [a for a in actions if a["type"] == "cut"]
    if not cuts:
        return list(actions), duration

    adjusted: list[dict] = []
    for action in actions:
        if action["type"] == "cut":
            continue
        item = dict(action)
        item["timecode"] = _adjusted_time(action["timecode"], cuts, duration)
        adjusted.append(item)
    return adjusted, _kept_duration(cuts, duration)


def _youtube_chapters_from_actions(actions: list[Action], duration: float, overlay_dur: float,
                                   offset: float = 0.0) -> str:
    """Chapitres YouTube du montage. `offset` : durée ajoutée au début du
    montage (carte d'intro, image figée) ; « Set 1 » reste à 0:00."""
    points = sorted([a for a in actions if isinstance(a, PointAction)],
                    key=lambda pt: pt.timecode)
    if not points:
        return ""

    cuts = sorted(
        [{"start": a.start, "end": a.end} for a in actions if isinstance(a, CutAction)],
        key=lambda x: x["start"],
    )
    kept_duration = _kept_duration(cuts, duration) if cuts else duration

    chapters: list[tuple[float, str]] = [(0.0, "Set 1")]
    cur_set = 1
    cur_p1 = cur_p2 = 0

    for idx, pt in enumerate(points):
        if pt.player == 1:
            cur_p1 += 1
        else:
            cur_p2 += 1
        if max(cur_p1, cur_p2) >= 11 and abs(cur_p1 - cur_p2) >= 2:
            if idx + 1 < len(points):
                chapters.append((_adjusted_time(pt.timecode, cuts, duration) + offset,
                                 f"Set {cur_set + 1}"))
            cur_set += 1
            cur_p1 = cur_p2 = 0

    last_pt_adj = _adjusted_time(points[-1].timecode, cuts, duration)
    stats_time = min(max(0.0, kept_duration - 0.9), last_pt_adj + overlay_dur)
    chapters.append((stats_time + offset, "Stats du match"))

    lines: list[str] = []
    last_sec = -1
    for idx, (timecode, label) in enumerate(chapters):
        sec = 0 if idx == 0 else max(0, int(timecode + 0.5))
        if sec <= last_sec:
            sec = last_sec + 1
        lines.append(f"{_fmt_chapter_time(sec)} {label}")
        last_sec = sec
    return "\n".join(lines)
