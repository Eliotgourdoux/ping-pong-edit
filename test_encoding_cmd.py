"""Commande ffmpeg trop longue (WinError 206) : le graphe de filtres passe par un fichier."""
import os, subprocess, sys, tempfile, unittest
from pongedit.export import encoding as e


class T(unittest.TestCase):
    def setUp(self):
        self._lim = e.CMD_LIMIT

    def tearDown(self):
        e.CMD_LIMIT = self._lim

    def test_commande_courte_inchangee(self):
        cmd = ["ffmpeg", "-i", "a.mp4", "-filter_complex", "[0:v]null[vout]", "out.mp4"]
        out, tmp = e._fit_command_line(cmd)
        self.assertEqual(out, cmd); self.assertEqual(tmp, [])

    def test_commande_longue_graphe_dans_un_fichier(self):
        e.CMD_LIMIT = 40
        graph = "[0:v]null[vout]"
        out, tmp = e._fit_command_line(["ffmpeg", "-i", "a.mp4", "-filter_complex", graph, "o.mp4"])
        try:
            self.assertNotIn(graph, out)
            self.assertIn(out[out.index("-i") + 2], ("-/filter_complex", "-filter_complex_script"))
            self.assertEqual(open(tmp[0], encoding="utf-8").read(), graph)
        finally:
            for f in tmp: os.unlink(f)

    def test_ffmpeg_reel_avec_graphe_en_fichier(self):
        """Vrai ffmpeg : même résultat avec le graphe lu depuis un fichier."""
        e.CMD_LIMIT = 40
        d = tempfile.mkdtemp(); o = os.path.join(d, "o.mp4")
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1",
               "-filter_complex", "[0:v]scale=160:90[vout]", "-map", "[vout]", "-c:v", "libx264", o]
        rc, err = e._run_ffmpeg_with_progress(cmd, lambda r: None, 1.0)
        self.assertEqual(rc, 0, err)
        self.assertGreater(os.path.getsize(o), 0)
        self.assertEqual(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width",
                                         "-of", "csv=p=0", o], capture_output=True, text=True).stdout.strip(), "160")

    def test_vraie_commande_de_plus_de_32767_caracteres(self):
        """Reproduit WinError 206 : sans le correctif, Windows refuse de lancer la commande."""
        d = tempfile.mkdtemp(); o = os.path.join(d, "o.mp4")
        # 4 chaînes de 2 500 filtres vers « nullsink » + la sortie : > 50 000 caractères, mais
        # chaque chaîne reste dans les limites propres de ffmpeg (≈ 3 000 filtres enchaînés).
        chain = ",".join(["null"] * 2500)
        graph = "[0:v]split=5[s0][s1][s2][s3][vout];" + ";".join(f"[s{i}]{chain},nullsink" for i in range(4))
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=0.5",
               "-filter_complex", graph, "-map", "[vout]", "-c:v", "libx264", o]
        self.assertGreater(len(subprocess.list2cmdline(cmd)), 32767)
        rc, err = e._run_ffmpeg_with_progress(cmd, lambda r: None, 0.5)
        self.assertEqual(rc, 0, err[-300:])
        self.assertGreater(os.path.getsize(o), 0)

    def test_tres_longue_ligne(self):
        """Un graphe de 60 000 caractères (bien au-delà de 32 767) devient une ligne courte."""
        graph = ";".join(f"[0:v]null[v{i}]" for i in range(4000)) + ";[0:v]null[vout]"
        self.assertGreater(len(graph), 60000)
        out, tmp = e._fit_command_line(["ffmpeg", "-i", "a.mp4", "-filter_complex", graph, "o.mp4"])
        try:
            self.assertLess(len(subprocess.list2cmdline(out)), 500)
        finally:
            for f in tmp: os.unlink(f)


if __name__ == "__main__":
    unittest.main()
