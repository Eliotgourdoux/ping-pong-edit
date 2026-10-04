# Journal des changements

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
