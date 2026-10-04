#!/usr/bin/env python3
"""Traînée de balle « comète » (rendu TV) pour vidéos de tennis de table.

Pipeline :
  1. détection neuronale BlurBall (blurball_infer.py), image entière ;
  2. suivi multi-hypothèses avec filtre de Kalman (accélération constante) ;
  3. validation balistique, choix de la balle principale (zone de jeu par
     densité, cohérence de taille et de vitesse) ;
  4. seconde passe de détection sur une fenêtre recadrée, uniquement sur les
     frames où la trajectoire retenue a un trou (balle trop petite pour le
     réseau à pleine image) ;
  5. lissage RTS par segment sans rebond, remplissage balistique des trous ;
  6. rendu : cœur fin + halo additif (sans tête ajoutée : la vraie balle
     termine la traînée), fondu temporel (~0,4 s),
     encodage matériel en conservant les métadonnées couleur (HLG 10 bits).
"""
import argparse
import bisect
import json
import math
import os
import subprocess
import sys

import cv2
import numpy as np

# --- Détections ----------------------------------------------------------------
NET_THR = 0.5           # seuil du heatmap (passe 1)
MIN_PEAK = 0.55         # pic minimal d'une composante pour être candidate
MAX_NPX = 170           # composante plus grosse = pas une balle
MAX_MED_NPX = 80        # taille médiane max d'une piste (balle lente)
# Une balle rapide est étalée par le flou de bougé : sa tache grossit avec la
# vitesse (smash ≈ 75-130 px contre ~28 au repos). Les plafonds de taille
# s'ouvrent donc au-delà de NPX_SPEED_FREE px/frame (1080p 60 fps).
NPX_SPEED_FREE = 15.0
MED_NPX_SPEED_K = 2.0   # px de tache médiane en plus par px/frame
NPX_RATIO_SPEED_K = 0.06  # rapport de taille en plus par px/frame
MAX_CANDS = 6           # candidats gardés par frame (meilleurs scores)
BANNER_X, BANNER_Y = 430, 880   # bandeau de score (overlay) : x < 430 et y > 880 à 1080p (échelle selon la taille de la vidéo)
STATIC_SEC = 3.0        # détection qui revient au même endroit (±STATIC_PX) plus de 3 s cumulées : décor, pas la balle
STATIC_PX = 3
MASK_DECOR = True
DETS_VERSION = 2        # version du cache de détections (dets2 = passe recadrée)
TRAJ_VERSION = 7        # version du cache de trajectoire (7 : zone de jeu assouplie, queues de rallye coupées, raccords au filet, bandeau masqué ; 6 : tronçons courts réels gardés, fondu de paraboles, plus de ligne droite de comblement ; 5 : bouts sans support coupés + origine en zone, 2026-10-03)

# --- Passe 2 (recadrage sur les trous) ------------------------------------------
DET2_MAX_ZOOM = 2.5     # zoom max du recadrage (le réseau connaît les vues larges)
DET2_MARGIN = 0.12      # marge autour de la zone de jeu, en fraction de W
DET2_PAD = 4            # frames avant/après chaque piste à re-scruter
DET2_MAX_GAP = 40       # au-delà, ce n'est plus un trou mais une pause
DET2_THR = 0.35         # seuil plus bas : la fenêtre et le trou contraignent déjà
DET2_MAX_FRAC = 0.35    # part max des frames en passe 2 (budget de temps)
DET2_MERGE_PX = 6.0     # même détection dans les deux passes si plus proche

# --- Suivi (Kalman accélération constante, unités : px à 1080p, k = W/1920) -----
SIGMA_JERK = 0.9        # bruit de processus (jerk) px/frame³
INIT_ACC_SIG = 3.0      # incertitude initiale sur l'accélération (px/frame² à 1080p)
SIGMA_MEAS = 2.2        # bruit de mesure px, gonflé par la taille du blob
MEAS_NPX_K = 0.012
MEAS_SRC2_K = 1.6       # les détections recadrées sont un peu moins précises
CHI2_GATE = 13.8        # χ² à 2 ddl, 99,9 %
GATE_MIN, GATE_MAX = 18.0, 200.0
GATE_SPEED_K = 0.6      # la porte pixel s'ouvre avec la vitesse (rebonds, renvois)…
GATE_MISS_K = 8.0       # …et avec les frames ratées (px à 1080p par frame ratée) (était 12)
MISS_VEL_SIG = 1.5      # gonflement de P par frame ratée (px/frame à 1080p)
MISS_ACC_SIG = 0.5
SEED_KEEP = 8           # amorçages gardés par frame en plus des hypothèses établies
KEEP_YOUNG = 16         # hypothèses jeunes (< MIN_HITS) gardées à part des établies
COAST_BRANCH_M2 = 4.0   # match plus net que ça (≈2σ) : pas de branche « raté » en plus
MAX_COAST = 12          # frames sans détection avant d'abandonner (0,2 s à 60)
COAST_DECAY = 0.82      # confiance par frame ratée
SEED_MAX_STEP = 170     # amorçage : deux détections consécutives assez proches
BRANCH = 2              # candidats explorés par hypothèse
KEEP_HYP = 24           # hypothèses vivantes max
FIT_WINDOW = 7

# --- Validation --------------------------------------------------------------------
MIN_HITS = 8
MIN_HIT_RATIO = 0.6
# Balle lente ou latérale (vue en perspective le long de la table) : quelques
# px/frame seulement. Un objet immobile (balle au sol, lampe) ne fait que
# trembler sur place : sa distance totale reste sous MIN_DISPLACEMENT.
MIN_DISPLACEMENT = 45
MIN_MEAN_SPEED = 2.5
# Sous SLOW_SPEED px/frame, la piste doit AVANCER : vitesse nette (distance
# bout à bout / durée) ≥ SLOW_NET_SPEED et trajet peu sinueux (chemin /
# distance ≤ SLOW_PATH_RATIO). Écarte la balle qu'on fait rebondir sur la
# raquette avant de servir, qui va et vient sur place.
SLOW_SPEED = 6.0
SLOW_NET_SPEED = 1.5
SLOW_PATH_RATIO = 1.8
MAX_RMS = 6
SPEED_MIN, SPEED_MAX = 2.0, 120.0   # px/frame à 1080p 60 fps
SPEED_P95_RATIO = 6.0               # p95/médiane des pas : piste bimodale = sauts
NPX_LO, NPX_HI = 0.45, 2.2          # taille relative à la balle principale
HIGH_Y_FRAC, HIGH_Y_PENALTY = 0.30, 6.0
MAX_TRIM = 6            # frames de recouvrement rognées entre deux pistes retenues

# --- Recouture / rallyes ----------------------------------------------------------
MAX_LINK_GAP = 16       # (était 20)
LINK_SPEED = 22.0       # px/frame à 1080p 60 fps : vitesse max crédible dans un trou (était 30)
LINK_SPEED_MUL = 1.3    # …relevée à 1,3× la vitesse mesurée aux bords (smash) (était 1,5)
FAST_ZONE_SPEED = 25.0  # px/frame : au-delà, une piste qui PART de la zone de jeu y appartient
MIN_RALLY_PX = 250      # échange plus court que MIN_RALLY_SEC gardé s'il traverse ≥ 250 px (smash)
ZONE_LOOSE = True       # piste dont le centre est hors ellipse mais dont un bout y touche : candidate de second rang
ZONE_MED_ND = 1.9       # …si sa médiane reste à ≤ 1,9 rayon de l'ellipse de jeu
ZONE_END_ND = 1.0       # …et que sa première/dernière détection vue est dans l'ellipse (× ce rayon)
ZONE_IN_FRAC = 0.25     # …ou qu'au moins ce quart de ses détections vues tombe dans l'ellipse
ZONE_LOOSE_MIN_HITS = 10  # détections vues mini d'une piste de second rang (après rognage)
ZONE_LOOSE_MIN_DISP = 120.0  # px à 1080p : déplacement mini (pas une balle tenue en main / posée)
ZONE_LOOSE_MAX_RMS = 2.5     # ajustement parabolique propre exigé
TAIL_CUT = True         # coupe la queue d'un échange qui sort durablement de la zone de jeu (balle au sol, sortie sur le côté)
TAIL_ND = 2.2           # rayon vertical (ellipse de jeu) vers le BAS au-delà duquel la queue est coupée (sol)
TAIL_ND_SIDE = 2.8      # …et rayon horizontal (balle sortie sur le côté). Vers le haut : jamais (lobs, balle lancée)
TAIL_MIN_OUT = 4        # frames mini passées dehors jusqu'à la fin de l'échange (sinon : bout de trajectoire, on garde)
EXIT_GAP = 4            # piste hors zone gardée si elle prolonge une retenue (≤ 4 frames)
LOOSE_GAP = 2           # trou avant une détection isolée en bout de piste
LINK_SHORT_GAP = 8      # trou ≤ 8 frames (filet, impact, balle masquée un instant) : raccord plus tolérant
LINK_SHORT_MUL = 1.9    # …tolérance × 1,9 (les bords lissés sont peu fiables près d'un impact)
LINK_TOL = 28.0         # px à 1080p entre extrapolation et piste suivante (était 40)
MIN_RALLY_SEC = 0.35    # rien n'est dessiné sous cette durée
FILL_CONF = 0.7
RAMP_FRAMES = 3

# --- Coupes de montage --------------------------------------------------------------
DIFF_SIZE = (128, 72)
DIFF_WIN = 45
CUT_RATIO = 5.0
CUT_MIN_DIFF = 3.0

# --- Rendu -------------------------------------------------------------------------
BOUNCE_VY = 1.5         # px/frame à 1080p : un rebond vu de loin est très plat
COAST_SPLIT = 5         # trou sans détection plus long : le lisseur n'extrapole pas, on recoud
MIN_PIECE = 3           # morceau avec moins de détections vues : jeté, recousu en balistique
KINK_DEG = 45.0         # cassure de direction (renvoi) : on n'arrondit pas le coin
KINK_MIN_STEP = 6.0     # px à 1080p sur 2 frames, pour ne pas lire un angle dans le bruit
# --- Physique : paraboles par segment entre impacts (2026-10-03) ---------------------
PHYS_TAU = 4.0          # px à 1080p : résidu au-delà duquel une détection est aberrante
PHYS_COST_CAP = 3.0     # plafond du coût d'un point, en multiples de TAU (aberrants lointains pèsent plus)
PHYS_JUNK_MAX = 8       # tronçon rebut (aberrant) de ≤ 8 détections : absorbé par ses voisins
PHYS_MIN_SEG = 5        # détections vues mini de chaque côté d'un impact (sinon : glitch)
PHYS_BREAK_COST = 4.0   # gain mini d'une coupure, en multiples de TAU² (≈ 4 aberrantes évitées)
PHYS_G0 = 0.65          # px/frame² : gravité image a priori (rappel faible, tables filmées de loin)
PHYS_G_MAX = 1.8        # px/frame² : courbure max admise d'un segment (×k)
PHYS_AX_MAX = 1.4       # px/frame² : accélération x max (perspective : balle qui s'éloigne), ×k
PHYS_RACKET_DV = 3.0    # px/frame : saut de vitesse (vecteur) qui suffit pour un coup de raquette
PHYS_MAX_RMS = 3.0      # px (×k) : au-delà le segment n'est pas une parabole → repli lisseur RTS
PHYS_EDGE_EXT = 4       # frames max qu'une parabole prolonge un bout de piste au-delà de sa 1re/dernière détection suivie
ORIGIN_ND = 2.0         # une traînée ne peut pas NAÎTRE au-delà de 2 rayons de l'ellipse de jeu (≈ 4 écarts-types)
RTS_CTX_MAXLEN = 16     # …et seulement si le tronçon fait au plus ce nombre de frames
RTS_CTX = 6             # frames de contexte de part et d'autre d'un tronçon lissé par RTS
JUNK_EXPLAIN_TAU = 2.5   # (×TAU) écart max au prolongement des voisines : tronçon = bruit d'impact
JUNK_KEEP_MIN = 4        # tronçon rebut réel gardé (RTS) : détections vues mini
JUNK_KEEP_GAP = 3        # trou max entre deux détections vues consécutives du tronçon
JUNK_KEEP_STEP = 55.0    # px/frame (×k) max entre deux détections : au-delà, ce n'est pas la balle
BLEND_HALF = 5          # frames : fondu entre deux paraboles voisines sans impact
BLEND_MAX_D = 12.0       # px (×k) : au-delà d'un tel écart c'est un vrai coup, on ne fond pas
PHYS_OK_RATIO = 0.7     # part mini de détections vues qui suivent la parabole
SPLINE_STEPS = 6
IMPACT_JOINT = True     # impact sous-frame : ajustement conjoint des deux paraboles (rebonds nets)
IMPACT_DT = 0.1         # pas de recherche de l'instant d'impact (frames)
IMPACT_VX_W = 3.0       # rappel (px par px/frame) : vx quasi conservé à travers un rebond
IMPACT_G_W = 6.0        # rappel : gravité commune aux deux côtés
IMPACT_REST = (0.35, 1.8)   # vy_out/vy_in image hors de cette plage : rappelé vers le bord
IMPACT_MAX_D = 3.0      # écart max (en TAU) entre les deux paraboles libres à l'impact
# Rétabli le 2026-10-01 (valeurs d'avant la retouche du 28/09 19:32, qui
# avait raccourci et assombri la comète : 0.25 s / 0.07 / 0.75 / 0.35).
TRAIL_SEC = 0.45        # longueur de la comète en secondes
TAU_SEC = 0.13          # constante de fondu : 5 % à 0,39 s
CORE_W_FRAC = 0.0025    # largeur du cœur, fraction de W
GLOW_W_MUL = 3.0
GLOW_SIG_FRAC = 0.006
CORE_BGR = np.array([245, 245, 245], np.float32)   # blanc neutre, sans teinte
GLOW_BGR = np.array([225, 225, 225], np.float32)   # halo blanchâtre
CORE_K, GLOW_K = 1.0, 0.0    # halo supprimé le 2026-10-03 (était 0.60) : traînée seule, balle sans brillance
SCREEN_MEAN = 200       # fond très clair : composition « screen » au lieu d'additif
# ancien look (--style classic)
TRAIL_LEN = 24
TAPER_POW = 0.75
TRAIL_ALPHA = 0.70
TRAIL_WIDTH = 8
TRAIL_COLOR = 225
SPEED_REF_LO, SPEED_REF_HI = 8.0, 60.0
WIDTH_AT_SLOW, WIDTH_AT_FAST = 0.55, 1.6


# =============================================================================
# 1. Détections issues du réseau
# =============================================================================

def _cand(c, src):
    return (c["x"], c["y"], c["score"], c.get("npx", 0),
            c.get("len", 0.0), c.get("angle", 0.0), src)


