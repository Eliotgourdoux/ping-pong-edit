"""Détection de la balle par le réseau BlurBall (table tennis, CVPRW 2026).

Enveloppe légère autour de cogsys-tuebingen/blurball : on reconstruit le modèle
depuis son YAML, on charge les poids pré-entraînés et on infère sur CPU/MPS
(le détecteur d'origine exige CUDA). Sortie : un JSON frame -> détections
(x, y, score, longueur de flou, angle) dans les coordonnées de la vidéo.
"""
import argparse
import bisect
import json
import os
import queue
import sys
import threading

import cv2
import numpy as np
import torch
import yaml

BB_SRC = os.environ.get("BLURBALL_SRC", "")
IN_W, IN_H = 512, 288


class AttrDict(dict):
    """Le modèle d'origine mélange cfg["x"] et cfg.X ; on accepte les deux."""

    def __init__(self, d):
        super().__init__({k: AttrDict(v) if isinstance(v, dict) else v
                          for k, v in d.items()})

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


def build(weights, cfg_path, device):
    sys.path.insert(0, BB_SRC)
    from models.blurball import BlurBall

    cfg = AttrDict(yaml.safe_load(open(cfg_path)))
    model = BlurBall(cfg)
    ck = torch.load(weights, map_location="cpu", weights_only=False)
    sd = ck.get("model_state_dict", ck)
    sd = {k[7:] if k.startswith("module.") else k: v for k, v in sd.items()}
    model.load_state_dict(sd)
    return model.to(device).eval(), cfg


MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def prep(bgr):
    img = cv2.resize(bgr, (IN_W, IN_H), interpolation=cv2.INTER_LINEAR)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    img = (img - MEAN) / STD
    return torch.from_numpy(img.transpose(2, 0, 1))


def prep_u8(bgr):
    """Même préparation, mais on s'arrête à l'entier 8 bits.

    Normaliser en numpy coûtait 8,4 ms par image sur le Victus, pour un calcul
    que le GPU fait pour rien. On ne redimensionne ici, et on transfère 4× moins
    d'octets (uint8 au lieu de float32) ; la mise à l'échelle se fait au bord du
    modèle, dans `to_tensor_batch`.
    """
    img = cv2.resize(bgr, (IN_W, IN_H), interpolation=cv2.INTER_LINEAR)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)          # H, W, 3 (uint8)


def to_tensor_batch(u8_windows, device, mean_t, std_t, half):
    """Empile des fenêtres de frames uint8 et les normalise sur le GPU."""
    arr = np.stack([np.concatenate(w, axis=2) for w in u8_windows])  # B,H,W,3*fin
    t = torch.from_numpy(arr).to(device, non_blocking=True)
    t = t.permute(0, 3, 1, 2).float().div_(255.0)
    t = (t - mean_t) / std_t
    return t.half() if half else t


class _CoreMLWrap(torch.nn.Module):
    """Réseau + normalisation, pour que Core ML reçoive directement du 0-255."""

    def __init__(self, model, fin, fout):
        super().__init__()
        self.m = model
        self.fout = fout
        self.register_buffer("mu", torch.tensor(np.tile(MEAN, fin)).view(1, -1, 1, 1) * 255.0)
        self.register_buffer("sd", torch.tensor(np.tile(STD, fin)).view(1, -1, 1, 1) * 255.0)

    def forward(self, x):
        x = (x - self.mu) / self.sd
        return self.m(x)[0][:, self.fout - 1].sigmoid()


def coreml_path(weights):
    return f"{os.path.splitext(weights)[0]}_{IN_W}x{IN_H}.mlpackage"


