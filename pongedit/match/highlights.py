"""Mode highlight : tri des séquences."""




# ── Mode highlight ─────────────────────────────────────────────────────────────
# Une « séquence » = un segment conservé entre deux coupes. Le tri (garder /
# jeter) est mémorisé par bornes du segment : si une coupe bouge, la séquence
# redevient « à trier », et un undo→redo retrouve ses décisions.
HL_TOL = 0.05


def _match_highlight(segment: tuple[float, float], decisions: list[dict],
                     tol: float = HL_TOL) -> dict | None:
    """Décision enregistrée pour ce segment, à `tol` près sur les deux bornes."""
    s, e = segment
    for d in decisions:
        if abs(d["start"] - s) <= tol and abs(d["end"] - e) <= tol:
            return d
    return None


def _highlight_status(segments: list[tuple[float, float]],
                      decisions: list[dict]) -> list[bool | None]:
    """Un état par segment : True gardé, False rejeté, None à trier."""
    out: list[bool | None] = []
    for seg in segments:
        d = _match_highlight(seg, decisions)
        out.append(None if d is None else bool(d["keep"]))
    return out


def _highlight_counts(status: list[bool | None]) -> tuple[int, int, int]:
    kept = sum(1 for s in status if s is True)
    rejected = sum(1 for s in status if s is False)
    return kept, rejected, len(status) - kept - rejected
