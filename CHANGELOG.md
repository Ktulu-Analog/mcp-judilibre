# Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Format basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/).

---

## [1.1.0] - 2026-05-26

### Ajouté

- **Dockerfile** : image Docker officielle basée sur `python:3.12-slim`. Permet de déployer le serveur MCP sans installer Python localement, sur n'importe quelle machine accessible en réseau.
- **README** : nouvelle section *Installation via Docker* et section *Démarrage via Docker* avec les commandes `docker build` et `docker run`.

---

## [1.0.0] - 2026-05-26

### Ajouté

- Serveur MCP HTTP Streamable (spec 2025-03-26) exposant l'API JUDILIBRE (PISTE / Cour de cassation).
- 8 outils MCP :
  - `judilibre_rechercher` — recherche plein texte paginée avec filtres
  - `judilibre_decision` — texte intégral et métadonnées d'une décision par ID
  - `judilibre_scan` — export par lot avec pagination par curseur (`search_after`)
  - `judilibre_export` — export par lot avec pagination par numéro de lot (`batch`)
  - `judilibre_taxonomie` — référentiel des valeurs valides pour les filtres
  - `judilibre_stats` — statistiques globales et agrégations de la base
  - `judilibre_historique` — historique transactionnel (create/update/delete)
  - `judilibre_healthcheck` — vérification de disponibilité de l'API
- Authentification OAuth2 client credentials via la plateforme PISTE, avec renouvellement automatique du token et 3 tentatives en cas d'erreur réseau.
- Chargement optionnel des identifiants via fichier `.env` (python-dotenv).
- Compatible Claude Desktop, Claude Code, et tout client MCP supportant HTTP Streamable.
