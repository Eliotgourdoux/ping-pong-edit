"""Windows : applique l'icône raquette aux raccourcis existants (Bureau, menu Démarrer).

L'icône d'un raccourci vient de l'exécutable installé. Un utilisateur qui a installé une
ancienne version garde donc l'icône par défaut de PyInstaller, et la mise à jour automatique
ne remplace pas l'exécutable. On corrige les raccourcis eux-mêmes : une seule fois par version,
en tâche de fond, sans fenêtre.
"""

import base64
import shutil
import subprocess
import sys
from pathlib import Path

from pongedit.updater import data_dir
from pongedit.version import VERSION


def refresh_shortcut_icons() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        marker = data_dir() / f"shortcuts_{VERSION}.done"
        if marker.exists():
            return
        src = Path(__file__).parent / "assets" / "icon.ico"
        if not src.exists():
            return
        dest = data_dir() / "icon.ico"
        data_dir().mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        script = r'''
$ico = '__ICO__,0'
$sh = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'),
          [Environment]::GetFolderPath('CommonDesktopDirectory'), [Environment]::GetFolderPath('CommonPrograms'))
foreach ($d in $dirs) {
  Get-ChildItem $d -Filter 'Ping Pong Edit*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
    $l = $sh.CreateShortcut($_.FullName)
    if ($l.TargetPath -like '*PingPongEdit.exe' -and $l.IconLocation -ne $ico) { $l.IconLocation = $ico; $l.Save() }
  }
}
& ie4uinit.exe -show
'''.replace("__ICO__", str(dest))
        enc = base64.b64encode(script.encode("utf-16-le")).decode()
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                       capture_output=True, timeout=30, creationflags=0x08000000)
        marker.write_text("ok")
    except Exception as e:
        print(f"Icône des raccourcis: {e}")
