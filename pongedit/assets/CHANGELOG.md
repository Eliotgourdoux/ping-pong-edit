# Journal des changements

## 1.1.8 — 2026-10-04
- **Tableau de stats** : suppression des « +N » (plus grande avance de chaque joueur) à gauche de la courbe de dynamique, peu lisibles et peu utiles.

## 1.1.7 — 2026-10-04
- **Polices cohérentes sur Windows** : les accents (« terminé »), les tirets (« 3–0 ») et la typographie des incrustations étaient cassés car Windows n'a ni Helvetica ni DIN. L'app embarque maintenant la police Barlow (licence libre OFL), identique sur tous les PC. Sur Mac, rien ne change.
- **Journal de performance de l'export** (`%APPDATA%\PingPongEdit\logs\export.log`) : matériel détecté, encodeur choisi, charge processeur/carte graphique, images par seconde et vitesse, raison d'un éventuel repli sur le processeur.

## 1.1.6 — 2026-10-04
- **Export sur Windows réparé de bout en bout** (erreur « FFmpeg fail ») : les chemins Windows (`C:\…`) dans le graphe de montage, y compris ceux du score, sont maintenant correctement échappés pour ffmpeg.
- **Repli automatique sur le processeur** : si la carte graphique refuse l'encodage ou le décodage, l'export est relancé tout seul sur le processeur au lieu d'échouer.
- **Plus d'erreur d'affichage** du journal sur Windows (caractères spéciaux dans la console).
- Testé sur Windows avec un extrait de 20 s : export complet, fichier HEVC 1080p lisible de bout en bout. Le chemin carte graphique (NVIDIA/Intel) n'a pas pu être testé à distance.

## 1.1.5 — 2026-10-04
- **Plus jamais de perte de l'historique** : les sessions (points, noms, coupes, classements) étaient rangées dans le dossier de la version installée, donc chaque mise à jour repartait d'un dossier vide (perdu en passant de 1.1.3 à 1.1.4). Elles sont maintenant dans un dossier permanent (`%APPDATA%\PingPongEdit\sessions` sur Windows). Les sessions des versions précédentes sont récupérées automatiquement au lancement, et sauvegardées avant tout nettoyage d'une ancienne version.

## 1.1.4 — 2026-10-04
- **Export sur Windows réparé** (erreur « [WinError 206] nom de fichier ou extension trop long ») : un montage avec beaucoup de points et de coupes produit une commande ffmpeg plus longue que ce que Windows accepte. Le graphe de montage est maintenant lu depuis un fichier temporaire. Testé sur Windows avec une commande de plus de 50 000 caractères.
- **Carte graphique NVIDIA utilisable** : l'installeur embarque une version stable de ffmpeg (8.1) qui accepte les pilotes NVIDIA courants (la version précédente exigeait un pilote 610+ et retombait sur l'encodeur Intel).
- **Icône raquette partout** : les raccourcis du Bureau et du menu Démarrer sont corrigés automatiquement (ils gardaient l'icône par défaut des anciennes versions).
- **Panneau des actions** : défilement de haut en bas uniquement, plus de glissement latéral ; police des temps adaptée à Windows.
- **Barre d'outils** : le bouton « Ouvrir » a la même hauteur que les autres boutons (34 px au lieu de 38 px).

## 1.1.3 — 2026-10-04
- **Entrée quitte le champ** : après avoir saisi un nom, un classement, un point ou le nom du fichier, Entrée valide et rend le focus à la fenêtre, donc les raccourcis marchent tout de suite. Un clic en dehors du champ libère aussi le focus.
- **Vitesse de lecture sur les flèches ↑ / ↓** (plus de crochets, introuvables sur AZERTY).
- **Clavier AZERTY** : seuls « point J1 », « point J2 » et « couper » suivent la position des touches (Q, S, C sur AZERTY). Muet (M), annuler (Z), changer de service (F), rotation (R) restent des lettres, partout.
- **Notes de version** affichées à chaque mise à jour, et dans le menu de la version.
- **Signature** : fenêtre « À propos » (créé par Eliot Gourdoux) et licence.

## 1.1.2 — 2026-10-04
- Raccourcis indépendants de la disposition du clavier (QWERTY / AZERTY / QWERTZ détectés automatiquement), avec les vraies lettres affichées.

## 1.1.1 — 2026-10-04
- La version s'affiche dans la fenêtre ; un bouton vérifie les mises à jour.
- La vérification ne s'interdit plus 6 h entre deux lancements (2 minutes).
- Boîte « Redémarrer maintenant ? » après une mise à jour.

## 1.1.0 — 2026-10-04
- Optimisé Windows : encodage sur carte graphique (NVIDIA, Intel, AMD), repli processeur, décodage matériel.
- Icône raquette ; l'installeur propose de se supprimer une fois installé.

## 1.0.1 — 2026-10-04
- Installeur Windows autonome (aucune installation de Python nécessaire).

## 1.0.0 — 2026-10-04
- Première version publique : éditeur de points, export avec statistiques, bande des 8 derniers points, mise à jour automatique.
