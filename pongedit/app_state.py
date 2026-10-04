"""État de l'application (state.json, vidéos récentes, slots, nouvelles fenêtres)."""

import os, sys, subprocess, re, json, signal
from pathlib import Path

from pongedit.utils import ENTRY_SCRIPT, PROJECT_DIR, _atomic_write_text


# État de l'application : UN seul fichier, hors du dossier du projet. Il portait
# avant sur plusieurs fichiers (last_video.json, last_video.2.json… un par
# fenêtre, plus recent_videos.json), posés à côté du code — donc sur le Bureau
# synchronisé iCloud, qui les resynchronisait à chaque ouverture de vidéo. Ici,
# ~/Library/Application Support n'est pas synchronisé : plus de va-et-vient
# iCloud, et plus de fichiers « .2. » qui traînent dans le projet.
from .updater import data_dir as _data_dir
STATE_DIR  = _data_dir()
STATE_FILE = STATE_DIR / "state.json"
# Anciens emplacements, relus une fois puis supprimés (voir _migrate_legacy_state).
LEGACY_STATE_DIR = PROJECT_DIR
RECENT_MAX = 8

# Structure de state.json :
#   {"windows": {"1": "/chemin/video.mov", "2": …},   ← dernière vidéo par fenêtre
#    "history": ["/chemin/a.mov", …]}                 ← récentes, partagées


def _read_state() -> dict:
    """État complet sur disque, avec reprise unique des anciens fichiers."""
    try:
        data = json.loads(STATE_FILE.read_text())
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        migrated = _migrate_legacy_state()
        if migrated is not None:
            return migrated
    except Exception:
        pass
    return {}


def _migrate_legacy_state():
    """Reprend last_video*.json / recent_videos.json, puis les efface.

    Renvoie l'état repris, ou None s'il n'y avait rien à reprendre.
    """
    windows, history = {}, []
    olds = sorted(LEGACY_STATE_DIR.glob("last_video*.json"))
    recent = LEGACY_STATE_DIR / "recent_videos.json"
    if not olds and not recent.exists():
        return None
    for f in olds:
        # "last_video.json" -> fenêtre 1 ; "last_video.2.json" -> fenêtre 2.
        parts = f.name.split(".")
        slot = parts[1] if len(parts) == 3 else "1"
        try:
            blob = json.loads(f.read_text())
        except Exception:
            continue
        path = blob.get("path", "")
        if isinstance(path, str) and path:
            windows[slot] = path
        history += [p for p in blob.get("history", []) if isinstance(p, str)]
    try:
        history += [p for p in json.loads(recent.read_text()).get("history", [])
                    if isinstance(p, str)]
    except Exception:
        pass
    state = {"windows": windows, "history": _merge_recent(history, [])}
    if not _write_state(state):
        return None
    for f in [*olds, recent]:
        try:
            f.unlink()
        except OSError:
            pass
    return state


def _write_state(state: dict) -> bool:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(STATE_FILE, json.dumps(state))
        return True
    except OSError:
        return False


def _read_shared_recent() -> list[str]:
    """Historique des vidéos récentes, PARTAGÉ par toutes les fenêtres : une
    nouvelle fenêtre sert justement à reprendre un autre match récent. Relu du
    disque avant chaque écriture pour que deux fenêtres ne s'écrasent pas."""
    return [p for p in _read_state().get("history", [])
            if isinstance(p, str) and Path(p).exists()]


def _merge_recent(mine: list[str], disk: list[str]) -> list[str]:
    """Ma liste d'abord (ordre local le plus frais), puis ce que les autres fenêtres
    ont ajouté entre-temps, sans doublon, bornée à RECENT_MAX."""
    out: list[str] = []
    for p in [*mine, *disk]:
        if p not in out and Path(p).exists():
            out.append(p)
    return out[:RECENT_MAX]


def _running_slots() -> set[str]:
    """Slots déjà occupés par des instances en cours (défaut "1" sans --slot)."""
    slots: set[str] = set()
    try:
        out = subprocess.run(["ps", "-Ao", "args="], capture_output=True, text=True).stdout
    except Exception:
        return slots
    for line in out.splitlines():
        if "pong_edit.py" not in line:
            continue
        m = re.search(r"--slot[= ](\S+)", line)
        slots.add(m.group(1) if m else "1")
    return slots


def _next_free_slot() -> str:
    """Plus petit slot entier libre (1, 2, 3, …) pour une nouvelle fenêtre."""
    used = _running_slots()
    i = 1
    while str(i) in used:
        i += 1
    return str(i)


