#!/usr/bin/env python3
"""
Ping Pong Live Edit
Usage: python3 pong_edit.py
Requires: pip3 install PySide6   (+ ffmpeg in PATH)

Keys:
  A        → Player 1 scores
  S        → Player 2 scores
  C (hold) → Cut segment  (2× speed while held)
  Z        → Undo last action
  Space    → Play / Pause
"""

# Point d'entrée mince : le code vit dans le package pongedit/.
# Les noms historiques sont ré-exportés pour `from pong_edit import …` (tests, scripts).
# Les drapeaux de module modifiés à l'exécution (_OV_HLG, _VIDEOTOOLBOX_CACHE) ne le
# sont pas : leur seule source de vérité est pongedit.export.cards / .encoding.

# Mise à jour automatique : si une version plus récente que celle-ci a été téléchargée
# (voir pongedit/updater.py), son package `pongedit` passe en tête de sys.path.
# Ce bloc n'importe rien du package : il doit rester minimal et ne jamais planter.
import sys as _sys
try:
    from pathlib import Path as _P
    _here = _P(__file__).resolve().parent
    _sys.path.insert(0, str(_here))
    from pongedit.updater import overlay_path_if_newer as _ov
    _o = _ov()
    if _o is not None:
        for _m in [m for m in _sys.modules if m == "pongedit" or m.startswith("pongedit.")]:
            del _sys.modules[_m]
        _sys.path.insert(0, str(_o))
except Exception as _e:   # une mise à jour cassée ne doit jamais empêcher de lancer l'app
    print(f"Mise à jour ignorée: {_e}")

from pongedit.utils import (  # noqa: F401
    _resolve_slot, INSTANCE_SLOT, EXPORTS_DIR, SESSIONS_DIR,
    _atomic_write_text, _reserve_output_path, _video_hash_cached, _video_hash,
    _get_video_dimensions, _get_fps, gen_id, fmt_time, _fmt_chapter_time,
    _probe_video_props,
)
from pongedit.app_state import (  # noqa: F401
    STATE_DIR, STATE_FILE, LEGACY_STATE_DIR, RECENT_MAX, _read_state,
    _migrate_legacy_state, _write_state, _read_shared_recent, _merge_recent,
    _running_slots, _next_free_slot, _cleanup_orphan_exports,
    _spawn_new_window,
)
from pongedit.match.actions import (  # noqa: F401
    PointAction, CutAction, RotateAction, Action, ROT_LABELS, ROT_COLOR,
    action_time, sort_actions,
)
from pongedit.match.scoring import (  # noqa: F401
    _compute_stats_from_dicts, compute_sets, recompute_point_fields,
)
from pongedit.match.highlights import (  # noqa: F401
    HL_TOL, _match_highlight, _highlight_status, _highlight_counts,
)
from pongedit.export.encoding import (  # noqa: F401
    _check_videotoolbox, _run_ffmpeg_with_progress, _stream_copy_kept_segments,
    BITRATE_BPP_MIN, BITRATE_BPP_MAX, BITRATE_BPP_FALLBACK, BITRATE_HEADROOM,
    _max_quality_video_args,
)
from pongedit.export.segments import (  # noqa: F401
    _merge_overlapping_spans, _adjusted_time, _build_kept_segments,
    _kept_duration, _normalize_actions_for_kept_timeline,
    _youtube_chapters_from_actions,
)
from pongedit.export.cards import (  # noqa: F401
    _make_stats_card_png, _find_font, _find_display_font, _load_font, OV_P1,
    OV_P2, OV_GOLD, OV_INK, OV_WHITE, OV_MUTED, OV_FAINT, OV_BG_A,
    SCORECARD_SCALE, SCORECARD_BG_A, _ov_save, _is_hlg_source, _text_glyph_mm,
    _text_glyph_lm, _draw_serve_dot, _ov_tint, _find_label_font, _fit_text,
    _overlay_unit, _scorecard_margin, _make_scorecard_png, _text_bbox, _text_w,
    _text_h,
)
from pongedit.export.filters import (  # noqa: F401
    _build_overlay_filter, _rotation_chain, _build_filter, _rotation_filter,
)
from pongedit.export.worker import (  # noqa: F401
    _fmt_mmss, ExportWorker,
)
from pongedit.export.merge import (  # noqa: F401
    CANVAS_CHOICES, FIT_CHOICES, MERGE_AUDIO_ARGS, _probe_clip_info, _even,
    _segment_display_size, _merge_canvas, _fit_filters, _build_merge_command,
    MergeWorker,
)
from pongedit.ball.trail import (  # noqa: F401
    TRAIL_DIR, TRAIL_SCRIPT, TRAIL_PYTHON, TRAIL_CACHE, TRAIL_PHASES,
    TRAIL_STALL_SEC, TRAIL_LABELS, TrailWorker,
)
from pongedit.ui.style import (  # noqa: F401
    UI, _sep, _section, _field_label, _card,
)
from pongedit.ui.timeline import (  # noqa: F401
    TimelineWidget,
)
from pongedit.ui.scoreboard import (  # noqa: F401
    ScoreboardWidget,
)
from pongedit.ui.video import (  # noqa: F401
    VideoContainer,
)
from pongedit.ui.dialogs import (  # noqa: F401
    MergeDialog,
)
from pongedit.ui.main_window import (  # noqa: F401
    MainWindow,
)
from pongedit.app import (  # noqa: F401
    _deferred_orphan_cleanup, main,
)


if __name__ == "__main__":
    main()
