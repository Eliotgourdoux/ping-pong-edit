"""Les sessions (points, noms, coupes) ne doivent JAMAIS se perdre à une mise à jour."""
import json, os, sys, tempfile, time, unittest
from pathlib import Path
from pongedit import updater, utils


def _mk(path: Path, data: dict, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    if mtime:
        os.utime(path, (mtime, mtime))


class Sessions(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._saved = (updater.data_dir, updater.UPDATES_DIR, utils.PROJECT_DIR, getattr(sys, "frozen", None))
        updater.data_dir = lambda: self.d                       # dossier de données factice
        updater.UPDATES_DIR = self.d / "updates"

    def tearDown(self):
        updater.data_dir, updater.UPDATES_DIR, utils.PROJECT_DIR, fr = self._saved
        if fr is None and hasattr(sys, "frozen"):
            del sys.frozen

    def test_migration_recupere_les_sessions_des_anciennes_versions(self):
        _mk(self.d / "updates/1.1.3/sessions/a.json", {"actions": list(range(115))})
        n = updater.migrate_sessions(self.d / "sessions", [self.d / "updates/1.1.3/sessions"])
        self.assertEqual(n, 1)
        self.assertEqual(len(json.loads((self.d / "sessions/a.json").read_text())["actions"]), 115)
        self.assertTrue((self.d / "updates/1.1.3/sessions/a.json").exists())   # rien n'est supprimé

    def test_le_plus_recent_gagne_et_rien_n_est_ecrase_par_plus_ancien(self):
        now = time.time()
        _mk(self.d / "sessions/a.json", {"v": "nouveau"}, now)
        _mk(self.d / "updates/1.1.2/sessions/a.json", {"v": "ancien"}, now - 1000)
        updater.migrate_sessions(self.d / "sessions", [self.d / "updates/1.1.2/sessions"])
        self.assertEqual(json.loads((self.d / "sessions/a.json").read_text())["v"], "nouveau")

    def test_scenario_mise_a_jour_1_1_3_vers_1_1_4_vers_1_1_5(self):
        """Reproduit la perte : session créée en 1.1.3, puis deux mises à jour."""
        sys.frozen = True
        _mk(self.d / "updates/1.1.3/sessions/video.json", {"actions": [1, 2, 3]})
        for new in ("1.1.4", "1.1.5"):
            utils.PROJECT_DIR = self.d / "updates" / new          # le programme tourne depuis la nouvelle version
            (utils.PROJECT_DIR).mkdir(parents=True, exist_ok=True)
            got = utils._pick_sessions_dir()
            self.assertEqual(got, self.d / "sessions", "les sessions doivent être permanentes")
            self.assertEqual(json.loads((got / "video.json").read_text())["actions"], [1, 2, 3],
                             f"session perdue après passage en {new}")

    def test_session_creee_apres_migration_survit_a_la_suppression_d_une_version(self):
        sys.frozen = True
        utils.PROJECT_DIR = self.d / "updates/1.1.5"; utils.PROJECT_DIR.mkdir(parents=True)
        d = utils._pick_sessions_dir()
        _mk(d / "nouvelle.json", {"actions": [9]})
        import shutil
        shutil.rmtree(self.d / "updates/1.1.5")                  # nettoyage d'une ancienne version
        self.assertTrue((d / "nouvelle.json").exists())

    def test_developpement_inchange(self):
        """Sur le Mac de dev (pas d'exécutable figé, hors dossier de mises à jour) : `sessions/` du projet."""
        if hasattr(sys, "frozen"):
            del sys.frozen
        utils.PROJECT_DIR = Path(tempfile.mkdtemp())
        self.assertEqual(utils._pick_sessions_dir(), utils.PROJECT_DIR / "sessions")


if __name__ == "__main__":
    unittest.main()