def _cleanup_orphan_exports() -> int:
    """Tue les ffmpeg d'export ORPHELINS (PPID=1 → l'appli qui les a lancés est morte),
    laissés par une fenêtre fermée en cours d'export. Ne touche jamais un export vivant
    (dont l'appli parente tourne encore). Évite l'accumulation qui sature le CPU.
    Retourne le nombre de process tués."""
    killed = 0
    try:
        out = subprocess.run(["ps", "-Ao", "pid,ppid,args"],
                             capture_output=True, text=True).stdout
    except Exception:
        return 0
    for line in out.splitlines():
        if "ffmpeg" not in line or "pong_export" not in line:
            continue  # 'pong_export' couvre le tmp 'pong_export_' ET le dossier 'pong_exports'
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, ppid_s = parts[0], parts[1]
        if ppid_s != "1":
            continue  # parent vivant => export légitime en cours, on n'y touche pas
        try:
            os.kill(int(pid_s), signal.SIGKILL)
            killed += 1
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    return killed


def _spawn_new_window() -> None:
    """Lance une nouvelle instance indépendante du programme sur un slot libre.

    On passe par le bundle .app (`open -n`) quand il existe : la fenêtre est alors
    rattachée à l'app, donc elle hérite de l'icône 🏓 (dock, AltTab, Cmd+Tab).
    Repli sur un lancement Python direct si le bundle est absent (autre machine).
    """
    slot = _next_free_slot()
    bundle = PROJECT_DIR / "Ping Pong Edit.app"
    if bundle.exists():
        subprocess.Popen(
            ["open", "-n", str(bundle), "--args", "--slot", slot],
            start_new_session=True,
        )
    else:
        env = dict(os.environ, PONG_SLOT=slot)
        if getattr(sys, "frozen", False):
            # Version installée (.exe / .app empaqueté) : l'exécutable EST le programme.
            cmd = [sys.executable, "--slot", slot]
        else:
            cmd = [sys.executable, os.path.abspath(ENTRY_SCRIPT), "--slot", slot]
        subprocess.Popen(cmd, env=env, start_new_session=True)


# ── Carte d'intro : dernières valeurs saisies ─────────────────────────────────
# Le club et l'occasion changent rarement d'un match à l'autre : on garde, dans
# state.json (clé « intro_memory »), le club de chaque joueur (par nom), la
# dernière occasion, et les listes récentes (clubs, occasions, lieux) proposées
# en complétion dans le dialogue.
INTRO_RECENT_MAX = 12


def _read_intro_memory() -> dict:
    mem = _read_state().get("intro_memory")
    return mem if isinstance(mem, dict) else {}


def _remember_intro(values: dict, names: list[tuple[str, str]]) -> None:
    """Mémorise les valeurs validées dans le dialogue « Carte d'intro ».

    `names` : [(clé joueur "p1"/"p2", nom du tableau de score)] ; le club est
    rangé sous ce nom ET sous « prénom nom » du dialogue.
    """
    def norm(s) -> str:
        return " ".join(str(s or "").split())

    def push(lst, v):
        v = norm(v)
        out = [x for x in (lst or []) if isinstance(x, str) and x.lower() != v.lower()]
        return ([v] + out if v else out)[:INTRO_RECENT_MAX]

    state = _read_state()
    mem = dict(state.get("intro_memory") or {})
    clubs = dict(mem.get("clubs") or {})
    for k, board_name in names:
        club = norm(values.get(f"{k}_club"))
        full = norm(f"{values.get(f'{k}_first', '')} {values.get(f'{k}_last', '')}")
        for key in {norm(board_name).lower(), full.lower()} - {""}:
            if club:
                clubs[key] = club
            else:
                clubs.pop(key, None)
        mem["recent_clubs"] = push(mem.get("recent_clubs"), club)
    mem["clubs"] = clubs
    mem["occasion"] = norm(values.get("occasion"))
    mem["recent_occasions"] = push(mem.get("recent_occasions"), values.get("occasion"))
    mem["recent_lieux"] = push(mem.get("recent_lieux"), values.get("lieu"))
    state["intro_memory"] = mem
    _write_state(state)


# Réglages d'export (Tableau de fin, Intro, traînée) : globaux, gardés
# d'un lancement à l'autre plutôt que remis aux valeurs par défaut.
# Clés inconnues d'un ancien state.json (ex. l'ancienne option de carte) : ignorées.
EXPORT_PREFS_DEFAULT = {"stats_card": True, "intro": True, "trail": False}


def _read_export_prefs() -> dict:
    prefs = _read_state().get("export_prefs")
    out = dict(EXPORT_PREFS_DEFAULT)
    if isinstance(prefs, dict):
        out.update({k: bool(v) for k, v in prefs.items() if k in EXPORT_PREFS_DEFAULT})
    return out


def _write_export_prefs(prefs: dict) -> None:
    state = _read_state()
    state["export_prefs"] = {k: bool(prefs.get(k, v)) for k, v in EXPORT_PREFS_DEFAULT.items()}
    _write_state(state)
