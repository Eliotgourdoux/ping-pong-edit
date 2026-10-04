# Ping Pong Edit

Application desktop pour couper rapidement des matchs de ping, marquer les points, puis exporter une video montee.

## Commande unique pour creer l'app

Depuis la racine du projet :

- macOS : `python3 make_launcher.py`
- Linux : `python3 make_launcher.py`
- Windows : `python make_launcher.py`

Wrappers disponibles :

- macOS / Linux : `./make_app.sh`
- Windows : `make_app.bat`

## Resultat cree

- macOS : `Ping Pong Edit.app`
- Linux : `Ping Pong Edit.desktop` et `ping-pong-edit.sh`
- Windows : `Ping Pong Edit.bat`

Genere les lanceurs sur la machine cible.
Le script enregistre le chemin du Python courant, donc un lanceur Windows doit etre cree depuis Windows, un lanceur Linux depuis Linux, etc.

## Prerequis

- Python avec `PySide6`
- `ffmpeg` et `ffprobe` dans le `PATH`

## Lancer l'editeur

Une fois le lanceur cree, ouvre l'artefact correspondant a ta plateforme.

## Mises à jour automatiques

Au démarrage, l'app vérifie en tâche de fond s'il existe une version plus récente sur
GitHub (Releases). Si oui, elle la télécharge, vérifie son empreinte SHA-256 et l'utilise
au lancement suivant. Aucun droit administrateur n'est nécessaire.

Publier une version (mainteneur) : `python3 release.py 1.1.0 "notes"`.

## Installation depuis les sources (Windows / macOS / Linux)

    pip install PySide6 Pillow numpy
    # + ffmpeg et ffprobe dans le PATH
    python pong_edit.py