def coreml_export(weights, cfg_path, out_path):
    """Trace le réseau et l'écrit en Core ML fp16 (une seule fois, ~30 s).

    Le Neural Engine du M4 fait tourner ce réseau à ~88 img/s contre ~14 img/s
    pour PyTorch sur MPS, pour un écart de position de 0,004 px en moyenne.
    """
    import coremltools as ct

    model, cfg = build(weights, cfg_path, "cpu")
    fin, fout = cfg["frames_in"], cfg["frames_out"]
    wrap = _CoreMLWrap(model, fin, fout).eval()
    x = torch.zeros(1, 3 * fin, IN_H, IN_W)
    with torch.no_grad():
        ts = torch.jit.trace(wrap, x)
    ct.convert(ts,
               inputs=[ct.TensorType(name="x", shape=x.shape)],
               outputs=[ct.TensorType(name="heatmap")],
               compute_precision=ct.precision.FLOAT16,
               compute_units=ct.ComputeUnit.ALL,
               minimum_deployment_target=ct.target.macOS14).save(out_path)


def coreml_available():
    if sys.platform != "darwin":
        return False
    try:
        import coremltools  # noqa: F401
    except Exception:
        return False
    return True


def decode(hm, thr):
    """Composantes connexes du heatmap -> centroïde pondéré + axe principal."""
    out = []
    if hm.max() <= thr:
        return out
    _, binm = cv2.threshold(hm, thr, 1, cv2.THRESH_BINARY)
    n, labels = cv2.connectedComponents(binm.astype(np.uint8))
    for m in range(1, n):
        ys, xs = np.where(labels == m)
        ws = hm[ys, xs]
        s = ws.sum()
        if s <= 0:
            continue
        x = float((xs * ws).sum() / s)
        y = float((ys * ws).sum() / s)
        coords = np.column_stack((ys, xs)).astype(np.float32)
        if len(coords) >= 3:
            mean, eig = cv2.PCACompute(coords, mean=None)
            proj = (coords - mean) @ eig[0]
            length = float(proj.max() - proj.min())
            angle = float(np.degrees(np.arctan2(eig[0][0], eig[0][1])))
        else:
            length, angle = 1.0, 0.0
        out.append((x, y, float(s), length / 2.0, angle, int(len(xs)),
                    float(ws.max())))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=0.0)
    ap.add_argument("--thr", type=float, default=0.5)
    ap.add_argument("--device", default="")
    ap.add_argument("--batch", type=int, default=8)
    # Demi-précision : les détections sont identiques, mais mesuré sur MPS
    # c'est *plus lent* de bout en bout (229 s contre 195 s sur 593 frames).
    # Gardé pour CUDA, désactivé par défaut.
    ap.add_argument("--half", type=int, default=0)
    # Passe recadrée : {frame: [x0, y0, x1, y1]} ; seules ces frames sont
    # inférées, chacune sur sa fenêtre (la balle y est 2 à 3 fois plus grosse
    # pour le réseau). Les frames de contexte de la fenêtre temporelle sont
    # recadrées avec la même boîte.
    ap.add_argument("--rois-file", default="")
    ap.add_argument("--phase", default="det", help="nom de phase des lignes PROGRESS")
    # Core ML = Neural Engine du Mac (6x le debit de PyTorch/MPS). "auto" le
    # prend des qu'il est disponible et qu'aucun CUDA n'est la.
    ap.add_argument("--backend", default="auto", choices=["auto", "torch", "coreml"])
    # Plages de frames (indices GLOBAUX du fichier, « a-b,c-d », b exclu) : on
    # saute directement à chacune (seek) au lieu de décoder tout le fichier.
    # Exige --pts : instant de chaque frame (s, depuis le début du conteneur),
    # pour retrouver l'indice exact de la frame où le seek a atterri (cadence
    # variable des iPhone : « temps × fps » ne donne pas le bon indice).
    ap.add_argument("--frames", default="")
    ap.add_argument("--pts", default="")
    a = ap.parse_args()
    rois = {}
    if a.rois_file:
        rois = {int(k): [int(v) for v in box]
                for k, box in json.load(open(a.rois_file)).items()}
    segs = []
    if a.frames:
        segs = [tuple(int(x) for x in s.split("-")) for s in a.frames.split(",") if s]
        segs = [(s0, s1) for s0, s1 in segs if s1 > s0]
    pts = json.load(open(a.pts)) if a.pts else None
    if segs and not pts:
        sys.exit("--frames exige --pts")
    # Passe recadrée avec --pts : même principe, on ne décode que les abords
    # des frames à re-scruter.
    seek_mode = bool(pts) and bool(segs or rois)

    # CUDA d'abord : le seul GPU du parc qui ne se bride pas sous charge est la
    # RTX 3060 du Victus ; le GPU Apple (MPS) reste le repli local.
    device = a.device or ("cuda" if torch.cuda.is_available()
                          else "mps" if torch.backends.mps.is_available()
                          else "cpu")
    use_ml = (a.backend == "coreml"
              or (a.backend == "auto" and device != "cuda" and coreml_available()))
    ml = ml_out = model = None
    half = False
    if use_ml:
        import coremltools as ct

        mlp = coreml_path(a.weights)
        if not os.path.isdir(mlp):
            print("Conversion Core ML du reseau (une seule fois)...", flush=True)
            coreml_export(a.weights, a.config, mlp)
        cfg = AttrDict(yaml.safe_load(open(a.config)))
        ml = ct.models.MLModel(mlp, compute_units=ct.ComputeUnit.ALL)
        ml_out = list(ml.get_spec().description.output)[0].name
        # Le Neural Engine est le plus rapide image par image ; au-dela de
        # batch 4 Core ML repasse sur le GPU et on perd la moitie du debit.
        a.batch = 1
    else:
        model, cfg = build(a.weights, a.config, device)
        half = bool(a.half)
        if half:
            model = model.half()
    fin, fout = cfg["frames_in"], cfg["frames_out"]

    cap = cv2.VideoCapture(a.input)
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if a.start > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, a.start * 1000.0)
    n_max = int(round(a.duration * fps)) if a.duration else 1 << 30
    n_file = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) - int(round(a.start * fps))
    n_total = min(n_max, n_file) if n_file > 0 else n_max

    sx, sy = W / IN_W, H / IN_H
    # Moyenne/écart-type d'ImageNet, répétés une fois par frame de la fenêtre.
    mean_t = std_t = None
    if ml is None:
        mean_t = torch.tensor(np.tile(MEAN, fin), device=device).view(1, -1, 1, 1)
        std_t = torch.tensor(np.tile(STD, fin), device=device).view(1, -1, 1, 1)

    dets, batch, bidx = {}, [], []
    # Écart image-à-image en miniature : sert au repérage des coupes de montage
    # (voir ball_trail2.detect_cuts). Gratuit ici, on décode déjà chaque frame.
    diffs = []

    def flush():
        if not batch:
            return
        if ml is not None:
            arr = np.stack([np.concatenate(w, axis=2) for w in batch])
            arr = np.ascontiguousarray(arr.transpose(0, 3, 1, 2), dtype=np.float32)
            y = np.asarray(ml.predict({"x": arr})[ml_out]).reshape(len(batch), IN_H, IN_W)
        else:
            x = to_tensor_batch(batch, device, mean_t, std_t, half)
            with torch.no_grad():
                # On ne garde que la dernière sortie de la fenêtre : la ramener
                # seule divise par 3 ce qui transite du GPU vers le CPU.
                y = model(x)[0][:, fout - 1].sigmoid().float().cpu().numpy()
        for k, f in enumerate(bidx):
            # dernière sortie de la fenêtre = frame courante
            hm = y[k]
            got = []
            if f in rois:
                x0, y0, x1, y1 = rois[f]
                fx, fy = (x1 - x0) / IN_W, (y1 - y0) / IN_H
            else:
                x0, y0, fx, fy = 0, 0, sx, sy
            for cx, cy, sc, ln, ang, npx, pk in decode(hm, a.thr):
                got.append({"x": x0 + cx * fx, "y": y0 + cy * fy, "score": sc,
                            "len": ln * fx, "angle": ang, "npx": npx,
                            "peak": pk})
            if got:
                dets[f] = got
        batch.clear()
        bidx.clear()

    # Décodage vidéo et redimensionnement dans un thread séparé : ce travail-là
    # (~5 ms de lecture + ~3 ms de resize par image) se recouvre alors avec
    # l'inférence au lieu de s'y ajouter.
    q = queue.Queue(maxsize=64)

    last_roi = max(rois) if rois else -1
    n_rois, done_rois = len(rois), 0

    # ── Lecture par plages (seek) ──
    # Le décodage d'une frame 1080p coûte ~3,5 ms même sans passer au réseau :
    # sauter les coupes de montage au lieu de les décoder est ce qui fait gagner
    # le plus. Un seek atterrit sur la frame à (ou juste avant) l'instant visé ;
    # son horodatage (POS_MSEC) donne l'indice exact dans `pts`, vérifié
    # identique pixel à pixel à la lecture séquentielle.
    SEEK_MIN_GAP = 40             # trou plus court : on lit en avançant, sans seek
    cur = [0]                     # indice de la frame que read() rendra ensuite

    def _nearest(sec):
        j = bisect.bisect_left(pts, sec)
        if j >= len(pts):
            return len(pts) - 1
        if j > 0 and sec - pts[j - 1] < pts[j] - sec:
            return j - 1
        return j

    def _goto(p):
        """Frame n° p (image BGR), lecture positionnée juste après ; None si fin."""
        if not (0 <= p - cur[0] <= SEEK_MIN_GAP):
            for back in (0.0, 1.0, 4.0, None):
                if back is None:          # dernier recours : depuis le début
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    cur[0] = 0
                    break
                cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, pts[p] - back) * 1000.0)
                ok, fr = cap.read()
                if not ok:
                    return None
                i = _nearest(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
                cur[0] = i + 1
                if i == p:
                    return fr
                if i < p:
                    break
        while cur[0] < p:
            if not cap.grab():
                return None
            cur[0] += 1
        ok, fr = cap.read()
        if not ok:
            return None
        cur[0] += 1
        return fr

    def _read():
        ok, fr = cap.read()
        if ok:
            cur[0] += 1
        return fr if ok else None

    def reader_seek():
        try:
            if rois:
                # Passe recadrée : groupes de frames à re-scruter proches.
                fs = sorted(rois)
                groups, g0, g1 = [], fs[0], fs[0]
                for f in fs[1:]:
                    if f - g1 > 60:
                        groups.append((g0, g1 + 1))
                        g0 = f
                    g1 = f
                groups.append((g0, g1 + 1))
                for s0, s1 in groups:
                    i = max(0, s0 - (fin - 1))
                    fr = _goto(i)
                    raw = []
                    while fr is not None and i < s1:
                        raw.append(fr)
                        if len(raw) > fin:
                            raw.pop(0)
                        if i in rois:
                            x0, y0, x1, y1 = rois[i]
                            win = [prep_u8(r[y0:y1, x0:x1]) for r in raw]
                            while len(win) < fin:
                                win.insert(0, win[0])
                            q.put((i, win, 0.0, True))
                        i += 1
                        fr = _read() if i < s1 else None
                return
            for s0, s1 in segs:
                # fin-1 frames d'amorce avant la plage : la fenêtre du réseau est
                # pleine dès la 1ʳᵉ frame et l'écart image-à-image est juste —
                # deux plages contiguës donnent exactement une analyse d'un bloc.
                i = max(0, s0 - (fin - 1))
                fr = _goto(i)
                q.put(("seg", s0))
                prev_small = None
                while fr is not None and i < s1:
                    small = cv2.cvtColor(cv2.resize(fr, (128, 72)),
                                         cv2.COLOR_BGR2GRAY).astype("float32")
                    d = 0.0 if prev_small is None else float(abs(small - prev_small).mean())
                    prev_small = small
                    q.put((i, prep_u8(fr), d, i >= s0))
                    i += 1
                    fr = _read() if i < s1 else None
        finally:
            q.put(None)

    def reader():
        i = 0
        prev_small = None
        raw = []                      # passe recadrée : dernières frames brutes
        try:
            while i < n_max:
                if rois and i > last_roi:
                    break
                ok, fr = cap.read()
                if not ok:
                    break
                if rois:
                    raw.append(fr)
                    if len(raw) > fin:
                        raw.pop(0)
                    if i in rois:
                        x0, y0, x1, y1 = rois[i]
                        win = [prep_u8(r[y0:y1, x0:x1]) for r in raw]
                        while len(win) < fin:
                            win.insert(0, win[0])
                        q.put((i, win, 0.0))
                else:
                    small = cv2.cvtColor(cv2.resize(fr, (128, 72)),
                                         cv2.COLOR_BGR2GRAY).astype("float32")
                    d = 0.0 if prev_small is None else float(abs(small - prev_small).mean())
                    prev_small = small
                    q.put((i, prep_u8(fr), d))
                i += 1
        finally:
            q.put(None)

    th = threading.Thread(target=reader_seek if seek_mode else reader, daemon=True)
    th.start()

    buf = []
    fidx = 0
    # Avec Core ML on infère image par image : on n'annonce la progression que
    # toutes les 8 frames, sinon la sortie noie l'appelant.
    prog_step = max(8, a.batch)
    last_prog = 0
    if seek_mode and not rois:
        n_total = sum(s1 - s0 for s0, s1 in segs)
    done_seg, seg_diffs, seg_end = 0, [], {}
    while True:
        item = q.get()
        if item is None:
            break
        if seek_mode:
            if item[0] == "seg":              # nouvelle plage : fenêtre vidée
                buf.clear()
                seg_diffs.append([item[1], []])
                continue
            fidx, u8, d, infer = item
            if rois:
                batch.append(u8)
                bidx.append(fidx)
                done_rois += 1
                if len(batch) >= a.batch:
                    flush()
                    if done_rois - last_prog >= prog_step or done_rois == n_rois:
                        last_prog = done_rois
                        print(f"PROGRESS {a.phase} {done_rois} {n_rois}", flush=True)
                continue
            buf.append(u8)
            if len(buf) > fin:
                buf.pop(0)
            if not infer:                      # amorce : contexte seulement
                continue
            seg_diffs[-1][1].append(d)
            done_seg += 1
            if len(buf) == fin:
                batch.append(list(buf))
                bidx.append(fidx)
                if len(batch) >= a.batch:
                    flush()
                    if done_seg - last_prog >= prog_step:
                        last_prog = done_seg
                        print(f"PROGRESS {a.phase} {done_seg} {n_total}", flush=True)
            continue
        fidx, u8, d = item
        if rois:
            batch.append(u8)
            bidx.append(fidx)
            done_rois += 1
            if len(batch) >= a.batch:
                flush()
                if done_rois - last_prog >= prog_step or done_rois == n_rois:
                    last_prog = done_rois
                    print(f"PROGRESS {a.phase} {done_rois} {n_rois}", flush=True)
            continue
        diffs.append(d)
        buf.append(u8)
        if len(buf) > fin:
            buf.pop(0)
        if len(buf) == fin:
            batch.append(list(buf))
            bidx.append(fidx)
            if len(batch) >= a.batch:
                flush()
                if fidx - last_prog >= prog_step:
                    last_prog = fidx
                    print(f"\r{fidx} frames", end="", flush=True)
                    # ligne lisible par l'appelant (barre de progression de l'app)
                    print(f"\nPROGRESS {a.phase} {fidx} {n_total}", flush=True)
        fidx += 1
    fidx = len(diffs) if not rois else fidx
    flush()
    cap.release()
    print()
    out = {"version": 2, "fps": fps, "w": W, "h": H, "start": a.start,
           "diffs": diffs, "frames": fidx, "rois": bool(rois),
           "dets": {str(k): v for k, v in dets.items()}}
    if seek_mode and not rois:
        # Plages réellement lues (une fin de fichier peut raccourcir la dernière)
        # et leurs écarts image-à-image ; indices globaux du fichier.
        out["frames"] = len(pts)
        out["ranges"] = [[s0, s0 + len(ds)] for s0, ds in seg_diffs if ds]
        out["diffs_r"] = [[s0, ds] for s0, ds in seg_diffs if ds]
        fidx = done_seg
    json.dump(out, open(a.out, "w"))
    print(f"{len(dets)}/{fidx} frames avec détection -> {a.out}")


if __name__ == "__main__":
    main()