def load_detections(path):
    """JSON produit par blurball_infer.py -> {frame: [(x, y, score, npx, len, angle, src)]}

    Les détections de la passe recadrée (`dets2`) sont fusionnées : elles ne
    servent qu'à prolonger une piste existante (voir track), jamais à en créer.
    """
    blob = json.load(open(path))
    det = {}
    sk = blob.get("w", 1920) / 1920.0
    fps_ = blob.get("fps", 60.0)
    # points fixes (affiche, chiffre du bandeau) : même cellule 3 s ou plus
    cell = max(1, int(STATIC_PX * 2 * sk))
    hist = {}
    for lst in blob["dets"].values():
        for c in lst:
            if c.get("peak", 1.0) >= MIN_PEAK:
                key = (int(c["x"] // cell), int(c["y"] // cell))
                hist[key] = hist.get(key, 0) + 1

    def is_static(c):
        cx, cy = int(c["x"] // cell), int(c["y"] // cell)
        n = sum(hist.get((cx + i, cy + j), 0) for i in (-1, 0, 1) for j in (-1, 0, 1))
        return n >= STATIC_SEC * fps_

    def is_banner(c):
        return c["x"] < BANNER_X * sk and c["y"] > BANNER_Y * sk
    for k, lst in blob["dets"].items():
        keep = [c for c in lst if c.get("peak", 1.0) >= MIN_PEAK
                and c.get("npx", 0) <= MAX_NPX
                and not (MASK_DECOR and (is_banner(c) or is_static(c)))]
        keep.sort(key=lambda c: -c["score"])
        if keep:
            det[int(k)] = [_cand(c, 1) for c in keep[:MAX_CANDS]]
    for k, lst in blob.get("dets2", {}).items():
        f = int(k)
        cur = det.get(f, [])
        for c in lst:
            if c.get("npx", 0) > MAX_NPX:
                continue
            if any(math.hypot(c["x"] - o[0], c["y"] - o[1]) < DET2_MERGE_PX
                   for o in cur):
                continue
            cur.append(_cand(c, 2))
        if cur:
            det[f] = cur
    return det, blob["fps"], blob["frames"], blob


def scan_diffs(path, start, duration, fps):
    """Écart moyen d'une image à la précédente, en miniature."""
    cap = open_at(path, start)
    n_max = int(round(duration * fps)) if duration else 1 << 30
    prev, out = None, []
    while len(out) < n_max:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(fr, DIFF_SIZE), cv2.COLOR_BGR2GRAY)
        g = g.astype(np.float32)
        out.append(0.0 if prev is None else float(np.abs(g - prev).mean()))
        prev = g
    cap.release()
    return out


def detect_cuts(diffs):
    """Frames où commence un nouveau plan.

    Un montage de ping-pong colle des extraits de la *même* caméra : le décor
    ne change pas, donc la détection de scène classique (comparaison d'images)
    ne voit rien. Ce qui trahit une coupe, c'est un écart image-à-image bien
    au-dessus du bruit local — le saut temporel, pas le changement de décor.
    """
    d = np.asarray(diffs, float)
    n = len(d)
    cuts = []
    for i in range(1, n):
        lo, hi = max(0, i - DIFF_WIN), min(n, i + DIFF_WIN + 1)
        w = np.concatenate([d[lo:i], d[i + 1:hi]])
        base = max(float(np.median(w)), 0.3)
        if d[i] >= CUT_MIN_DIFF and d[i] / base >= CUT_RATIO:
            if cuts and i - cuts[-1] <= 2:
                if d[i] > d[cuts[-1]]:
                    cuts[-1] = i
            else:
                cuts.append(i)
    return cuts


def shot_of(f, cuts):
    """Numéro du plan auquel appartient la frame f."""
    lo, hi = 0, len(cuts)
    while lo < hi:                                   # bisect sans import
        mid = (lo + hi) // 2
        if cuts[mid] <= f:
            lo = mid + 1
        else:
            hi = mid
    return lo


# =============================================================================
# 2. Filtre de Kalman (accélération constante, dt = 1 frame)
# =============================================================================

_A = np.array([[1.0, 1.0, 0.5], [0.0, 1.0, 1.0], [0.0, 0.0, 1.0]])
_QA = np.array([[1 / 20.0, 1 / 8.0, 1 / 6.0],
                [1 / 8.0, 1 / 3.0, 1 / 2.0],
                [1 / 6.0, 1 / 2.0, 1.0]])
KF_F = np.zeros((6, 6)); KF_F[:3, :3] = _A; KF_F[3:, 3:] = _A
KF_FT = KF_F.T.copy()
_Q1 = np.zeros((6, 6)); _Q1[:3, :3] = _QA; _Q1[3:, 3:] = _QA
KF_H = np.zeros((2, 6)); KF_H[0, 0] = 1.0; KF_H[1, 3] = 1.0
KF_I = np.eye(6)


class KF:
    """Paramètres d'échelle partagés par toutes les hypothèses d'une vidéo."""

    def __init__(self, k):
        self.k = k
        self.Q = _Q1 * (SIGMA_JERK * k) ** 2
        self.sm = SIGMA_MEAS * k
        self.gate_min = GATE_MIN * k
        self.gate_max = GATE_MAX * k
        v2, a2 = (MISS_VEL_SIG * k) ** 2, (MISS_ACC_SIG * k) ** 2
        self.Pmiss = np.diag([0.0, v2, a2, 0.0, v2, a2])

    def gate(self, speed, miss, dt):
        g = self.gate_min + GATE_SPEED_K * speed * dt + GATE_MISS_K * self.k * miss
        return min(g, self.gate_max)

    def reacquire(self, s, P):
        """Grosse innovation acceptée par la porte pixel (rebond, renvoi) : on
        oublie la dynamique pour que la vitesse se recale sur les mesures."""
        P = P.copy()
        v2 = (2.0 * math.hypot(s[1], s[4]) + 4.0 * self.k) ** 2
        a2 = (INIT_ACC_SIG * self.k) ** 2
        P[1, 1] = max(P[1, 1], v2); P[4, 4] = max(P[4, 4], v2)
        P[2, 2] = max(P[2, 2], a2); P[5, 5] = max(P[5, 5], a2)
        return P

    def meas_sigma(self, npx, src):
        s = self.sm * (1.0 + MEAS_NPX_K * npx)
        return s * MEAS_SRC2_K if src == 2 else s

    def init(self, x0, y0, x1, y1, npx, src):
        s = np.array([x1, x1 - x0, 0.0, y1, y1 - y0, 0.0])
        m2 = self.meas_sigma(npx, src) ** 2
        a2 = (INIT_ACC_SIG * self.k) ** 2
        P = np.diag([m2, 2 * m2, a2, m2, 2 * m2, a2])
        return s, P

    def predict(self, s, P, n=1):
        for _ in range(n):
            s = KF_F @ s
            P = KF_F @ P @ KF_FT + self.Q
        return s, P

    def innov(self, s, P, x, y, sig):
        """Innovation, sa covariance et la distance de Mahalanobis²."""
        nu = np.array([x - s[0], y - s[3]])
        S = P[np.ix_([0, 3], [0, 3])] + np.eye(2) * sig * sig
        Si = np.linalg.inv(S)
        return nu, Si, float(nu @ Si @ nu)

    def update(self, s, P, nu, Si):
        K = P[:, [0, 3]] @ Si                          # P Hᵀ S⁻¹
        s = s + K @ nu
        P = (KF_I - K @ KF_H) @ P
        return s, P


# =============================================================================
# 3. Suivi multi-hypothèses
# =============================================================================

class Hyp:
    __slots__ = ("fr", "xs", "ys", "sc", "npx", "ln", "ang", "src", "seen",
                 "cf", "miss", "hits", "misses", "res", "conf", "s", "P")

    def __init__(self, kf, f0, c0, f1, c1):
        self.fr = [f0, f1]
        self.xs, self.ys = [c0[0], c1[0]], [c0[1], c1[1]]
        self.sc = [c0[2], c1[2]]
        self.npx = [c0[3], c1[3]]
        self.ln, self.ang = [c0[4], c1[4]], [c0[5], c1[5]]
        self.src = [c0[6], c1[6]]
        self.seen = [True, True]
        self.cf = [1.0, 1.0]
        self.miss, self.hits, self.misses, self.res, self.conf = 0, 2, 0, 0.0, 1.0
        self.s, self.P = kf.init(c0[0], c0[1], c1[0], c1[1], c1[3], c1[6])

    def clone(self):
        h = Hyp.__new__(Hyp)
        for a in ("fr", "xs", "ys", "sc", "npx", "ln", "ang", "src", "seen", "cf"):
            setattr(h, a, list(getattr(self, a)))
        h.miss, h.hits, h.misses = self.miss, self.hits, self.misses
        h.res, h.conf = self.res, self.conf
        h.s, h.P = self.s.copy(), self.P.copy()
        return h

    @property
    def speed(self):
        return math.hypot(self.s[1], self.s[4])

    def add(self, f, c, seen, s, P, res=0.0):
        self.fr.append(f)
        self.xs.append(c[0]); self.ys.append(c[1]); self.sc.append(c[2])
        self.npx.append(c[3]); self.ln.append(c[4]); self.ang.append(c[5])
        self.src.append(c[6]); self.seen.append(seen)
        self.s, self.P = s, P
        if seen:
            self.hits += 1
            self.miss = 0
            self.res += res
            self.conf = 1.0
        else:
            self.misses += 1
            self.miss += 1
            self.conf *= COAST_DECAY
        self.cf.append(self.conf)

    def score(self):
        return (self.hits - 1.0 * self.misses
                - 0.05 * self.res / max(1, self.hits) + 2.0 * self.conf)

    def sig(self):
        """Trois dernières détections *vues* : deux branches qui ont divergé
        (raté / touché) puis reconvergent sur les mêmes détections sont la
        même piste, on ne garde que la mieux notée."""
        out, i = [], len(self.fr) - 1
        while i >= 0 and len(out) < 3:
            if self.seen[i]:
                out.append((self.fr[i], round(self.xs[i], 1), round(self.ys[i], 1)))
            i -= 1
        return tuple(out)


def _finish(h, out):
    while h.seen and not h.seen[-1]:                     # pas de queue en l'air
        for a in ("fr", "xs", "ys", "sc", "npx", "ln", "ang", "src", "seen", "cf"):
            getattr(h, a).pop()
    if len(h.fr) >= MIN_HITS:
        out.append(h)


def track(det, f0, f1, cuts, kf, verbose=True, progress=None, ranges=None):
    """Fait pousser des hypothèses image par image, en garde les meilleures.

    Aucune piste ne franchit un changement de plan : au montage, la frame
    suivante montre une autre table, et prolonger la trajectoire ferait sauter
    la traînée sur la balle d'un autre échange.

    `ranges` : plages [a, b) réellement analysées (analyse limitée aux parties
    gardées) ; on ne parcourt qu'elles. Chaque début de plage doit figurer dans
    `cuts` : rien ne traverse un trou non analysé.
    """
    cutset = set(cuts)
    live, done = [], []
    spans = ranges or [(f0, f1)]
    n_all = max(1, sum(b - a for a, b in spans))
    k_done = 0
    for f in (f for a, b in spans for f in range(a, b)):
        if f in cutset:                              # nouveau plan : on repart à zéro
            for h in live:
                _finish(h, done)
            live = []
        cands = det.get(f, [])
        nxt = []
        for h in live:
            used = False
            dt = f - h.fr[-1]
            s_, P_ = kf.predict(h.s, h.P, dt)
            gate = kf.gate(h.speed, h.miss, dt)
            near = []
            for c in cands:
                d = math.hypot(c[0] - s_[0], c[1] - s_[3])
                if d > kf.gate_max:
                    continue
                nu, Si, m2 = kf.innov(s_, P_, c[0], c[1], kf.meas_sigma(c[3], c[6]))
                if m2 <= CHI2_GATE or d <= gate:
                    near.append((m2, d, c, nu, Si))
            near.sort(key=lambda z: z[0])
            clean = bool(near) and near[0][0] <= COAST_BRANCH_M2
            for m2, d, c, nu, Si in near[:BRANCH]:
                g = h.clone()
                if m2 > CHI2_GATE:                       # accepté par la porte pixel
                    P2 = kf.reacquire(s_, P_)
                    nu, Si, _ = kf.innov(s_, P2, c[0], c[1], kf.meas_sigma(c[3], c[6]))
                    s2, P2 = kf.update(s_, P2, nu, Si)
                else:
                    s2, P2 = kf.update(s_, P_, nu, Si)
                g.add(f, c, True, s2, P2, d)
                nxt.append(g)
                used = True
            # branche « raté » : toujours si rien n'a matché, sinon seulement quand
            # le match est douteux (sinon les clones étouffent les autres pistes)
            if h.miss + 1 <= MAX_COAST and not clean:
                g = h.clone()
                g.add(f, (s_[0], s_[3], 0.0, h.npx[-1], h.ln[-1], h.ang[-1], 0),
                      False, s_, P_ + kf.Pmiss)
                nxt.append(g)
            elif not used:
                _finish(h, done)
        # amorçages : deux détections consécutives assez proches (passe 1 seulement),
        # sauf si une hypothèse vivante se termine déjà par ces deux points
        prev = [] if f in cutset else det.get(f - 1, [])
        tails = {(h.fr[-2], round(h.xs[-2], 1), round(h.ys[-2], 1),
                  round(h.xs[-1], 1), round(h.ys[-1], 1)) for h in nxt if len(h.fr) >= 2}
        seeds = []
        for c in cands:
            if c[6] == 2:
                continue
            for p in prev:
                if p[6] == 2:
                    continue
                if math.hypot(c[0] - p[0], c[1] - p[1]) > SEED_MAX_STEP * kf.k:
                    continue
                key = (f - 1, round(p[0], 1), round(p[1], 1), round(c[0], 1), round(c[1], 1))
                if key in tails:
                    continue
                seeds.append(Hyp(kf, f - 1, p, f, c))
        seeds.sort(key=lambda h: -(h.sc[0] + h.sc[1]))
        # déduplication + élagage ; les amorçages ont leurs propres places pour
        # ne pas être étouffés par les clones d'une longue hypothèse
        seen_sig, uniq = set(), []
        for h in sorted(nxt, key=lambda h: -h.score()):
            sg = h.sig()
            if sg in seen_sig:
                continue
            seen_sig.add(sg)
            uniq.append(h)
        old = [h for h in uniq if h.hits >= MIN_HITS]
        young = [h for h in uniq if h.hits < MIN_HITS]
        live = old[:KEEP_HYP] + young[:KEEP_YOUNG] + seeds[:SEED_KEEP]
        for h in old[KEEP_HYP:] + young[KEEP_YOUNG:]:
            _finish(h, done)
        if f % 300 == 0 and verbose:
            print(f"  suivi {f}/{f1}  ({len(done)} pistes closes)", flush=True)
        if progress and k_done % 30 == 0:
            progress(k_done, n_all)
        k_done += 1
    for h in live:
        _finish(h, done)
    out = []
    for i, h in enumerate(done):
        v = validate(h, kf.k)
        if v:
            out.append(v)
        if progress and i % 500 == 0:
            print(f"PROGRESS validate {i} {len(done)}", flush=True)
    if progress:
        print(f"PROGRESS validate {len(done)} {len(done)}", flush=True)
    return out


# =============================================================================
# 4. Validation balistique
# =============================================================================

def validate(h, k=1.0):
    n = len(h.fr)
    if n < MIN_HITS or h.hits < MIN_HITS:
        return None
    span = h.fr[-1] - h.fr[0] + 1
    if h.hits / float(span) < MIN_HIT_RATIO:
        return None
    xs, ys = np.array(h.xs, float), np.array(h.ys, float)
    disp = math.hypot(xs[-1] - xs[0], ys[-1] - ys[0])
    if disp < MIN_DISPLACEMENT * k:
        return None
    step = np.hypot(np.diff(xs), np.diff(ys))
    speed = float(np.median(step))
    if speed < MIN_MEAN_SPEED * k:
        return None
    if float(np.percentile(step, 95)) > SPEED_P95_RATIO * max(speed, 1e-6):
        return None                      # saute entre deux objets
    if speed < SLOW_SPEED * k:           # balle lente : elle doit aller quelque part
        span_f = max(1.0, float(h.fr[-1] - h.fr[0]))
        if (disp / span_f < SLOW_NET_SPEED * k
                or float(step.sum()) > SLOW_PATH_RATIO * max(disp, 1e-6)):
            return None
    res = []
    fs = np.array(h.fr, float)
    for i in range(n):
        a, b = max(0, i - 3), min(n, i + 4)
        if b - a < 5:
            continue
        t = fs[a:b] - fs[i]
        ex = np.polyval(np.polyfit(t, xs[a:b], 2), 0.0)
        ey = np.polyval(np.polyfit(t, ys[a:b], 2), 0.0)
        res.append(math.hypot(ex - xs[i], ey - ys[i]))
    rms = float(np.median(res)) if res else 0.0
    if rms > MAX_RMS * k:
        return None
    npx = [p for p, s in zip(h.npx, h.seen) if s]
    med_npx = float(np.median(npx)) if npx else 0.0
    if med_npx > MAX_MED_NPX + MED_NPX_SPEED_K * max(0.0, speed / k - NPX_SPEED_FREE):
        return None
    return {"f0": h.fr[0], "f1": h.fr[-1], "fr": h.fr, "xs": h.xs, "ys": h.ys,
            "seen": h.seen, "npx": h.npx, "ln": h.ln, "ang": h.ang, "src": h.src,
            "cf": h.cf, "hits": h.hits, "speed": speed, "rms": rms,
            "med_npx": med_npx,
            "score": h.hits - 1.2 * rms / k + 0.05 * min(speed / k, 60.0)}


def play_zone(tracklets, width, height):
    """Ellipse de la zone de jeu principale, par densité des points de piste.

    Les tables du fond restent chacune dans son coin ; la balle qu'on suit
    passe son temps autour de la table filmée. On prend le blob dominant de
    l'histogramme 2D des points (plutôt qu'une médiane, qui bascule dès qu'un
    arrière-plan actif marque plus de points sur un plan).
    """
    xs = np.concatenate([np.asarray(t["xs"], float) for t in tracklets])
    ys = np.concatenate([np.asarray(t["ys"], float) for t in tracklets])
    cell = width / 32.0
    nx, ny = 32, max(1, int(math.ceil(height / cell)))
    hist, _, _ = np.histogram2d(ys, xs, bins=[ny, nx],
                                range=[[0, ny * cell], [0, nx * cell]])
    hist = cv2.GaussianBlur(hist.astype(np.float32), (3, 3), 0)
    mask = (hist > 0.25 * hist.max()).astype(np.uint8)
    n, lab = cv2.connectedComponents(mask)
    best, best_w = 1, -1.0
    for m in range(1, n):
        w = float(hist[lab == m].sum())
        if w > best_w:
            best, best_w = m, w
    ix = np.clip((xs / cell).astype(int), 0, nx - 1)
    iy = np.clip((ys / cell).astype(int), 0, ny - 1)
    inside = lab[iy, ix] == best
    if inside.sum() < 4:
        inside = np.ones_like(inside, bool)
    cx, cy = float(xs[inside].mean()), float(ys[inside].mean())
    rx = max(2.0 * float(xs[inside].std()), 0.12 * width)
    ry = max(2.0 * float(ys[inside].std()), 0.12 * height)
    return cx, cy, rx, ry


def select_main_ball(tracklets, cuts=(), width=1920, height=1080, fps=60.0):
    """Une seule balle : les pistes retenues ne peuvent pas se chevaucher.

    Quatre filtres : zone de jeu (densité), taille du blob cohérente avec la
    balle principale, vitesse plausible, pénalité pour les pistes en haut du
    cadre. Puis, plan par plan, sélection gloutonne sans recouvrement.
    """
    if not tracklets:
        return []
    k = width / 1920.0
    cx, cy, rx, ry = play_zone(tracklets, width, height)

    def inside(x, y):
        return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0

    def in_zone(t):
        if inside(float(np.median(t["xs"])), float(np.median(t["ys"]))):
            return True
        # Smash : la balle part de la raquette (dans la zone) et file vers le
        # bord de l'image en quelques frames — sa médiane tombe dehors. On
        # regarde alors le départ (ou l'arrivée) de la piste.
        if t["speed"] / k * 60.0 / fps >= FAST_ZONE_SPEED:
            seen = [i for i, sn in enumerate(t["seen"]) if sn]
            if seen and (inside(t["xs"][seen[0]], t["ys"][seen[0]])
                         or inside(t["xs"][seen[-1]], t["ys"][seen[-1]])):
                return True
        return False

    zone = [t for t in tracklets if in_zone(t)]
    if not zone:
        return []

    def nd(x, y):
        return math.hypot((x - cx) / rx, (y - cy) / ry)

    def loose_zone(t):
        """Lob, service haut, échange près d'un joueur : la médiane sort de
        l'ellipse mais la piste y naît ou y meurt, et se comporte en balle."""
        if not ZONE_LOOSE:
            return False
        if nd(float(np.median(t["xs"])), float(np.median(t["ys"]))) > ZONE_MED_ND:
            return False
        seen = [i for i, sn in enumerate(t["seen"]) if sn]
        if not seen:
            return False
        n_in = sum(1 for i in seen if nd(t["xs"][i], t["ys"][i]) <= ZONE_END_ND)
        if not (nd(t["xs"][seen[0]], t["ys"][seen[0]]) <= ZONE_END_ND
                or nd(t["xs"][seen[-1]], t["ys"][seen[-1]]) <= ZONE_END_ND
                or n_in >= ZONE_IN_FRAC * len(seen)):
            return False
        xs = np.asarray(t["xs"], float)[seen]
        ys = np.asarray(t["ys"], float)[seen]
        disp = math.hypot(float(xs.max() - xs.min()), float(ys.max() - ys.min()))
        return (t["hits"] >= ZONE_LOOSE_MIN_HITS and disp >= ZONE_LOOSE_MIN_DISP * k
                and t["rms"] <= ZONE_LOOSE_MAX_RMS * k)
    npx_ref = float(np.median([t["med_npx"] for t in zone if t["med_npx"] > 0] or [0]))
    v_lo = SPEED_MIN * k * fps / 60.0
    v_hi = SPEED_MAX * k * fps / 60.0

    def plausible(t):
        if npx_ref > 0 and t["med_npx"] > 0:
            r = t["med_npx"] / npx_ref
            v60 = t["speed"] / k * 60.0 / fps
            hi = NPX_HI + NPX_RATIO_SPEED_K * max(0.0, v60 - NPX_SPEED_FREE)
            if r < NPX_LO or r > hi:
                return False
        return v_lo <= t["speed"] <= v_hi

    ok = []
    for t in zone:
        if not plausible(t):
            continue
        t = dict(t)
        if float(np.median(t["ys"])) < HIGH_Y_FRAC * height:
            t["score"] -= HIGH_Y_PENALTY
        ok.append(t)

    by_shot = {}
    for t in ok:
        by_shot.setdefault(shot_of(t["f0"], cuts), []).append(t)
    chosen = []
    for lst in by_shot.values():
        taken = []
        for t in sorted(lst, key=lambda t: -t["score"]):
            ov = sum(max(0, min(t["f1"], b) - max(t["f0"], a) + 1) for a, b in taken)
            if ov > MAX_TRIM:
                continue
            if ov:
                # Coup de raquette : le suivi repart d'une nouvelle hypothèse qui
                # reprend la dernière détection de la précédente. On rogne le
                # recouvrement au lieu de jeter toute la suite de l'échange.
                t = _trim_taken(t, taken)
                if t is None:
                    continue
            chosen.append(t)
            taken.append((t["f0"], t["f1"]))
    # second rang : pistes « zone souple », uniquement dans les trous laissés
    # par les pistes de première classe (jamais à leur place)
    loose = []
    for t in tracklets:
        if not in_zone(t) and plausible(t) and loose_zone(t):
            t = dict(t)
            loose.append(t)
    n_loose = 0
    for lst_shot in sorted({shot_of(t["f0"], cuts) for t in loose}):
        taken = [(c["f0"], c["f1"]) for c in chosen if shot_of(c["f0"], cuts) == lst_shot]
        for t in sorted([t for t in loose if shot_of(t["f0"], cuts) == lst_shot],
                        key=lambda t: -t["score"]):
            ov = sum(max(0, min(t["f1"], b) - max(t["f0"], a) + 1) for a, b in taken)
            if ov:
                t = _trim_taken(t, taken)
                if t is None or t["hits"] < ZONE_LOOSE_MIN_HITS:
                    continue
            chosen.append(t)
            taken.append((t["f0"], t["f1"]))
            n_loose += 1
    return _add_exits(chosen, [t for t in tracklets if not in_zone(t) and plausible(t)],
                      cuts, k, fps)


def _add_exits(chosen, outside, cuts, k, fps):
    """Smash, balle qui file hors de la table : la piste sort de la zone de jeu
    et son centre tombe dehors. On la garde si elle prolonge directement une
    piste retenue (ou la précède) : même plan, trou ≤ EXIT_GAP frames, départ
    à portée de la vitesse mesurée. Une balle d'une autre table, elle, n'est
    raccordée à rien."""
    if not chosen or not outside:
        return sorted(chosen, key=lambda t: t["f0"])
    taken = [(t["f0"], t["f1"]) for t in chosen]
    anchors = list(chosen)
    for t in sorted(outside, key=lambda t: -t["score"]):
        ov = sum(max(0, min(t["f1"], b) - max(t["f0"], a) + 1) for a, b in taken)
        if ov > MAX_TRIM:
            continue
        hit = False
        for c in anchors:
            for (fa, xa, ya, fb, xb, yb) in ((c["f1"], c["xs"][-1], c["ys"][-1],
                                              t["f0"], t["xs"][0], t["ys"][0]),
                                             (t["f1"], t["xs"][-1], t["ys"][-1],
                                              c["f0"], c["xs"][0], c["ys"][0])):
                gap = fb - fa
                if not (-MAX_TRIM <= gap <= EXIT_GAP):
                    continue
                if shot_of(fa, cuts) != shot_of(fb, cuts):
                    continue
                v = max(c["speed"], t["speed"])
                reach = (LINK_SPEED_MUL * v) * max(1, abs(gap)) + LINK_TOL * k
                if math.hypot(xb - xa, yb - ya) <= reach:
                    hit = True
                    break
            if hit:
                break
        if not hit:
            continue
        if ov:
            t = _trim_taken(t, taken)
            if t is None:
                continue
        chosen.append(t)
        taken.append((t["f0"], t["f1"]))
    return sorted(chosen, key=lambda t: t["f0"])


def _trim_taken(t, taken):
    """Garde le plus long bloc contigu de t hors des intervalles déjà retenus,
    sans point extrapolé aux bouts ; None s'il n'en reste pas assez."""
    keep = [i for i, f in enumerate(t["fr"])
            if not any(a <= f <= b for a, b in taken)]
    if not keep:
        return None
    blocks, cur = [], [keep[0]]
    for i in keep[1:]:
        if i == cur[-1] + 1:
            cur.append(i)
        else:
            blocks.append(cur)
            cur = [i]
    blocks.append(cur)
    idx = max(blocks, key=len)
    while idx and not t["seen"][idx[0]]:
        idx = idx[1:]
    while idx and not t["seen"][idx[-1]]:
        idx = idx[:-1]
    if sum(1 for i in idx if t["seen"][i]) < MIN_PIECE:
        return None
    u = dict(t)
    for key in _SEQ_KEYS:
        u[key] = [t[key][i] for i in idx]
    u["f0"], u["f1"] = u["fr"][0], u["fr"][-1]
    u["hits"] = sum(1 for x in u["seen"] if x)
    return u


# =============================================================================
# 5. Lissage RTS et recouture balistique
# =============================================================================

def _bounce_indices(pts, k=1.0):
    """Indices des creux descente→montée.

    Vus de loin, les rebonds sont presque plats à l'image : on cherche le
    changement de signe de la vitesse verticale sur 3 frames de chaque côté,
    avec un seuil au-dessus du bruit de mesure (sinon un point bruité passe
    pour un rebond et le lisseur y casse la trajectoire pour rien).
    """
    thr = BOUNCE_VY * k
    cands = []
    for i in range(1, len(pts) - 1):
        a = max(0, i - 3)
        b = min(len(pts) - 1, i + 3)
        incoming = (pts[i][1] - pts[a][1]) / float(i - a)
        outgoing = (pts[b][1] - pts[i][1]) / float(b - i)
        if incoming > thr and outgoing < -thr:
            cands.append(i)
    bounces = []
    for i in cands:
        if bounces and i <= bounces[-1] + 3:
            if pts[i][1] > pts[bounces[-1]][1]:
                bounces[-1] = i
        else:
            bounces.append(i)
    return bounces


def _kink_indices(pts, k=1.0):
    """Cassures de direction (renvoi de raquette, balle qui frappe le filet) :
    l'angle entre la vitesse entrante et sortante dépasse KINK_DEG."""
    out = []
    n = len(pts)
    for i in range(2, n - 2):
        ax, ay = pts[i][0] - pts[i - 2][0], pts[i][1] - pts[i - 2][1]
        bx, by = pts[i + 2][0] - pts[i][0], pts[i + 2][1] - pts[i][1]
        na, nb = math.hypot(ax, ay), math.hypot(bx, by)
        if na < KINK_MIN_STEP * k or nb < KINK_MIN_STEP * k:
            continue
        cos = (ax * bx + ay * by) / (na * nb)
        if cos < math.cos(math.radians(KINK_DEG)):
            if out and i <= out[-1] + 2:
                continue
            out.append(i)
    return out


def _forward(kf, t, lo, hi):
    """Filtre avant sur t[lo:hi] (frames contiguës). Renvoie les états
    prédits/filtrés et les innovations normalisées."""
    xs, ys, seen = t["xs"], t["ys"], t["seen"]
    n = hi - lo
    sp, Pp, sf, Pf = [None] * n, [None] * n, [None] * n, [None] * n
    nres = [0.0] * n
    s, P = kf.init(xs[lo], ys[lo], xs[lo + 1] if n > 1 else xs[lo] + 1e-3,
                   ys[lo + 1] if n > 1 else ys[lo], t["npx"][lo], t["src"][lo])
    s[0], s[3] = xs[lo], ys[lo]
    sp[0], Pp[0] = s, P
    if seen[lo]:
        sig = kf.meas_sigma(t["npx"][lo], t["src"][lo])
        nu, Si, _ = kf.innov(s, P, xs[lo], ys[lo], sig)
        s, P = kf.update(s, P, nu, Si)
    sf[0], Pf[0] = s, P
    for i in range(1, n):
        j = lo + i
        s, P = kf.predict(s, P, 1)
        sp[i], Pp[i] = s, P
        if seen[j]:
            sig = kf.meas_sigma(t["npx"][j], t["src"][j])
            nu, Si, m2 = kf.innov(s, P, xs[j], ys[j], sig)
            nres[i] = math.sqrt(m2)                  # distance de Mahalanobis
            if m2 > CHI2_GATE:                       # cassure : on recale la vitesse
                P = kf.reacquire(s, P)
                nu, Si, _ = kf.innov(s, P, xs[j], ys[j], sig)
            s, P = kf.update(s, P, nu, Si)
        sf[i], Pf[i] = s, P
    return sp, Pp, sf, Pf, nres


def _rts(kf, sp, Pp, sf, Pf):
    n = len(sf)
    out = [None] * n
    s, P = sf[-1], Pf[-1]
    out[-1] = s
    for i in range(n - 2, -1, -1):
        try:
            C = Pf[i] @ KF_FT @ np.linalg.inv(Pp[i + 1])
        except np.linalg.LinAlgError:
            C = np.zeros((6, 6))
        s = sf[i] + C @ (s - sp[i + 1])
        P = Pf[i] + C @ (P - Pp[i + 1]) @ C.T
        out[i] = s
    return out


# --- Segments paraboliques entre impacts --------------------------------------------
# Entre deux impacts (raquette, rebond) la balle suit x linéaire / y quadratique.
# On ajuste donc chaque segment par ce modèle (moindres carrés tronqués : les
# détections aberrantes sont ignorées) et on ne coupe qu'à un impact plausible
# appuyé par ≥ PHYS_MIN_SEG détections de chaque côté. Tout autre coude est rejeté.

def _pfit(ii, X, Y, k):
    """Ajuste x = a+b·u, y = c+d·u+e·u² (u = i − centre) sur les indices ii.
    Renvoie (fit, coût, masque d'inliers) ou None."""
    n = len(ii)
    tau = PHYS_TAU * k
    tc = float(ii.mean())
    u = ii - tc
    xs, ys = X[ii], Y[ii]
    mask = np.ones(n, bool)
    A2 = np.stack([np.ones(n), u, u * u], 1)
    lam_y, lam_x = 20.0, 80.0                    # rappels faibles : courbure y → G0, x → 0
    c2y_0 = 0.5 * PHYS_G0 * k
    cxmax = 0.5 * PHYS_AX_MAX * k
    cymax = 0.5 * PHYS_G_MAX * k
    for lim in (3.0, 1.5, 1.0, 1.0):
        m = mask
        if m.sum() < 3:
            return None
        Am = A2[m]
        Ax = np.vstack([Am, [0.0, 0.0, math.sqrt(lam_x)]])
        cx = np.linalg.lstsq(Ax, np.concatenate([xs[m], [0.0]]), rcond=None)[0]
        cx[2] = min(max(cx[2], -cxmax), cxmax)
        cx[:2] = np.linalg.lstsq(Am[:, :2], xs[m] - cx[2] * u[m] ** 2, rcond=None)[0]
        Ay = np.vstack([Am, [0.0, 0.0, math.sqrt(lam_y)]])
        cy = np.linalg.lstsq(Ay, np.concatenate([ys[m], [math.sqrt(lam_y) * c2y_0]]),
                             rcond=None)[0]
        cy[2] = min(max(cy[2], 0.0), cymax)
        cy[:2] = np.linalg.lstsq(Am[:, :2], ys[m] - cy[2] * u[m] ** 2, rcond=None)[0]
        r = np.hypot(A2 @ cx - xs, A2 @ cy - ys)
        new = r < lim * tau
        if new.sum() < 3 or (new == mask).all():
            mask = new if new.sum() >= 3 else mask
            break
        mask = new
    cost = float(np.minimum(r * r, PHYS_COST_CAP ** 2 * tau * tau).sum())
    return {"tc": tc, "cx": cx, "cy": cy}, cost, mask, r


def _peval(fit, i):
    u = i - fit["tc"]
    cx, cy = fit["cx"], fit["cy"]
    return (cx[0] + cx[1] * u + cx[2] * u * u, cx[1] + 2.0 * cx[2] * u,
            cy[0] + cy[1] * u + cy[2] * u * u, cy[1] + 2.0 * cy[2] * u, 2.0 * cy[2])


def _impact_ok(vxa, vya, vxb, vyb, k):
    """Le changement de vitesse (avant → après) est-il un coup de raquette ou un
    rebond ? Les coudes modestes (< KINK_DEG) passent."""
    na, nb = math.hypot(vxa, vya), math.hypot(vxb, vyb)
    if na < 1e-6 or nb < 1e-6:
        return True
    cos = (vxa * vxb + vya * vyb) / (na * nb)
    if cos >= math.cos(math.radians(KINK_DEG)):
        return True
    if vxa * vxb < 0 and abs(vxa) >= 1.0 * k and abs(vxb) >= 1.0 * k:
        return True                                  # raquette : vx s'inverse
    if math.hypot(vxb - vxa, vyb - vya) >= PHYS_RACKET_DV * k:
        return True                                  # raquette : impulsion nette
    if vya > BOUNCE_VY * k * 0.5 and vyb < -BOUNCE_VY * k * 0.5:
        return True                                  # rebond : vy descend → monte
    return False


def _plausible_impact(fa, fb, ta, tb, k):
    _, vxa, _, vya, _ = _peval(fa, ta)
    _, vxb, _, vyb, _ = _peval(fb, tb)
    return _impact_ok(vxa, vya, vxb, vyb, k)


def _split_phys(ii, X, Y, k, depth=0):
    """Segmentation récursive de ii (indices vus) en segments paraboliques.
    Renvoie [(ii_segment, fit, masque)]."""
    base = _pfit(ii, X, Y, k)
    n = len(ii)
    leaf = [(ii, base[0], base[2])] if base else [(ii, None, None)]
    if base is None or n < 2 * PHYS_MIN_SEG or depth > 6:
        return leaf
    tau2 = (PHYS_TAU * k) ** 2
    if base[1] <= PHYS_BREAK_COST * tau2:             # déjà une parabole propre
        return leaf
    best = None
    step = 1 if n <= 90 else 2
    for m in range(PHYS_MIN_SEG, n - PHYS_MIN_SEG + 1, step):
        a = _pfit(ii[:m], X, Y, k)
        b = _pfit(ii[m:], X, Y, k)
        if a is None or b is None:
            continue
        c = a[1] + b[1]
        if best is None or c < best[0]:
            best = (c, m, a, b)
    if best is None or base[1] - best[0] <= PHYS_BREAK_COST * tau2:
        return leaf
    _, m, a, b = best
    if not _plausible_impact(a[0], b[0], float(ii[m - 1]), float(ii[m]), k):
        return leaf                                   # coude non physique : rejeté
    return _split_phys(ii[:m], X, Y, k, depth + 1) + _split_phys(ii[m:], X, Y, k, depth + 1)


def _phys_segments(kf, t):
    """Segments paraboliques de la piste [(indices, fit|None, masque)] ou None.

    Segments : paraboles robustes (détections aberrantes ignorées) ; un petit
    tronçon rebut est absorbé par ses voisins ; un long tronçon non balistique
    garde le lisseur RTS. Un coude non physique entre deux paraboles voisines
    est rejeté (le segment le plus court est traité comme aberrant).
    """
    n = len(t["fr"])
    seen = [i for i in range(n) if t["seen"][i]]
    if len(seen) < 2 * PHYS_MIN_SEG:
        return None
    k = kf.k
    X = np.asarray(t["xs"], float)
    Y = np.asarray(t["ys"], float)
    segs = _split_phys(np.asarray(seen, int), X, Y, k)
    if any(s[1] is None for s in segs):
        return None

    def _good(seg):
        ii, fit, mask = seg
        if mask.sum() < PHYS_OK_RATIO * len(ii):
            return False
        i_in = ii[mask].astype(int)
        res = [math.hypot(_peval(fit, i)[0] - X[i], _peval(fit, i)[2] - Y[i]) for i in i_in]
        return math.sqrt(sum(r * r for r in res) / max(len(res), 1)) <= PHYS_MAX_RMS * k

    def _coherent(seg):
        """Tronçon court non parabolique mais réel : détections quasi
        consécutives, pas de saut de balle impossible. Il garde le lisseur RTS au lieu d'être jeté (un
        trou raccordé en ligne droite laisserait la tête de comète loin de la
        balle visible, ex. rebond + renvoi serrés)."""
        ii = seg[0]
        if len(ii) < JUNK_KEEP_MIN:
            return False
        for a, b in zip(ii[:-1], ii[1:]):
            d = int(b - a)
            if d > JUNK_KEEP_GAP:
                return False
            if math.hypot(X[b] - X[a], Y[b] - Y[a]) > JUNK_KEEP_STEP * k * d:
                return False
        return True

    def _explained(j):
        """Le tronçon rebut j n'est que du bruit autour d'un impact : chaque
        détection est expliquée par le prolongement de la parabole bonne qui
        précède OU par le recul de celle qui suit. Alors on garde l'ancien
        comportement (le raccord/impact conjoint des voisines, sommet exact)."""
        a = next((segs[q] for q in range(j - 1, -1, -1) if _good(segs[q])), None)
        b = next((segs[q] for q in range(j + 1, len(segs)) if _good(segs[q])), None)
        if a is None or b is None:
            return False
        for i in segs[j][0]:
            ea, eb = _peval(a[1], float(i)), _peval(b[1], float(i))
            d = min(math.hypot(ea[0] - X[i], ea[2] - Y[i]),
                    math.hypot(eb[0] - X[i], eb[2] - Y[i]))
            if d > JUNK_EXPLAIN_TAU * PHYS_TAU * k:
                return False
        return True

    keep = []
    for j, sg in enumerate(segs):
        if _good(sg):
            keep.append(sg)
        elif len(sg[0]) > PHYS_JUNK_MAX:
            keep.append((sg[0], None, None))             # lisseur RTS sur ce tronçon
        elif _coherent(sg) and not _explained(j):
            keep.append((sg[0], None, None))             # vrai tronçon court : RTS, pas de trou
    merged = []                                           # tronçons RTS voisins : un seul passage
    for sg in keep:
        if merged and sg[1] is None and merged[-1][1] is None:
            merged[-1] = (np.concatenate([merged[-1][0], sg[0]]), None, None)
        else:
            merged.append(sg)
    keep = merged
    if not any(sg[1] is not None for sg in keep):
        return None
    changed = True
    while changed and len(keep) > 1:                      # coudes non physiques
        changed = False
        for j in range(len(keep) - 1):
            A, B = keep[j], keep[j + 1]
            if A[1] is None or B[1] is None:
                continue
            if not _plausible_impact(A[1], B[1], float(A[0][-1]), float(B[0][0]), k):
                if len(A[0]) <= len(B[0]):
                    keep.pop(j)
                else:
                    keep.pop(j + 1)
                changed = True
                break
    return keep


def _joint_impact(pool, wpool, fa, fb, ia, ib, X, Y, k, bounce, mode, anchor=None):
    """Impact sous-frame entre deux paraboles : ajustement CONJOINT.

    Les deux côtés passent par un même point (t*, x*, y*) et partagent la même
    gravité ; pour un rebond, vx est rappelé vers la conservation et le rapport
    vy_out/vy_in vers une plage plausible. Pour t* fixé le problème est linéaire
    (x*, y*, vx±, vy±, g) ; on balaie t* (pas IMPACT_DT) puis on affine.
    `pool` : indices de détections inliers des deux côtés ; `wpool` : poids ;
    `anchor` : (t, x, y) point déjà fixé du côté gauche (impact précédent).
    Renvoie dict(t, x, y, fa, fb, cost, rms) ou None.
    """
    tau = PHYS_TAU * k
    pool = np.asarray(pool, float)
    n = len(pool)
    g0 = 0.5 * (2.0 * fa["cy"][2] + 2.0 * fb["cy"][2])
    g0 = min(max(g0, 0.2 * k), PHYS_G_MAX * k)
    px, py = X[pool.astype(int)], Y[pool.astype(int)]

    def solve(ts, mode, rest=None):
        left = pool < ts - 1e-9
        right = pool > ts + 1e-9
        both = ~left & ~right
        left, right = left | both, right | both
        if left.sum() < 3 or right.sum() < 3:
            return None
        rows, rhs, wt = [], [], []
        for sel, ci in ((left, 0), (right, 1)):
            for idx in np.nonzero(sel)[0]:
                u = pool[idx] - ts
                w = wpool[idx]
                rx = np.zeros(9); rx[0] = 1.0; rx[2 + ci] = u; rx[7 + ci] = u * u
                ry = np.zeros(9); ry[1] = 1.0; ry[4 + ci] = u; ry[6] = 0.5 * u * u
                rows += [rx, ry]; rhs += [px[idx], py[idx]]; wt += [w, w]
        if anchor is not None and anchor[0] < ts - 0.5:
            u = anchor[0] - ts
            rx = np.zeros(9); rx[0] = 1.0; rx[2] = u; rx[7] = u * u
            ry = np.zeros(9); ry[1] = 1.0; ry[4] = u; ry[6] = 0.5 * u * u
            rows += [rx, ry]; rhs += [anchor[1], anchor[2]]; wt += [6.0, 6.0]
        r = np.zeros(9); r[6] = 1.0                       # gravité commune
        rows.append(r); rhs.append(g0); wt.append(IMPACT_G_W)
        for ci in (7, 8):                                 # courbure x : rappel faible vers 0
            r = np.zeros(9); r[ci] = 1.0
            rows.append(r); rhs.append(0.0); wt.append(9.0)
        if mode == "full":
            r = np.zeros(9); r[2], r[3] = -1.0, 1.0       # vx conservé
            rows.append(r); rhs.append(0.0); wt.append(IMPACT_VX_W)
        if rest is not None:                              # vy_out = −e·vy_in
            r = np.zeros(9); r[5], r[4] = 1.0, rest
            rows.append(r); rhs.append(0.0); wt.append(4.0)
        A = np.array(rows); b = np.array(rhs); w0 = np.array(wt)
        nd = 2 * int(left.sum() + right.sum())            # lignes de détection
        th, wcur = None, w0.copy()
        for _ in range(3):                                # IRLS (Huber)
            th = np.linalg.lstsq(A * wcur[:, None], b * wcur, rcond=None)[0]
            res = A @ th - b
            rr = np.hypot(res[0:nd:2], res[1:nd:2])
            h = 1.0 / np.maximum(1.0, rr / (1.5 * tau))
            wcur = w0.copy(); wcur[0:nd:2] *= h; wcur[1:nd:2] *= h
        th[6] = min(max(th[6], 0.0), PHYS_G_MAX * k)
        th[7:9] = np.clip(th[7:9], -0.5 * PHYS_AX_MAX * k, 0.5 * PHYS_AX_MAX * k)
        res = A @ th - b
        rr = np.hypot(res[0:nd:2], res[1:nd:2])
        cost = float(np.minimum(rr * rr, (PHYS_COST_CAP * tau) ** 2).sum())
        return th, cost, rr

    def best(mode):
        lo, hi = ia - 1.0, ib + 1.0
        cand = None
        grid = np.arange(lo, hi + 1e-9, IMPACT_DT)
        for ts in grid:
            r = solve(ts, mode)
            if r is not None and (cand is None or r[1] < cand[0]):
                cand = (r[1], ts)
        if cand is None:
            return None
        for ts in np.arange(cand[1] - IMPACT_DT, cand[1] + IMPACT_DT + 1e-9, IMPACT_DT / 5):
            r = solve(ts, mode)
            if r is not None and r[1] < cand[0]:
                cand = (r[1], ts)
        ts = cand[1]
        th, cost, rr = solve(ts, mode)
        if bounce and th[4] > 1e-3:
            e = -th[5] / th[4]
            if e < IMPACT_REST[0] or e > IMPACT_REST[1]:
                r2 = solve(ts, mode, min(max(e, IMPACT_REST[0]), IMPACT_REST[1]))
                if r2 is not None:
                    th, cost, rr = r2
        return ts, th, cost, rr

    r = best(mode)
    if r is None:
        return None
    ts, th, cost, rr = r
    fitA = {"tc": ts, "cx": np.array([th[0], th[2], th[7]]),
            "cy": np.array([th[1], th[4], 0.5 * th[6]])}
    fitB = {"tc": ts, "cx": np.array([th[0], th[3], th[8]]),
            "cy": np.array([th[1], th[5], 0.5 * th[6]])}
    return {"t": ts, "x": th[0], "y": th[1], "fa": fitA, "fb": fitB,
            "cost": cost, "rms": float(np.sqrt((rr * rr).mean())), "mode": mode}


def _free_cross(fa, fb, ia, ib):
    """Point de rencontre des deux paraboles libres (non contraintes) :
    (distance min, t, x, y) sur [ia−2, ib+2]."""
    ts = np.arange(ia - 2.0, ib + 2.0 + 1e-9, 0.05)
    best = None
    for t_ in ts:
        a, b = _peval(fa, t_), _peval(fb, t_)
        d = math.hypot(a[0] - b[0], a[2] - b[2])
        if best is None or d < best[0]:
            best = (d, t_, 0.5 * (a[0] + b[0]), 0.5 * (a[2] + b[2]))
    return best


class SmoothMap(dict):
    """{frame: état} + .apex = [(t_frame_float, x, y)] des impacts sous-frame."""
    apex = ()


def _refine_impact(A, B, X, Y, k, anchor):
    """Impact conjoint entre les segments A et B (ou None : on garde le raccord
    historique). Garde-fous : les deux paraboles libres doivent se rejoindre,
    et le sommet ne s'éloigne pas de leur croisement libre de plus de TAU."""
    (ia_, fa, ma), (ib_, fb, mb) = A, B
    tau = PHYS_TAU * k
    ina, inb = ia_[ma].astype(int), ib_[mb].astype(int)
    if len(ina) < 3 or len(inb) < 3:
        return None
    ta, tb = float(ina[-1]), float(inb[0])
    ea, eb = _peval(fa, ta), _peval(fb, tb)
    cross = _free_cross(fa, fb, ta, tb)
    if cross is None or cross[0] > IMPACT_MAX_D * tau:
        return None
    bounce = (ea[3] > 0.0 and eb[3] < 0.0
              and abs(eb[1] - ea[1]) <= 0.45 * abs(ea[1]) + 4.0 * k)   # vx quasi conservé : table
    pool = np.concatenate([ina, inb]).astype(float)
    sep = [math.hypot(_peval(fa, i)[0] - X[int(i)], _peval(fa, i)[2] - Y[int(i)]) for i in ina]
    sep += [math.hypot(_peval(fb, i)[0] - X[int(i)], _peval(fb, i)[2] - Y[int(i)]) for i in inb]
    sep_rms = math.sqrt(sum(r * r for r in sep) / len(sep))
    for mode in (("full", "pos") if bounce else ("pos",)):
        res = _joint_impact(pool, np.ones(len(pool)), fa, fb, ta, tb, X, Y, k,
                            bounce, mode, anchor)
        if res is None:
            continue
        a_, b_ = _peval(fa, res["t"]), _peval(fb, res["t"])    # courbes libres au même instant
        if (math.hypot(res["x"] - a_[0], res["y"] - a_[2]) > tau
                or math.hypot(res["x"] - b_[0], res["y"] - b_[2]) > tau):
            continue                                    # s'éloigne des détections : rejeté
        if res["rms"] > max(1.5 * sep_rms, 0.75 * tau):
            continue
        res["bounce"] = bounce
        return res
    return None


def _physical_smooth(kf, t):
    """{frame: état 6D} par paraboles entre impacts, ou None si la piste ne s'y
    prête pas (voir _phys_segments)."""
    segs = _phys_segments(kf, t)
    if segs is None:
        return None
    n = len(t["fr"])
    k = kf.k
    X = np.asarray(t["xs"], float)
    Y = np.asarray(t["ys"], float)
    # Impacts sous-frame : ajustement conjoint des deux paraboles voisines (le
    # sommet exact, hors grille de frames, que le tracé doit traverser).
    joints = [None] * max(len(segs) - 1, 0)
    if IMPACT_JOINT:
        anchor = None
        for j in range(len(segs) - 1):
            A, B = segs[j], segs[j + 1]
            if A[1] is None or B[1] is None:
                anchor = None
                continue
            res = _refine_impact(A, B, X, Y, k, anchor)
            joints[j] = res
            anchor = (res["t"], res["x"], res["y"]) if res else None
        for j, res in enumerate(joints):
            if res is None:
                continue
            ii, fit, mk = segs[j]
            segs[j] = (ii, res["fa"], mk)                   # côté gauche : sa fin = l'impact
            if j + 1 < len(segs) and (j + 1 >= len(joints) or joints[j + 1] is None):
                ii2, _, mk2 = segs[j + 1]
                segs[j + 1] = (ii2, res["fb"], mk2)         # dernier segment : côté droit
    # Chaque parabole couvre ses propres détections ; le trou entre deux
    # paraboles voisines est raccordé par la même physique qu'un trou entre
    # deux pistes (_link_gap) : jamais de fondu ni de coude inventé.
    bounds = [0]
    for j, ((ia, fa, _), (ib, fb, _)) in enumerate(zip(segs, segs[1:])):
        if joints[j]:
            bounds.append(int(math.floor(joints[j]["t"])) + 1)
        else:
            bounds.append((int(ia[-1]) + int(ib[0])) // 2 + 1)
    bounds.append(n)
    out = SmoothMap()
    holes = []
    for j, ((ii, fit, _), lo, hi) in enumerate(zip(segs, bounds, bounds[1:])):
        if fit is not None:
            jl = j > 0 and joints[j - 1] is not None
            jr = j < len(joints) and joints[j] is not None
            lo2 = lo if jl else (int(ii[0]) if j > 0 and segs[j - 1][1] is not None else lo)
            hi2 = hi if jr else (int(ii[-1]) + 1 if j < len(segs) - 1 and segs[j + 1][1] is not None else hi)
            for i in range(lo2, hi2):
                x, vx, y, vy, ay = _peval(fit, i)
                out[t["fr"][i]] = np.array([x, vx, 0.0, y, vy, ay], float)
            if hi2 < hi and not jr:
                holes.append((j, hi2, int(segs[j + 1][0][0])))
        elif hi - lo >= 3:
            # un peu de contexte de chaque côté : sans lui le filtre démarre à
            # l'aveugle (vitesse nulle) et le tronçon décroche à son 1er point
            ctx = RTS_CTX if hi - lo <= RTS_CTX_MAXLEN else 0     # tronçons courts seulement
            lo_c, hi_c = max(0, lo - ctx), min(n, hi + ctx)
            a_, b_, c_, d_, _ = _forward(kf, t, lo_c, hi_c)
            sm_c = _rts(kf, a_, b_, c_, d_)
            for i in range(lo, hi):
                out[t["fr"][i]] = sm_c[i - lo_c]
        else:
            for i in range(lo, hi):
                out[t["fr"][i]] = np.array([X[i], 0, 0, Y[i], 0, 0], float)
    # Deux paraboles voisines sans impact franc (coude < KINK_DEG, pas de
    # sommet conjoint) ne se rejoignent jamais exactement : la bascule sèche à
    # mi-chemin laissait un décrochement de plusieurs px (un « S » dans l'arche).
    # On les fond l'une dans l'autre sur ±BLEND_HALF frames (smoothstep) ; si
    # elles divergent de plus de BLEND_MAX_D px on garde la bascule (vrai coup).
    for j in range(len(segs) - 1):
        if joints[j] is not None:
            continue
        (ia, fa, _), (ib, fb, _) = segs[j], segs[j + 1]
        if fa is None or fb is None:
            continue
        ea, sb0 = int(ia[-1]), int(ib[0])
        if sb0 - ea > 2:
            continue
        pa, pb = _peval(fa, 0.5 * (ea + sb0)), _peval(fb, 0.5 * (ea + sb0))
        if math.hypot(pa[0] - pb[0], pa[2] - pb[2]) > BLEND_MAX_D * k:
            continue
        if math.hypot(pb[1] - pa[1], pb[3] - pa[3]) >= PHYS_RACKET_DV * k:
            continue                                     # rebond / raquette : le coin reste net
        lo_b, hi_b = ea - BLEND_HALF + 1, sb0 + BLEND_HALF
        span = float(hi_b - lo_b)
        for i in range(max(lo_b, 0), min(hi_b, n)):
            fr_i = t["fr"][i]
            if fr_i not in out:
                continue
            u = min(max((i - lo_b + 0.5) / span, 0.0), 1.0)
            w = u * u * (3.0 - 2.0 * u)
            A_, B_ = _peval(fa, i), _peval(fb, i)
            out[fr_i] = np.array([(1 - w) * A_[0] + w * B_[0], (1 - w) * A_[1] + w * B_[1], 0.0,
                                  (1 - w) * A_[2] + w * B_[2], (1 - w) * A_[3] + w * B_[3],
                                  (1 - w) * A_[4] + w * B_[4]], float)
    # Pas d'extrapolation à l'aveugle aux deux bouts : un tronçon rebut (ou une
    # détection aberrante) en tête de piste laissait la 1re parabole se prolonger
    # en arrière sur des dizaines de frames, une « origine » inventée.
    if segs[0][1] is not None:
        inl = segs[0][0][segs[0][2]]
        if len(inl):
            for i in range(0, int(inl[0]) - PHYS_EDGE_EXT):
                out.pop(t["fr"][i], None)
    if segs[-1][1] is not None:
        inl = segs[-1][0][segs[-1][2]]
        if len(inl):
            for i in range(int(inl[-1]) + PHYS_EDGE_EXT + 1, n):
                out.pop(t["fr"][i], None)
    fr_idx = np.arange(n, dtype=float)
    out.apex = tuple(
        (float(np.interp(r["t"], fr_idx, t["fr"])), float(r["x"]), float(r["y"]), r["mode"])
        for r in joints if r)
    for j, ia, ib in holes:                               # trous entre deux paraboles
        fa, fb = segs[j][1], segs[j + 1][1]
        ea, eb = _peval(fa, ia - 1), _peval(fb, ib)
        sa_ = np.array([ea[0], ea[1], 0.0, ea[2], ea[3], ea[4]])
        sb_ = np.array([eb[0], eb[1], 0.0, eb[2], eb[3], eb[4]])
        g_loc = min(max(0.5 * (ea[4] + eb[4]), 0.2 * k), 1.2 * k)
        nn = ib - (ia - 1)
        fill = _link_gap(sa_, sb_, nn, g_loc, k, True)
        if fill is None:                                   # pas de physique : on gomme le trou
            continue                                       # (jamais de ligne droite ; build_points coupe)
        for u, (x, y) in enumerate(fill, start=1):
            out[t["fr"][ia - 1 + u]] = np.array(
                [x, (sb_[0] - sa_[0]) / nn, 0.0, y, (sb_[3] - sa_[3]) / nn, g_loc], float)
    return out


def rts_smooth(kf, t):
    """Lissage aller-retour par segment sans rebond.

    On dessine le modèle, pas la mesure brute : c'est ce qui supprime le
    tremblement. Les segments ne franchissent jamais un rebond, sinon le
    lisseur arrondit le coin et fait déborder la traînée sous la table.
    Renvoie {frame: état 6D lissé}.
    """
    n = len(t["fr"])
    if n < 3:
        return {t["fr"][i]: np.array([t["xs"][i], 0, 0, t["ys"][i], 0, 0], float)
                for i in range(n)}
    phys = _physical_smooth(kf, t)
    if phys is not None:
        return phys
    sp, Pp, sf, Pf, nres = _forward(kf, t, 0, n)
    filt = [(s[0], s[3]) for s in sf]
    splits = set(_bounce_indices(filt, kf.k)) | set(_kink_indices(filt, kf.k))
    splits = {i for i in splits if t["seen"][i]}   # jamais sur une position extrapolée
    for i in range(1, n):                           # garde-fou : résidu aberrant
        if nres[i] > 4.0 and nres[i - 1] > 4.0:
            splits.add(i - 1)
    cuts = [0] + sorted(i for i in splits if 0 < i < n - 1) + [n - 1]
    out, acc = {}, {}
    for lo, hi in zip(cuts, cuts[1:]):
        hi = hi + 1                                 # le rebond appartient aux deux
        if hi - lo < 3:
            sm = [np.array([t["xs"][i], 0, 0, t["ys"][i], 0, 0], float)
                  for i in range(lo, hi)]
        else:
            a, b, c, d, _ = _forward(kf, t, lo, hi)
            sm = _rts(kf, a, b, c, d)
        for i, s in zip(range(lo, hi), sm):
            acc.setdefault(i, []).append(s)
    for i, lst in acc.items():
        out[t["fr"][i]] = np.mean(lst, axis=0) if len(lst) > 1 else lst[0]
    return out


def estimate_gravity(smoothed_list, k, fps):
    """Accélération verticale apparente (px/frame²), médiane sur les états lissés
    des segments en vol. Repli : ordre de grandeur d'une table filmée de loin."""
    ays = []
    for sm in smoothed_list:
        for s in sm.values():
            ays.append(s[5])
    fallback = 0.8 * k * (60.0 / fps) ** 2
    if len(ays) < 20:
        return fallback
    g = float(np.median(ays))
    return float(min(max(g, 0.0), 3.0 * k)) if g > 0.05 * fallback else fallback


def _link_gap(sa, sb, n, g, k, cont=False):
    """Raccord physique entre l'état lissé sa (frame 0) et sb (frame n).

    Renvoie les n−1 positions intermédiaires, ou None si aucune parabole ni
    impact plausible ne relie les deux bords (la traînée est alors coupée).
      - sans impact : x en Hermite (vitesses des bords), y parabole de gravité g
        passant par les deux bouts ;
      - avec impact (raquette, rebond) : on prolonge chaque bord en balistique
        jusqu'à l'instant où les deux courbes se rejoignent (u*), jamais de coude
        ailleurs ; si elles ne se rejoignent pas à LINK_TOL près, rejet.
    """
    vxa, vya, vxb, vyb = sa[1], sa[4], sb[1], sb[4]
    tol = LINK_TOL * k * (1.0 + n / 12.0) * (1.5 if cont else 1.0)
    if 2 <= n <= LINK_SHORT_GAP:
        tol *= LINK_SHORT_MUL
    direct = math.hypot(sb[0] - sa[0], sb[3] - sa[3])
    v_edge = max(math.hypot(vxa, vya), math.hypot(vxb, vyb))
    v_max = max(LINK_SPEED * k, LINK_SPEED_MUL * v_edge)
    if direct > v_max * n + tol:
        return None                                  # plus vite que la balle ne va
    if n < 2:
        return []

    def A(u):
        return sa[0] + vxa * u, sa[3] + vya * u + 0.5 * g * u * u

    def B(u):
        m = n - u
        return sb[0] - vxb * m, sb[3] - vyb * m + 0.5 * g * m * m

    E = [math.hypot(A(u)[0] - B(u)[0], A(u)[1] - B(u)[1]) for u in range(n + 1)]
    dv = math.hypot(vxb - vxa, vyb - vya)
    cos_ok = True
    na, nb = math.hypot(vxa, vya), math.hypot(vxb, vyb)
    if na > 1e-6 and nb > 1e-6:
        cos_ok = (vxa * vxb + vya * vyb) / (na * nb) >= math.cos(math.radians(KINK_DEG))
    out = []
    if dv < PHYS_RACKET_DV * k and cos_ok:           # trajectoire continue
        if E[n] > tol or E[0] > tol:
            return None
        dx, dy = sb[0] - sa[0], sb[3] - sa[3]
        lin = dx / n
        herm = (abs(vxa - lin) <= 0.6 * max(abs(lin), 3.0 * k)
                and abs(vxb - lin) <= 0.6 * max(abs(lin), 3.0 * k))
        for u in range(1, n):
            r = u / float(n)
            if herm:
                x = ((2 * r**3 - 3 * r**2 + 1) * sa[0] + (r**3 - 2 * r**2 + r) * n * vxa
                     + (-2 * r**3 + 3 * r**2) * sb[0] + (r**3 - r**2) * n * vxb)
            else:
                x = sa[0] + dx * r
            y = sa[3] + (dy - 0.5 * g * n * n) * r + 0.5 * g * u * u
            out.append((x, y))
        return out
    if not _impact_ok(vxa, vya, vxb, vyb, k):
        return None                                  # coude sans cause physique
    us = min(range(1, n), key=lambda u: E[u])
    if E[us] > tol:
        return None                                  # les deux côtés ne se rejoignent pas
    ax_, ay_ = A(us)
    bx_, by_ = B(us)
    mx, my = 0.5 * (ax_ + bx_), 0.5 * (ay_ + by_)
    for u in range(1, n):
        if u <= us:
            x, y = A(u)
            w = u / float(us)
            out.append((x + (mx - ax_) * w, y + (my - ay_) * w))
        else:
            x, y = B(u)
            w = (n - u) / float(n - us)
            out.append((x + (mx - bx_) * w, y + (my - by_) * w))
    return out


_SEQ_KEYS = ("fr", "xs", "ys", "seen", "npx", "ln", "ang", "src", "cf")


def _drop_loose_ends(t):
    """Retire aux deux bouts une ou deux détections isolées derrière un trou.

    Au coup de raquette le filtre continue quelques frames à l'aveugle puis
    ramasse souvent une fausse détection (main, raquette) : ce point final est
    loin de la vraie balle et fausse le raccord avec la piste suivante.
    """
    seen = t["seen"]
    idx = [i for i, sn in enumerate(seen) if sn]
    lo, hi = 0, len(idx)
    for _ in range(2):
        tail = idx[lo:hi]
        if len(tail) <= 2 * MIN_PIECE:
            break
        cut = None
        for j in range(len(tail) - 1, len(tail) - MIN_PIECE, -1):
            if tail[j] - tail[j - 1] - 1 >= LOOSE_GAP:
                cut = j
                break
        if cut is None:
            break
        hi = lo + cut
    for _ in range(2):
        head = idx[lo:hi]
        if len(head) <= 2 * MIN_PIECE:
            break
        cut = None
        for j in range(1, MIN_PIECE):
            if head[j] - head[j - 1] - 1 >= LOOSE_GAP:
                cut = j
        if cut is None:
            break
        lo += cut
    if lo == 0 and hi == len(idx):
        return t
    a, b = idx[lo], idx[hi - 1] + 1
    u = dict(t)
    for key in _SEQ_KEYS:
        u[key] = t[key][a:b]
    u["f0"], u["f1"] = u["fr"][0], u["fr"][-1]
    return u


def _split_coast(t):
    """Coupe une piste à chaque run d'au moins COAST_SPLIT frames sans détection.

    Le filtre y a extrapolé à l'aveugle (un rebond dans le trou l'envoie sous
    la table) ; on préfère recoudre les deux bords en balistique. Les morceaux
    suivants portent cont=True : ils viennent de la même hypothèse, on les
    relie sans condition.
    """
    t = _drop_loose_ends(t)
    seen = t["seen"]
    runs, i, n = [], 0, len(seen)
    while i < n:
        if not seen[i]:
            j = i
            while j < n and not seen[j]:
                j += 1
            if j - i >= COAST_SPLIT:
                runs.append((i, j))
            i = j
        else:
            i += 1
    if not runs:
        return [t]
    out, lo = [], 0
    for a, b in runs + [(n, n)]:
        if sum(1 for x in seen[lo:a] if x) >= MIN_PIECE:
            u = dict(t)
            for key in _SEQ_KEYS:
                u[key] = t[key][lo:a]
            u["f0"], u["f1"] = u["fr"][0], u["fr"][-1]
            u["cont"] = bool(out)
            out.append(u)
        lo = b
    return out or [t]


def _crop_to_smooth(t, sm):
    """Retire de la piste les frames que le lisseur a refusé d'extrapoler (bouts
    sans support) ; None s'il reste moins de MIN_PIECE détections vues."""
    idx = [i for i, f in enumerate(t["fr"]) if f in sm]
    if len(idx) == len(t["fr"]):
        return t
    if not idx:
        return None
    a, b = idx[0], idx[-1] + 1
    u = dict(t)
    for key in _SEQ_KEYS:
        u[key] = t[key][a:b]
    if sum(1 for x in u["seen"] if x) < MIN_PIECE:
        return None
    u["f0"], u["f1"] = u["fr"][0], u["fr"][-1]
    return u


def build_points(chosen, cuts, kf, fps):
    """Assemble les pistes en échanges continus, trous compris.

    {frame: (x, y, rallye, confiance, longueur de flou, angle)}
    """
    cutset = set(cuts)
    k = kf.k
    chosen = [u for t in chosen for u in _split_coast(t)]
    smooth = [rts_smooth(kf, t) for t in chosen]
    kept, kept_sm = [], []
    for t, sm in zip(chosen, smooth):
        t = _crop_to_smooth(t, sm)
        if t is not None:
            kept.append(t)
            kept_sm.append(sm)
    chosen, smooth = kept, kept_sm
    g = estimate_gravity(smooth, k, fps)
    if chosen:
        zcx, zcy, zrx, zry = play_zone(chosen, int(round(1920 * k)), int(round(1080 * k)))
    else:
        zcx = zcy = 0.0
        zrx = zry = 1e9

    def far(p):
        return math.hypot((p[0] - zcx) / zrx, (p[1] - zcy) / zry) > ORIGIN_ND
    points, rally = {}, 0
    prev = None
    for t, sm in zip(chosen, smooth):
        linked = False
        if prev is not None:
            pt, psm = prev
            gap = t["f0"] - pt["f1"]
            crosses_cut = any(f in cutset for f in range(pt["f1"] + 1, t["f0"] + 1))
            max_gap = MAX_LINK_GAP * (2 if t.get("cont") else 1)
            # pas de raccord vers une tête hors zone de jeu : fausse piste, on coupe
            head_far = (not t.get("cont")) and far(sm[t["f0"]][[0, 3]])
            if 1 <= gap <= max_gap and not crosses_cut and not head_far:
                sa, sb = psm[pt["f1"]], sm[t["f0"]]
                fill = _link_gap(sa, sb, gap, g, k, bool(t.get("cont")))
                if fill is not None:
                    linked = True
                    ln, ang = pt["ln"][-1], pt["ang"][-1]
                    for u, (x, y) in enumerate(fill, start=1):
                        points[pt["f1"] + u] = (float(x), float(y), rally, FILL_CONF,
                                                ln, ang)
        if not linked:
            rally += 1
        apex = {}
        for tf, ax, ay, _m in getattr(sm, "apex", ()):
            fl = int(math.floor(tf))
            apex[fl] = (float(tf - fl), float(ax), float(ay))   # impact entre fl et fl+1
        last_f = None
        for i, f in enumerate(t["fr"]):
            if f not in sm:                      # trou gommé (aucune physique crédible)
                continue
            if last_f is not None and f != last_f + 1:
                rally += 1                       # la traînée s'interrompt : pas de pont
            last_f = f
            s = sm[f]
            points[f] = (float(s[0]), float(s[3]), rally, float(t["cf"][i]),
                         float(t["ln"][i]), float(t["ang"][i])) + apex.get(f, ())
        prev = (t, sm)
    # Origine justifiée : un échange naît près des joueurs (zone de jeu élargie),
    # ou sort d'une zone qu'il a quittée. Une tête hors zone (réflexion, objet,
    # raccord abusif) est coupée jusqu'au point où la piste entre dans la zone.
    if chosen:
        heads = {}
        for f in sorted(points):
            heads.setdefault(points[f][2], []).append(f)
        for r, fr in heads.items():
            for f in fr:
                if far(points[f]):
                    del points[f]
                else:
                    break
    # queue d'échange : après la fin du point la balle roule au sol, part sur le
    # côté ou rejoint une autre table ; on arrête la traînée à la sortie durable
    # de la zone (sauf coup très rapide, smash qui file hors du cadre).
    if chosen and TAIL_CUT:
        def out_tail(p):
            return ((p[1] - zcy) / zry > TAIL_ND
                    or abs(p[0] - zcx) / zrx > TAIL_ND_SIDE)
        by_r = {}
        for f, p in points.items():
            by_r.setdefault(p[2], []).append(f)
        for r, fr in by_r.items():
            fr.sort()
            j = len(fr)
            while j > 0 and out_tail(points[fr[j - 1]]):
                j -= 1
            if j == len(fr) or len(fr) - j < TAIL_MIN_OUT or j == 0:
                continue
            a, b = points[fr[j]], points[fr[min(j + 1, len(fr) - 1)]]
            dxv, dyv = (b[0] - a[0]) / k * 60.0 / fps, (b[1] - a[1]) / k * 60.0 / fps
            v = math.hypot(dxv, dyv)
            if v >= FAST_ZONE_SPEED and dyv <= 0.5 * v:   # smash qui file (pas une chute au sol)
                continue
            for f in fr[j:]:
                del points[f]
    # rallyes trop courts : un flash de quelques frames n'est pas un échange
    min_len = int(round(MIN_RALLY_SEC * fps))
    by_rally = {}
    for f, p in points.items():
        by_rally.setdefault(p[2], []).append(f)
    out = {}
    for r, fr in by_rally.items():
        fr.sort()
        if fr[-1] - fr[0] + 1 < min_len:
            # …sauf un coup rapide isolé : 15 frames suffisent à traverser la table
            xs_r = [points[f][0] for f in fr]; ys_r = [points[f][1] for f in fr]
            if (len(fr) < 2 * RAMP_FRAMES + 2
                    or math.hypot(max(xs_r) - min(xs_r), max(ys_r) - min(ys_r))
                    < MIN_RALLY_PX * k):
                continue
        for i, f in enumerate(fr):
            x, y, _, cf, ln, ang = points[f][:6]
            ramp = min(1.0, (i + 1) / float(RAMP_FRAMES),
                       (len(fr) - i) / float(RAMP_FRAMES))
            out[f] = (x, y, r, cf * ramp, ln, ang) + tuple(points[f][6:])
    return out


# =============================================================================
# 6. Passe 2 : détection recadrée sur les trous
# =============================================================================

def _aspect_box(x0, y0, x1, y1, W, H, max_zoom):
    """Boîte 16:9 englobant (x0,y0,x1,y1), zoom ≤ max_zoom, dans l'image."""
    bw, bh = x1 - x0, y1 - y0
    bw = max(bw, bh * W / H, W / max_zoom)
    bh = bw * H / W
    if bw > W:
        bw, bh = W, H
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    nx0 = min(max(cx - bw / 2, 0), W - bw)
    ny0 = min(max(cy - bh / 2, 0), H - bh)
    return [int(round(nx0)), int(round(ny0)),
            int(round(nx0 + bw)), int(round(ny0 + bh))]


def plan_pass2(points, chosen, cuts, W, H, total, allowed=None):
    """Frames à re-scruter et fenêtre de recadrage par frame.

    `allowed` : plages [a, b) où la passe 2 reste à faire (None = partout).
    """
    ok = None
    if allowed is not None:
        ok = np.zeros(total, bool)
        for a, b in allowed:
            ok[max(0, a):min(total, b)] = True
    by_shot = {}
    for t in chosen:
        by_shot.setdefault(shot_of(t["f0"], cuts), []).append(t)
    rois = {}
    for shot, lst in by_shot.items():
        xs = np.concatenate([np.asarray(t["xs"], float) for t in lst])
        ys = np.concatenate([np.asarray(t["ys"], float) for t in lst])
        m = DET2_MARGIN * W
        box = _aspect_box(xs.min() - m, ys.min() - m, xs.max() + m, ys.max() + m,
                          W, H, DET2_MAX_ZOOM)
        if box[2] - box[0] >= W - 2:      # pas de zoom possible : rien à gagner
            continue
        frames = set()
        lst = sorted(lst, key=lambda t: t["f0"])
        lo = cuts[shot - 1] if shot > 0 else 0
        hi = cuts[shot] if shot < len(cuts) else total
        for a, b in zip(lst, lst[1:]):
            gap = b["f0"] - a["f1"]
            if 1 < gap <= DET2_MAX_GAP:
                frames.update(range(a["f1"] + 1, b["f0"]))
        for t in lst:
            frames.update(range(max(lo, t["f0"] - DET2_PAD), t["f0"]))
            frames.update(range(t["f1"] + 1, min(hi, t["f1"] + DET2_PAD + 1)))
            frames.update(f for f, sn in zip(t["fr"], t["seen"]) if not sn)
        for f in frames:
            if 0 <= f < total and (f not in points or points[f][3] < 1.0):
                if ok is None or ok[f]:
                    rois[f] = box
    budget = int(DET2_MAX_FRAC * (total if ok is None else int(ok.sum())))
    if len(rois) > budget:
        keep = sorted(rois)[:budget]
        rois = {f: rois[f] for f in keep}
    return rois


def run_pass2(args, rois, blob, done_ranges):
    here = os.path.dirname(os.path.abspath(__file__))
    tmp_rois = args.dets + ".rois.json"
    tmp_out = args.dets + ".p2.json"
    tmp_pts = args.dets + ".pts.json"
    json.dump({str(f): r for f, r in rois.items()}, open(tmp_rois, "w"))
    cmd = [sys.executable, os.path.join(here, "blurball_infer.py"),
           "--input", args.input, "--weights", args.weights,
           "--config", args.config, "--out", tmp_out,
           "--start", str(args.start), "--thr", str(DET2_THR),
           "--rois-file", tmp_rois, "--phase", "det2"]
    if args.duration:
        cmd += ["--duration", str(args.duration)]
    elif not args.start:
        # Indices globaux : on saute d'un groupe de frames au suivant (seek)
        # au lieu de décoder le fichier jusqu'à la dernière.
        json.dump(probe_pts(args.input), open(tmp_pts, "w"))
        cmd += ["--pts", tmp_pts]
    env = dict(os.environ, BLURBALL_SRC=args.bb_src)
    subprocess.run(cmd, check=True, env=env)
    p2 = json.load(open(tmp_out))
    blob.setdefault("dets2", {}).update(p2["dets"])
    blob["ranges2"] = done_ranges
    blob["version"] = DETS_VERSION
    json.dump(blob, open(args.dets, "w"))
    for p in (tmp_rois, tmp_out, tmp_pts):
        try:
            os.remove(p)
        except OSError:
            pass


# =============================================================================
# 7. Rendu
# =============================================================================

def catmull_rom(pts, steps=SPLINE_STEPS, alpha=0.5):
    """Catmull-Rom centripète (alpha=0.5) : jamais de boucle ni de rebroussement,
    contrairement à la version uniforme qui dépasse dans les virages serrés.

    `steps` : sous-pas par segment (entier, ou liste d'un entier par segment :
    un segment plus court qu'une frame, comme celui qui touche un impact
    sous-frame, reçoit moins de pas pour garder un échantillonnage uniforme en
    temps). Bouts prolongés par symétrie : la tangente d'extrémité suit la corde,
    elle ne s'incurve pas (coin net au rebond)."""
    nseg = len(pts) - 1
    st = list(steps) if hasattr(steps, "__len__") else [steps] * max(nseg, 0)
    if len(pts) < 3:
        if len(pts) == 2:
            return [(pts[0][0] + (pts[1][0] - pts[0][0]) * k / st[0],
                     pts[0][1] + (pts[1][1] - pts[0][1]) * k / st[0])
                    for k in range(st[0])] + [pts[-1]]
        return list(pts)
    p_a = (2 * pts[0][0] - pts[1][0], 2 * pts[0][1] - pts[1][1])
    p_z = (2 * pts[-1][0] - pts[-2][0], 2 * pts[-1][1] - pts[-2][1])
    ext = [p_a] + list(pts) + [p_z]
    out = []
    for i in range(len(ext) - 3):
        p0, p1, p2, p3 = ext[i:i + 4]
        n_i = st[i]

        def nxt(t, a, b):
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            return t + max(d, 1e-6) ** alpha

        t0 = 0.0
        t1 = nxt(t0, p0, p1)
        t2 = nxt(t1, p1, p2)
        t3 = nxt(t2, p2, p3)
        for k in range(n_i):
            t = t1 + (t2 - t1) * k / float(n_i)

            def mix(u, v, ta, tb):
                w = (tb - t) / (tb - ta)
                return (w * u[0] + (1 - w) * v[0], w * u[1] + (1 - w) * v[1])
            a1 = mix(p0, p1, t0, t1)
            a2 = mix(p1, p2, t1, t2)
            a3 = mix(p2, p3, t2, t3)
            b1 = mix(a1, a2, t0, t2)
            b2 = mix(a2, a3, t1, t3)
            out.append(mix(b1, b2, t1, t2))
    out.append(pts[-1])
    return out


def _dense_curve(pts, vals, corners=(), times=None):
    """Courbe densifiée + valeur interpolée par sommet. Coupée (coin net) aux
    `corners` (impacts sous-frame, indices de sommets) et aux rebonds détectés
    par _bounce_indices loin d'un coin connu. `times` : instant de chaque sommet
    (frames) pour un échantillonnage uniforme en temps."""
    corners = sorted(set(int(c) for c in corners if 0 < c < len(pts) - 1))
    legacy = [b for b in _bounce_indices(pts) if all(abs(b - c) > 3 for c in corners)]
    cuts = sorted(set([0, len(pts) - 1] + corners + legacy))
    dense, dv = [], []
    for start, end in zip(cuts, cuts[1:]):
        if end <= start:
            continue
        seg = pts[start:end + 1]
        if times is None:
            st = [SPLINE_STEPS] * (len(seg) - 1)
        else:
            st = [max(2, int(round(SPLINE_STEPS * (times[start + i + 1] - times[start + i]))))
                  for i in range(len(seg) - 1)]
        curve = catmull_rom(seg, st)
        vv = []
        for i, n_i in enumerate(st):
            v0, v1 = vals[start + i], vals[start + i + 1]
            vv.extend(v0 + (v1 - v0) * (j / float(n_i)) for j in range(n_i))
        vv.append(vals[end])
        if dense:
            curve, vv = curve[1:], vv[1:]
        dense.extend(curve)
        dv.extend(vv)
    return dense, dv


class Comet:
    """Rendu « comète » : cœur fin net, halo additif ; la vraie balle sert de tête."""

    def __init__(self, W, H, fps, hdr=False):
        self.W, self.H, self.fps = W, H, fps
        self.core_w = CORE_W_FRAC * W
        self.glow_w = GLOW_W_MUL * self.core_w
        self.glow_sig = GLOW_SIG_FRAC * W
        self.n_back = int(round(TRAIL_SEC * fps))
        self.glow_k = GLOW_K * (0.75 if hdr else 1.0)
        self.margin = int(3 * self.glow_sig + self.glow_w / 2 + self.core_w) + 2

    def samples(self, points, f):
        seq = [(k, points[k]) for k in range(f - self.n_back, f + 1) if k in points]
        if not seq:
            return []
        rally = seq[-1][1][2]
        return [(k, p) for k, p in seq if p[2] == rally]

    def draw(self, frame, points, f):
        seq = self.samples(points, f)
        if len(seq) < 2:
            return frame
        # Sommets (instant, x, y, confiance) ; l'impact sous-frame entre deux frames
        # consécutives devient un sommet de plus, et un coin net de la courbe.
        verts, corners = [], []
        for n_, (kf_, p) in enumerate(seq):
            verts.append((float(kf_), p[0], p[1], p[3]))
            if len(p) > 8:
                fr_, ax, ay = p[6], p[7], p[8]
                nxt_ok = n_ + 1 < len(seq) and seq[n_ + 1][0] == kf_ + 1
                if fr_ < 0.03:
                    corners.append(len(verts) - 1)
                elif nxt_ok and fr_ > 0.97:
                    corners.append(len(verts))
                elif nxt_ok:
                    verts.append((kf_ + fr_, ax, ay,
                                  p[3] + (seq[n_ + 1][1][3] - p[3]) * fr_))
                    corners.append(len(verts) - 1)
        pts = [(v[1], v[2]) for v in verts]
        times = [v[0] for v in verts]
        ages = [(f - v[0]) / self.fps for v in verts]
        alpha = [math.exp(-a / TAU_SEC) * v[3] for a, v in zip(ages, verts)]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        x0 = max(0, int(min(xs)) - self.margin); x1 = min(self.W, int(max(xs)) + self.margin + 1)
        y0 = max(0, int(min(ys)) - self.margin); y1 = min(self.H, int(max(ys)) + self.margin + 1)
        rw, rh = x1 - x0, y1 - y0
        if rw < 4 or rh < 4:
            return frame
        loc = [(x - x0, y - y0) for x, y in pts]
        dense, da = _dense_curve(loc, alpha, corners, times)
        if len(dense) < 2:
            return frame

        # cœur, supersamplé ×2 dans la ROI
        ss = 2
        core = np.zeros((rh * ss, rw * ss), np.float32)
        shift, sc = 4, 16 * ss
        for i in range(len(dense) - 1):
            a = 0.5 * (da[i] + da[i + 1])
            if a < 0.01:
                continue
            w = max(1, int(round(self.core_w * ss * (0.25 + 0.75 * a))))
            p1 = (int(round(dense[i][0] * sc)), int(round(dense[i][1] * sc)))
            p2 = (int(round(dense[i + 1][0] * sc)), int(round(dense[i + 1][1] * sc)))
            cv2.line(core, p1, p2, float(a), w, cv2.LINE_AA, shift)
        core = cv2.resize(core, (rw, rh), interpolation=cv2.INTER_AREA)

        # halo, au quart de résolution puis flouté
        q = 4
        gw, gh = max(2, rw // q + 1), max(2, rh // q + 1)
        glow = np.zeros((gh, gw), np.float32)
        scq = 16.0 / q
        for i in range(len(dense) - 1):
            a = 0.5 * (da[i] + da[i + 1])
            if a < 0.01:
                continue
            w = max(1, int(round(self.glow_w / q * (0.4 + 0.6 * a))))
            p1 = (int(round(dense[i][0] * scq)), int(round(dense[i][1] * scq)))
            p2 = (int(round(dense[i + 1][0] * scq)), int(round(dense[i + 1][1] * scq)))
            cv2.line(glow, p1, p2, float(a), w, cv2.LINE_AA, shift)
        glow = cv2.GaussianBlur(glow, (0, 0), max(0.5, self.glow_sig / q))
        glow = cv2.resize(glow, (rw, rh), interpolation=cv2.INTER_LINEAR)

        mono = core * CORE_K
        glow *= self.glow_k
        light = cv2.merge([mono * float(CORE_BGR[c]) + glow * float(GLOW_BGR[c])
                           for c in range(3)])
        light = cv2.convertScaleAbs(light)                 # clip 0..255 -> uint8
        roi = frame[y0:y1, x0:x1]
        if cv2.mean(roi)[:3] > (SCREEN_MEAN,) * 3:
            # « screen » : 1 - (1-a)(1-b), ne crame pas un fond blanc
            inv = cv2.multiply(cv2.bitwise_not(roi), cv2.bitwise_not(light),
                               scale=1.0 / 255.0)
            cv2.bitwise_not(inv, dst=roi)
        else:
            cv2.add(roi, light, dst=roi)                   # additif saturé
        return frame


# --- ancien look, gardé pour comparaison (--style classic) -------------------

def speed_factor(v):
    t = (v - SPEED_REF_LO) / (SPEED_REF_HI - SPEED_REF_LO)
    t = min(1.0, max(0.0, t))
    return WIDTH_AT_SLOW + t * (WIDTH_AT_FAST - WIDTH_AT_SLOW)


def stroke(layer, pts, width_head, taper_start):
    cuts = [0] + _bounce_indices(pts) + [len(pts) - 1]
    dense = []
    for start, end in zip(cuts, cuts[1:]):
        curve = catmull_rom(pts[start:end + 1])
        dense.extend(curve if not dense else curve[1:])
    if len(dense) < 2:
        return
    shift = 3
    scale = 1 << shift
    last_width = 1
    for i, (p1, p2) in enumerate(zip(dense, dense[1:])):
        t = (i + 1) / (len(dense) - 1)
        v = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) * SPLINE_STEPS
        taper = taper_start + (1.0 - taper_start) * t ** TAPER_POW
        last_width = max(1, round(width_head * taper * speed_factor(v)))
        q1 = (round(p1[0] * scale), round(p1[1] * scale))
        q2 = (round(p2[0] * scale), round(p2[1] * scale))
        cv2.line(layer, q1, q2, t ** 1.3, last_width, cv2.LINE_AA, shift)
    head = (round(dense[-1][0] * scale), round(dense[-1][1] * scale))
    cv2.circle(layer, head, max(scale, last_width * scale // 2), 1.0, -1,
               cv2.LINE_AA, shift)


def draw_trail_classic(frame, points, f, trail_len, alpha_max):
    seq = [(k, points[k]) for k in range(f - trail_len + 1, f + 1) if k in points]
    if len(seq) < 2:
        return frame
    segments, current = [], [seq[0]]
    for prev, cur in zip(seq, seq[1:]):
        if cur[0] - prev[0] == 1 and prev[1][2] == cur[1][2]:
            current.append(cur)
        else:
            segments.append(current)
            current = [cur]
    segments.append(current)
    segments = [s for s in segments if len(s) >= 2]
    if not segments:
        return frame
    h, w = frame.shape[:2]
    layer = np.zeros((h, w), np.float32)
    for seg in segments:
        pts = [(p[1][0], p[1][1]) for p in seg]
        stroke(layer, pts, TRAIL_WIDTH, 0.0 if seg[-1][0] == f else 0.35)
    layer = cv2.GaussianBlur(layer, (0, 0), 0.8)
    np.clip(layer, 0, 1, out=layer)
    a = (layer * alpha_max)[..., None]
    out = frame.astype(np.float32) * (1.0 - a) + TRAIL_COLOR * a
    return np.clip(out, 0, 255).astype(np.uint8)


def draw_debug(frame, cands, points, f):
    for c in cands:
        col = (0, 0, 255) if c[6] == 1 else (255, 0, 255)
        cv2.circle(frame, (int(c[0]), int(c[1])), 9, col, 1, cv2.LINE_AA)
    if f in points:
        x, y = points[f][0], points[f][1]
        cv2.circle(frame, (int(x), int(y)), 14, (0, 255, 0), 2, cv2.LINE_AA)
    cv2.putText(frame, f"frame {f}  candidats {len(cands)}", (30, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2, cv2.LINE_AA)
    return frame


# =============================================================================
# Pipeline
# =============================================================================

def open_at(path, start):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"Impossible d'ouvrir {path}")
    if start > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    return cap


def probe_video(path):
    """Cadence nominale et métadonnées couleur du flux (ffprobe)."""
    props = {"r_frame_rate": "", "pix_fmt": "", "color_transfer": "",
             "color_primaries": "", "color_space": ""}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate,pix_fmt,color_transfer,"
                              "color_primaries,color_space",
             "-of", "default=nw=1", path],
            capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if k.strip() in props and v not in ("", "unknown", "N/A"):
                    props[k.strip()] = v
    except Exception:
        pass
    return props


def container_fps(props, fallback):
    """Cadence exacte du conteneur (`r_frame_rate`), en fraction.

    OpenCV renvoie la cadence *moyenne* (59,933 au lieu de 60) ; la réinjecter
    donnerait une vidéo qui n'est plus du 60 fps franc. On passe la cadence
    nominale du flux telle quelle à ffmpeg.
    """
    out = props.get("r_frame_rate", "")
    try:
        num, den = (out.split("/") + ["1"])[:2]
        if float(num) > 0 and float(den) > 0:
            return out, float(num) / float(den)
    except Exception:
        pass
    return f"{fallback}", float(fallback)


def _frame_index(pts, sec):
    """Indice de la frame dont l'horodatage est le plus proche de `sec`."""
    j = bisect.bisect_left(pts, sec)
    if j >= len(pts):
        return len(pts) - 1
    if j > 0 and sec - pts[j - 1] < pts[j] - sec:
        return j - 1
    return j


def start_offset(args, cap, blob):
    """Décalage (frames) entre la 1re image rendue et l'indice 0 des détections.

    Trajectoire et détections sont indexées depuis `blob["start"]` (0 pour un
    cache de vidéo entière) ; le rendu, lui, démarre à `--start`. Sans ce
    décalage, `--start 10` dessinait la traînée de la seconde 0 sur l'image de
    la seconde 10 (≈ 600 images d'écart). Retourne (décalage, 1re image lue).
    """
    base_t = float(blob.get("start", 0.0)) if blob else 0.0
    ok, first = cap.read()
    if not ok or (args.start <= 0 and base_t <= 0) or abs(args.start - base_t) < 1e-6:
        return 0, (first if ok else None)
    pts = probe_pts(args.input)
    landed = _frame_index(pts, cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
    base = _frame_index(pts, base_t) if base_t > 0 else 0
    return landed - base, first


def render_pass(args, points, detections, fps, total, blob=None):
    cap = open_at(args.input, args.start)
    off, pending = start_offset(args, cap, blob)
    if off:
        print(f"  --start {args.start}s : trajectoire décalée de {off} images "
              f"(indices de détection = rendu + {off})", flush=True)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    props = probe_video(args.input)
    rate, rate_f = container_fps(props, fps)
    if abs(rate_f - fps) > 0.01:
        print(f"  cadence : {rate} ({rate_f:.3f} fps) d'après le conteneur, "
              f"OpenCV disait {fps:.3f} — on garde celle du conteneur", flush=True)
    hdr = props["color_transfer"] in ("arib-std-b67", "smpte2084")
    ten_bit = "10" in props["pix_fmt"]
    # Métadonnées couleur recopiées : sans elles, un fichier HLG sort « unknown »
    # et les lecteurs l'affichent comme du SDR délavé.
    # OpenCV décode en BGR avec la matrice BT.601 : on reconvertit avec la même
    # (aller-retour sans dérive de teinte), PUIS on pose les tags avec setparams.
    # Les options -color_trc/-color_primaries seules sont écrasées par ffmpeg ≥ 7
    # (trames du pipe « unspecified ») et -colorspace fait choisir une autre
    # matrice au convertisseur automatique.
    out_pix = "yuv420p10le" if ten_bit else "yuv420p"
    params = []
    if props["color_primaries"]:
        params.append(f"color_primaries={props['color_primaries']}")
    if props["color_transfer"]:
        params.append(f"color_trc={props['color_transfer']}")
    if props["color_space"]:
        params.append(f"colorspace={props['color_space']}")
    vf = f"scale=out_color_matrix=bt601:out_range=tv,format={out_pix}"
    if params:
        vf += ",setparams=" + ":".join(params)
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", rate, "-i", "pipe:0",
           "-ss", str(args.start)]
    if args.duration:
        cmd += ["-t", str(args.duration)]
    # Encodeur matériel Apple par défaut : x264 en 4K fait du CPU le goulot
    # (~3 images/s) et sa chauffe bride ensuite la détection.
    if args.encoder == "videotoolbox":
        venc = ["-c:v", "hevc_videotoolbox", "-q:v", "60", "-allow_sw", "1",
                "-profile:v", "main10" if ten_bit else "main", "-tag:v", "hvc1"]
    else:
        venc = ["-c:v", "libx265", "-preset", "medium", "-crf", "18", "-tag:v", "hvc1"]
    # Pas de -shortest : si l'audio finit quelques images avant la vidéo,
    # ffmpeg fermerait le pipe et les dernières images seraient perdues.
    cmd += ["-i", args.input, "-map", "0:v", "-map", "1:a?",
            "-vf", vf, *venc, "-pix_fmt", out_pix,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", args.output]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    comet = Comet(w, h, rate_f, hdr=hdr)
    f = 0
    n_out = min(total - off,
                int(round(args.duration * rate_f)) if args.duration else 1 << 30)
    while f < n_out:
        if pending is not None:
            ok, frame, pending = True, pending, None
        else:
            ok, frame = cap.read()
        if not ok:
            break
        fa = f + off            # indice dans les détections / la trajectoire
        if args.debug:
            frame = draw_debug(frame, detections.get(fa, []), points, fa)
        elif args.style == "classic":
            frame = draw_trail_classic(frame, points, fa, args.trail, args.opacity)
        else:
            frame = comet.draw(frame, points, fa)
        try:
            proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            print(f"  ffmpeg s'est arrêté à l'image {f}", flush=True)
            break
        f += 1
        if f % 60 == 0:
            print(f"PROGRESS render {f} {n_out}", flush=True)
        if f % 600 == 0:
            print(f"  rendu {f}/{n_out}", flush=True)
    cap.release()
    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass
    if proc.wait() != 0:
        sys.exit(f"ffmpeg a échoué (code {proc.returncode})")
    return f


# --- Plages analysées ----------------------------------------------------------
# Ping Pong Edit n'analyse que les parties gardées au montage (--ranges). Le
# cache de détections retient les plages de frames déjà passées au réseau
# ("ranges", indices globaux [a, b)) : remettre un passage coupé n'analyse que
# ce qui manque. Un cache sans "ranges" couvre toute la vidéo (ancien format).

def probe_pts(path):
    """Instant (s) de chaque frame dans l'ordre d'affichage, depuis le début du
    conteneur — même origine que la position de lecture d'OpenCV (POS_MSEC).
    Cadence variable des iPhone : « n° de frame / fps » ne tombe pas juste."""
    st = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time",
                         "-of", "csv=p=0", path], capture_output=True, text=True,
                        timeout=60).stdout.strip()
    try:
        t0 = float(st)
    except ValueError:
        t0 = 0.0
    pk = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
                        capture_output=True, text=True, timeout=300).stdout.split()
    times = sorted(float(x) for x in pk if x.strip() not in ("", "N/A"))
    return [round(t - t0, 6) for t in times]


def norm_ranges(rs):
    """Plages [a, b) triées, fusionnées quand elles se touchent."""
    out = []
    for a, b in sorted((int(a), int(b)) for a, b in rs if b > a):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def sub_ranges(want, have):
    """Ce qui, dans `want`, n'est pas couvert par `have`."""
    have = norm_ranges(have)
    out = []
    for a, b in norm_ranges(want):
        cur = a
        for c, d in have:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append([cur, c])
            cur = max(cur, d)
            if cur >= b:
                break
        if cur < b:
            out.append([cur, b])
    return out


def analyzed_ranges(blob):
    """Plages passées au réseau (passe 1) : tout le fichier pour un ancien cache."""
    if blob.get("ranges") is not None:
        return norm_ranges(blob["ranges"])
    return [[0, int(blob.get("frames", 0))]]


def pass2_ranges(blob):
    """Plages où la passe 2 (recadrée) est faite."""
    if blob.get("ranges2") is not None:
        return norm_ranges(blob["ranges2"])
    return analyzed_ranges(blob) if "dets2" in blob else []


def ranges_seconds(ranges, pts):
    """Plages de frames → plages de temps [t0, t1] (s) pour l'app."""
    out = []
    n = len(pts)
    for a, b in ranges:
        if a >= n:
            continue
        end = pts[b] if b < n else pts[-1] + (pts[-1] - pts[-2] if n > 1 else 1 / 60)
        out.append([round(pts[a], 4), round(end, 4)])
    return out


def parse_time_ranges(text, pts):
    """« t0-t1,t2-t3 » (s) → plages de frames [a, b) couvrant ces instants."""
    import bisect
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        t0, t1 = (float(x) for x in part.split("-"))
        a = bisect.bisect_left(pts, t0 - 1e-4)
        b = bisect.bisect_right(pts, t1 + 1e-4)
        if b > a:
            out.append([a, b])
    return norm_ranges(out)


def run_network(args):
    """Passe 1 (réseau) sur ce qui n'est pas déjà dans le cache de détections."""
    blob = None
    if os.path.exists(args.dets):
        try:
            blob = json.load(open(args.dets))
        except Exception:
            blob = None
    if blob is not None and not args.ranges and blob.get("ranges") is None:
        print("PROGRESS det 1 1", flush=True)   # cache : vidéo entière déjà faite
        return
    if blob is not None or args.ranges:
        return run_network_ranges(args, blob)
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "blurball_infer.py"),
           "--input", args.input, "--weights", args.weights,
           "--config", args.config, "--out", args.dets,
           "--start", str(args.start), "--thr", str(NET_THR)]
    if args.duration:
        cmd += ["--duration", str(args.duration)]
    env = dict(os.environ, BLURBALL_SRC=args.bb_src)
    print("Passe 1/4 : détection neuronale (BlurBall)…", flush=True)
    subprocess.run(cmd, check=True, env=env)


