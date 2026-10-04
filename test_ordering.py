#!/usr/bin/env python3
"""Ordre des points : c'est la timeline qui fait foi, pas l'ordre de pose.

Lancer :  ~/.pyenv/versions/3.14.2/bin/python3.14 -B -m unittest test_ordering -v
"""
import random
import unittest

from pong_edit import (
    CutAction, PointAction, RotateAction,
    _compute_stats_from_dicts, _youtube_chapters_from_actions,
    action_time, compute_sets, gen_id, recompute_point_fields, sort_actions,
)


def pt(t: float, player: int) -> PointAction:
    return PointAction(id=gen_id(), player=player, timecode=t, score="0-0")


def rally(players, start=1.0, step=1.0):
    """Une suite de points, un par seconde, dans l'ordre de la video."""
    return [pt(start + i * step, p) for i, p in enumerate(players)]


def shuffled(actions, seed=0):
    out = list(actions)
    random.Random(seed).shuffle(out)
    return out


def as_dicts(points):
    return [{"type": "point", "player": p.player, "timecode": p.timecode,
             "score": p.score} for p in points]


class TestSortActions(unittest.TestCase):
    def test_melange_de_types(self):
        p1, p2 = pt(5.0, 1), pt(1.0, 2)
        cut = CutAction(id=gen_id(), start=3.0, end=4.0)
        rot = RotateAction(id=gen_id(), start=0.5, end=2.0, angle=90)
        order = sort_actions([p1, cut, p2, rot])
        self.assertEqual([action_time(a) for a in order], [0.5, 1.0, 3.0, 5.0])

    def test_stabilite_sur_timecodes_egaux(self):
        a, b, c = pt(2.0, 1), pt(2.0, 2), pt(2.0, 1)
        self.assertEqual(sort_actions([a, b, c]), [a, b, c])

    def test_ne_mute_pas_la_liste_source(self):
        src = [pt(5.0, 1), pt(1.0, 2)]
        copie = list(src)
        sort_actions(src)
        self.assertEqual(src, copie)


class TestComputeSets(unittest.TestCase):
    def test_desordre_donne_le_meme_resultat_que_l_ordre(self):
        points = rally([1, 2] * 5 + [1])          # 6-5
        self.assertEqual(compute_sets(shuffled(points, seed=1)),
                         compute_sets(points))

    def test_set_clos(self):
        points = rally([1] * 11 + [2] * 9)        # 11-0 puis 0-9
        completed, cur = compute_sets(points)
        self.assertEqual(completed, [(11, 0)])
        self.assertEqual(cur, (0, 9))

    def test_deux_points_d_ecart_obligatoires(self):
        points = rally([1, 2] * 10 + [1])         # 11-10 : le set continue
        completed, cur = compute_sets(points)
        self.assertEqual(completed, [])
        self.assertEqual(cur, (11, 10))


class TestRecomputePointFields(unittest.TestCase):
    def test_point_oublie_insere_au_milieu(self):
        # 10-5 pose dans l'ordre, puis on se rend compte qu'un point de J2
        # manquait a t=3.5 s : tout ce qui suit doit se renumeroter.
        points = rally([1] * 10 + [2] * 5)
        oublie = pt(3.5, 2)
        out = recompute_point_fields(points + [oublie])

        self.assertEqual([p.timecode for p in out], sorted(p.timecode for p in out))
        scores = [p.score for p in out]
        # t=1,2,3 -> 1-0, 2-0, 3-0 ; puis l'oublie a 3.5 -> 3-1 ; puis la suite decalee
        self.assertEqual(scores[:5], ["1-0", "2-0", "3-0", "3-1", "4-1"])
        self.assertEqual(scores[-1], "10-6")
        self.assertTrue(all(p.set_num == 0 for p in out))
        self.assertTrue(all(p.completed_sets == [] for p in out))

    def test_score_apres_le_point_et_sets_avant_le_point(self):
        points = rally([1] * 11 + [2] * 3)
        out = recompute_point_fields(points)
        dernier_du_set, premier_du_suivant = out[10], out[11]
        # le point qui clot le set porte le score final du set...
        self.assertEqual(dernier_du_set.score, "11-0")
        # ...mais compte encore dans le set 0, qui n'etait pas termine avant lui
        self.assertEqual(dernier_du_set.set_num, 0)
        self.assertEqual(dernier_du_set.completed_sets, [])
        self.assertEqual(premier_du_suivant.score, "0-1")
        self.assertEqual(premier_du_suivant.set_num, 1)
        self.assertEqual(premier_du_suivant.completed_sets, [(11, 0)])

    def test_insertion_anterieure_decale_la_cloture_du_set(self):
        points = rally([1] * 11 + [2] * 9)        # set clos a 11-0
        out = recompute_point_fields(points + [pt(0.5, 2)])
        # J2 a marque avant : le set se clot un point plus tard, a 11-1
        self.assertEqual([p.score for p in out if p.set_num == 0][-1], "11-1")
        self.assertEqual(compute_sets(out)[0], [(11, 1)])

    def test_ne_mute_pas_les_points_d_origine(self):
        # La pile d'annulation ne garde que des copies superficielles de la
        # liste : muter un PointAction en place corromprait undo et redo.
        points = rally([1, 2, 1])
        avant = [(p.id, p.score, p.set_num, list(p.completed_sets)) for p in points]
        out = recompute_point_fields(points)
        self.assertTrue(all(a is not b for a in points for b in out))
        self.assertEqual(
            [(p.id, p.score, p.set_num, list(p.completed_sets)) for p in points], avant)
        self.assertEqual([p.id for p in out], [p.id for p in points])

    def test_conserve_les_coupes_et_rotations(self):
        cut = CutAction(id=gen_id(), start=2.5, end=3.5)
        rot = RotateAction(id=gen_id(), start=0.1, end=0.2, angle=180)
        out = recompute_point_fields(rally([1, 2]) + [cut, rot])
        self.assertEqual([a for a in out if not isinstance(a, PointAction)], [rot, cut])

    def test_idempotent(self):
        points = rally([1, 2] * 8)
        une = recompute_point_fields(points)
        deux = recompute_point_fields(une)
        self.assertEqual([(p.timecode, p.score, p.set_num) for p in une],
                         [(p.timecode, p.score, p.set_num) for p in deux])


class TestConsommateurs(unittest.TestCase):
    def setUp(self):
        self.points = rally([1, 2] * 6 + [1] * 6, step=4.0)

    def test_chapitres_youtube_insensibles_a_l_ordre(self):
        attendu = _youtube_chapters_from_actions(self.points, 120.0, 2.5)
        self.assertTrue(attendu)
        self.assertEqual(
            _youtube_chapters_from_actions(shuffled(self.points, seed=2), 120.0, 2.5),
            attendu)

    def test_stats_insensibles_a_l_ordre(self):
        dicts = as_dicts(recompute_point_fields(self.points))
        self.assertEqual(_compute_stats_from_dicts(shuffled(dicts, seed=3), 1),
                         _compute_stats_from_dicts(dicts, 1))


if __name__ == "__main__":
    unittest.main()
