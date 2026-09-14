# HeroScriptsDynamo

Suite de scripts Python pour Dynamo (Revit) destinée à automatiser les tâches récurrentes d'un **BIM Manager** : gestion des warnings, nettoyage du modèle (filtres, gabarits de vue), gestion des éléments miroir, copie de vues entre maquettes, génération de vues par sous-projet, placement automatique de groupes de modèle par pièce, et suivi/rapport HTML de l'évolution du modèle.

🎩 by **Manseur Mohamed**

## Structure de chaque script

Chaque script existe en **paire de fichiers** :

- **`NN - Titre.dyn`** — le graphe Dynamo. Le code Python y est intégré directement dans le nœud (`Nodes[0].Code`) : c'est ce que Dynamo exécute réellement.
- **`NN - Titre.py`** — un miroir texte du même code, tenu **strictement identique** au `.dyn`, pour pouvoir le relire/comparer sans ouvrir Dynamo.

Pour utiliser un script : ouvrir le `.dyn` correspondant dans Dynamo (pour Revit) et lancer l'exécution du graphe (Run).

## Les 11 scripts

| # | Script | Description |
|---|--------|-------------|
| 01 | Gestion Warnings par Créateur | Regroupe les warnings actifs du document par créateur (worksharing) puis par type, et isole les éléments concernés dans la vue courante. Fenêtre non-modale : reste ouverte pendant qu'on corrige dans Revit. |
| 02 | Isoler par Type de Warning | Isole les éléments d'un type de warning choisi dans la vue courante, ou ouvre une nouvelle vue dédiée. |
| 03 | Isolement Permanent par Créateur et Warning | Pour un créateur et un type de warning choisis, crée une vue 3D dédiée et masque **de façon permanente** (pas un isolement temporaire) tout le reste du modèle dans cette vue — utile pour de la documentation qui doit survivre à la fermeture du fichier. |
| 04 | Isoler les Éléments Miroir | Isole les éléments miroir (familles retournées) dans la vue active. |
| 05 | Gestion des Types Miroir | Assistant en 3 étapes : sélection des types ayant des instances miroir → nommage (préfixe/suffixe) et création des nouveaux types → remplacement des éléments miroir par ces nouveaux types. |
| 06 | Supprimer les Filtres Non Utilisés | Liste les filtres de vue non appliqués à aucune vue du projet et permet de les supprimer en masse. |
| 07 | Supprimer les Gabarits de Vue Non Utilisés | Liste les gabarits de vue non appliqués à aucune vue (et non définis par défaut sur un type de vue) et permet de les supprimer en masse. |
| 08 | Copie Views from Link-Other Project | Copie des vues (tous types, y compris vues de dessin/détails) depuis un autre document ouvert ou un lien Revit, avec filtres, paramètres, gabarit de vue et annotations. |
| 09 | Création de Vues par Sous-Projet | Crée une vue 3D et/ou un plan (View Range étendu, filaire) par sous-projet, avec uniquement ce sous-projet visible dans la vue — met à jour les vues déjà existantes plutôt que de les recréer. |
| 10 | Placer un Groupe de Modèle par Pièce | Place un groupe de modèle (mobilier, équipement) dans toutes les pièces dont un paramètre choisi vaut une valeur choisie, orienté selon la porte de chaque pièce. Reprise en Dynamo de la logique métier du plugin Revit autonome [BIMATIKA — Groupe par pièce](https://github.com/Manseur-Moh/BimAtika-GroupeToRoom). |
| 11 | Suivi des Éléments et Rapport HTML | À chaque exécution, prend un instantané de tous les éléments du projet (catégorie, famille, type, sous-projet, créateur, dernier modificateur, paramètres) et génère un rapport HTML autonome (onglets Détail / Récapitulatif / Diagrammes) permettant de comparer deux dates (Ajouté/Supprimé/Modifié) et suivre l'évolution du modèle dans le temps — tout l'historique est embarqué dans le HTML, rien à re-choisir à l'ouverture. Une fenêtre demande le dossier d'export au lancement (dernier dossier utilisé mémorisé). |

## Interface

Tous les scripts partagent la même charte graphique (Windows Forms) : Segoe UI, titre en gras + signature 🎩 en haut à droite, code couleur cohérent pour les boutons (bleu = action principale, vert = création, orange = remplacement, rouge = suppression, gris = secondaire/fermer).

## Prérequis

- Autodesk Revit + Dynamo (module Player ou Dynamo for Revit).
- Moteur Python du nœud, pour les 11 scripts : **IronPython2** (testé sur Revit 2024). Compatible CPython3 sous réserve d'adapter l'`Engine` dans le `.dyn` (voir `CLAUDE.md`) — le script 11 a ete ecrit pour fonctionner sous les deux moteurs si besoin.

## Notes techniques

- Voir `CLAUDE.md` pour les conventions du projet (rôles Claude/Ollama, stack technique, compatibilité Revit 2024–2027).
- `.gitattributes` fixe les `.py` en LF pur et laisse les `.dyn` intacts (binaire) pour éviter toute divergence entre les deux fichiers d'une même paire lors d'un clone.