def run_network_ranges(args, blob):
    """Passe 1 limitée aux plages demandées (--ranges, sinon tout le fichier),
    moins celles déjà analysées ; le résultat est fusionné dans le cache."""
    pts = probe_pts(args.input)
    total = len(pts)
    want = parse_time_ranges(args.ranges, pts) if args.ranges else [[0, total]]
    have = analyzed_ranges(blob) if blob is not None else []
    todo = sub_ranges(want, have)
    if not todo:
        print("  plages demandées déjà analysées (cache)", flush=True)
        print("PROGRESS det 1 1", flush=True)
        return
    n = sum(b - a for a, b in todo)
    print(f"Passe 1/4 : détection neuronale (BlurBall) sur {n} frames "
          f"({100.0 * n / max(total, 1):.0f} % de la vidéo, {len(todo)} plages)…",
          flush=True)
    here = os.path.dirname(os.path.abspath(__file__))
    tmp_out = args.dets + ".part.json"
    tmp_pts = args.dets + ".pts.json"
    json.dump(pts, open(tmp_pts, "w"))
    cmd = [sys.executable, os.path.join(here, "blurball_infer.py"),
           "--input", args.input, "--weights", args.weights,
           "--config", args.config, "--out", tmp_out, "--thr", str(NET_THR),
           "--frames", ",".join(f"{a}-{b}" for a, b in todo), "--pts", tmp_pts]
    env = dict(os.environ, BLURBALL_SRC=args.bb_src)
    try:
        subprocess.run(cmd, check=True, env=env)
        new = json.load(open(tmp_out))
    finally:
        for p in (tmp_out, tmp_pts):
            try:
                os.remove(p)
            except OSError:
                pass
    if blob is None:
        blob = {"version": DETS_VERSION, "fps": new["fps"], "w": new["w"],
                "h": new["h"], "start": 0.0, "rois": False, "dets": {}}
        have = []
    diffs = list(blob.get("diffs") or [])
    diffs += [0.0] * max(0, total - len(diffs))
    for s0, ds in new.get("diffs_r", []):
        diffs[s0:s0 + len(ds)] = ds
    blob["diffs"] = diffs[:total]
    blob["dets"].update(new["dets"])
    blob["ranges"] = norm_ranges(have + new.get("ranges", []))
    blob["ranges_s"] = ranges_seconds(blob["ranges"], pts)
    blob.setdefault("ranges2", pass2_ranges(blob) if "dets2" in blob else [])
    blob["frames"] = total
    json.dump(blob, open(args.dets, "w"))


