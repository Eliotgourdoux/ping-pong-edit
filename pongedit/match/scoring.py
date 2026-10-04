"""Score : statistiques, sets, service."""

from dataclasses import replace

from pongedit.match.actions import PointAction, sort_actions


def server_at(first_server: int, set_index: int, cp1: int, cp2: int, flips: int = 0) -> int:
    """Serveur du prochain échange : alternance tous les 2 points (1 à 10-10), le
    premier serveur change à chaque set, et chaque inversion forcée (touche F)
    échange le serveur calculé."""
    fsrv = first_server if set_index % 2 == 0 else (3 - first_server)
    total = cp1 + cp2
    sidx = ((total - 20) % 2 if cp1 >= 10 and cp2 >= 10 else (total // 2) % 2)
    srv = fsrv if sidx == 0 else (3 - fsrv)
    return srv if flips % 2 == 0 else (3 - srv)


def flips_before(swap_times, t: float | None = None) -> int:
    """Nombre d'inversions forcées posées avant `t` (toutes si `t` vaut None)."""
    return sum(1 for s in swap_times if t is None or s < t)


def _compute_stats_from_dicts(actions: list[dict], first_server: int) -> dict:
    # Ordre de la video : le calcul des sets depend de l'enchainement reel des
    # points, pas de l'ordre dans lequel ils ont ete poses.
    points = sorted([a for a in actions if a["type"] == "point"],
                    key=lambda pt: pt["timecode"])
    if not points:
        return {}
    swaps = [a["timecode"] for a in actions if a["type"] == "swap"]

    sets: list[list[dict]] = []
    cur: list[dict] = []
    p1 = p2 = 0
    for pt in points:
        cur.append(pt)
        if pt["player"] == 1:
            p1 += 1
        else:
            p2 += 1
        if max(p1, p2) >= 11 and abs(p1 - p2) >= 2:
            sets.append(cur)
            cur = []
            p1 = p2 = 0
    if cur:
        sets.append(cur)

    result = {
        "p1_sets": 0, "p2_sets": 0, "p1_pts": 0, "p2_pts": 0,
        "p1_won_srv": 0, "p1_tot_srv": 0, "p2_won_srv": 0, "p2_tot_srv": 0,
        "sets": [],
    }
    for si, set_pts in enumerate(sets):
        fsrv = first_server if si % 2 == 0 else (3 - first_server)
        sp1 = sp2 = 0
        sd = {"fsrv": fsrv, "p1_won_srv": 0, "p1_tot_srv": 0, "p2_won_srv": 0, "p2_tot_srv": 0}
        if set_pts:
            # 1er serveur RÉEL du set (inversions forcées comprises), pour le tableau.
            sd["fsrv"] = server_at(first_server, si, 0, 0,
                                   flips_before(swaps, set_pts[0]["timecode"]))
        for pt in set_pts:
            srv = server_at(first_server, si, sp1, sp2,
                            flips_before(swaps, pt["timecode"]))
            if srv == 1:
                sd["p1_tot_srv"] += 1
                result["p1_tot_srv"] += 1
                if pt["player"] == 1:
                    sd["p1_won_srv"] += 1
                    result["p1_won_srv"] += 1
            else:
                sd["p2_tot_srv"] += 1
                result["p2_tot_srv"] += 1
                if pt["player"] == 2:
                    sd["p2_won_srv"] += 1
                    result["p2_won_srv"] += 1
            if pt["player"] == 1:
                sp1 += 1
            else:
                sp2 += 1
        sd["score"] = (sp1, sp2)
        if sp1 > sp2:
            result["p1_sets"] += 1
        elif sp2 > sp1:
            result["p2_sets"] += 1
        result["p1_pts"] += sp1
        result["p2_pts"] += sp2
        result["sets"].append(sd)
    return result


def compute_sets(actions: list) -> tuple[list[tuple[int, int]], tuple[int, int]]:
    """Returns (completed_sets, (cur_p1, cur_p2))."""
    points = sorted((a for a in actions if isinstance(a, PointAction)),
                    key=lambda pt: pt.timecode)
    completed: list[tuple[int, int]] = []
    p1 = p2 = 0
    for pt in points:
        if pt.player == 1: p1 += 1
        else:              p2 += 1
        if max(p1, p2) >= 11 and abs(p1 - p2) >= 2:
            completed.append((p1, p2))
            p1 = p2 = 0
    return completed, (p1, p2)


def recompute_point_fields(actions: list) -> list:
    """Redonne a chaque point son score, son numero de set et les sets deja joues.

    Ces trois champs ne sont qu'un cache : le seul fait qui compte est le couple
    (timecode, joueur). Les recalculer en ordre chronologique apres chaque
    modification permet d'inserer ou de retirer un point au milieu du match sans
    fausser tout ce qui suit -- avant, un point re-marque apres coup partait en fin
    de liste et comptait comme le dernier du match.

    Les points sont remplaces, jamais mutes en place : la pile d'annulation ne garde
    que des copies superficielles de la liste, une mutation la corromprait.

    Rend la liste triee, actions hors points comprises.
    """
    out: list = []
    completed: list[tuple[int, int]] = []
    p1 = p2 = 0
    for a in sort_actions(actions):
        if not isinstance(a, PointAction):
            out.append(a)
            continue
        set_num = len(completed)
        sets_before = list(completed)
        if a.player == 1: p1 += 1
        else:             p2 += 1
        score = f"{p1}-{p2}"          # score apres le point, meme s'il clot le set
        if max(p1, p2) >= 11 and abs(p1 - p2) >= 2:
            completed.append((p1, p2))
            p1 = p2 = 0
        out.append(replace(a, score=score, set_num=set_num,
                           completed_sets=sets_before))
    return out