def compute_trajectory(args, detections, blob, fps, total, allow_pass2=True):
    width, height = blob.get("w", 1920), blob.get("h", 1080)
    kf = KF(width / 1920.0)
    diffs = blob.get("diffs")
    if not diffs:                    # ancien cache : on remesure nous-mêmes
        diffs = scan_diffs(args.input, args.start, args.duration, fps)
    # Plages analysées : coupes cherchées dans chacune, et chaque début de plage
    # compte comme un changement de plan (aucune piste à travers un trou).
    ranges = analyzed_ranges(blob)
    if blob.get("ranges") is None:
        cuts = shot_cuts = detect_cuts(diffs)
    else:
        shot_cuts = sorted({a + c for a, b in ranges for c in detect_cuts(diffs[a:b])})
        cuts = sorted(set(shot_cuts) | {a for a, _ in ranges if a > 0})
    print(f"  {len(cuts)} changements de plan détectés", flush=True)
    # Après la passe 2 on re-suit sans barre : elle reculerait sinon.
    prog = ((lambda i, n: print(f"PROGRESS track {i} {n}", flush=True))
            if allow_pass2 else None)
    tracklets = track(detections, 0, total, cuts, kf, verbose=allow_pass2,
                      progress=prog,
                      ranges=None if blob.get("ranges") is None else ranges)
    chosen = select_main_ball(tracklets, cuts, width, height, fps)
    points = build_points(chosen, cuts, kf, fps)
    print(f"  {len(tracklets)} pistes valides → {len(chosen)} retenues → "
          f"{len(points)} frames avec balle "
          f"({100.0 * len(points) / max(total, 1):.0f} %)", flush=True)
    todo2 = sub_ranges(ranges, pass2_ranges(blob))
    if allow_pass2 and not args.no_pass2 and todo2 and chosen:
        done2 = norm_ranges(pass2_ranges(blob) + todo2)
        # Fenêtres de recadrage par VRAI plan (coupes de montage de la vidéo),
        # pas par plage analysée : mêmes fenêtres qu'une analyse d'un bloc.
        rois = plan_pass2(points, chosen, shot_cuts, width, height, total,
                          None if blob.get("ranges") is None else todo2)
        if rois:
            print(f"Passe 2/4 : détection recadrée sur {len(rois)} frames "
                  f"({100.0 * len(rois) / max(total, 1):.0f} %)…", flush=True)
            run_pass2(args, rois, blob, done2)
            detections, _, _, blob = load_detections(args.dets)
            return compute_trajectory(args, detections, blob, fps, total, False)
        blob.setdefault("dets2", {})
        blob["ranges2"] = done2
        blob["version"] = DETS_VERSION
        json.dump(blob, open(args.dets, "w"))
    print("PROGRESS det2 1 1", flush=True)
    return points


HERE = os.path.dirname(os.path.abspath(__file__))
DEF_WEIGHTS = os.path.join(HERE, "blurball", "blurball_best")
DEF_CONFIG = os.path.join(HERE, "blurball", "src", "configs", "model", "blurball.yaml")
DEF_BB_SRC = os.path.join(HERE, "blurball", "src")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default=None,
                    help="vidéo de sortie (inutile avec --no-render)")
    ap.add_argument("--dets", required=True, help="cache .json des détections")
    ap.add_argument("--weights", default=DEF_WEIGHTS)
    ap.add_argument("--config", default=DEF_CONFIG)
    ap.add_argument("--bb-src", default=DEF_BB_SRC)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--trail", type=int, default=TRAIL_LEN, help="(classic) frames")
    ap.add_argument("--opacity", type=float, default=TRAIL_ALPHA, help="(classic)")
    ap.add_argument("--style", choices=("comet", "classic"), default="comet")
    ap.add_argument("--no-pass2", action="store_true",
                    help="pas de seconde passe de détection recadrée")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--cache", default=None, help="cache .json de trajectoire")
    ap.add_argument("--encoder", choices=("videotoolbox", "x264"),
                    default="videotoolbox")
    # Trajectoire seule (carte des rebonds de Ping Pong Edit) : détection +
    # suivi, cache --cache écrit, aucun rendu vidéo.
    ap.add_argument("--no-render", action="store_true",
                    help="s'arrête après la trajectoire (cache --cache requis)")
    # Analyse limitée à des plages de temps (s) : « t0-t1,t2-t3 ». Seules les
    # plages pas encore dans le cache --dets passent au réseau ; la trajectoire
    # est recalculée sur l'union de tout ce qui a été analysé.
    ap.add_argument("--ranges", default="",
                    help="plages de temps à analyser, ex. 12.5-40,63-90.2")
    args = ap.parse_args()
    if args.no_render and not args.cache:
        ap.error("--no-render exige --cache")
    if not args.no_render and not args.output:
        ap.error("--output est requis (sauf avec --no-render)")

    run_network(args)
    detections, fps, total, blob = load_detections(args.dets)
    print(f"  {total} frames, {sum(len(v) for v in detections.values())} "
          f"détections ({len(detections)} frames couvertes)", flush=True)

    points = None
    if args.cache and os.path.exists(args.cache):
        cb = json.load(open(args.cache))
        # Même union de plages analysées que le cache de détections, sinon
        # la trajectoire est à refaire (des plages ont été ajoutées).
        same = (cb.get("ranges") is None and blob.get("ranges") is None) or (
            cb.get("ranges") is not None and blob.get("ranges") is not None
            and norm_ranges(cb["ranges"]) == norm_ranges(blob["ranges"]))
        if cb.get("version") == TRAJ_VERSION and same:
            points = {int(k): tuple(v) for k, v in cb["points"].items()}
            print(f"Trajectoire relue du cache : {len(points)} frames", flush=True)
            print("PROGRESS det2 1 1", flush=True)
        else:
            print("  cache de trajectoire obsolète ou plages ajoutées, recalcul",
                  flush=True)
    if points is None:
        print("Passe 3/4 : suivi balistique…", flush=True)
        points = compute_trajectory(args, detections, blob, fps, total)
        if args.cache:
            cb = {"version": TRAJ_VERSION,
                  "points": {str(k): list(v) for k, v in points.items()},
                  "fps": fps, "total": total}
            _, _, _, blob = load_detections(args.dets)   # relu : passe 2 fusionnée
            if blob.get("ranges") is not None:
                cb["ranges"] = norm_ranges(blob["ranges"])
                cb["ranges_s"] = blob.get("ranges_s", [])
            json.dump(cb, open(args.cache, "w"))

    if args.no_render:
        print(f"Trajectoire seule : {len(points)} frames → {args.cache}", flush=True)
        return
    print("Passe 4/4 : rendu + encodage…", flush=True)
    n = render_pass(args, points, detections, fps, total, blob)
    print(f"Terminé : {args.output} ({n} frames)", flush=True)


if __name__ == "__main__":
    main()
