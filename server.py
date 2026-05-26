#!/usr/bin/env python3
# ============================================================================
# JUDILIBRE — Serveur MCP HTTP Streamable (spec 2025-03-26)
# ============================================================================
# Auteur  : Pierre COUGET  ktulu.analog@gmail.com
# Licence : GNU Affero General Public License v3.0 (AGPL-3.0)
#           https://www.gnu.org/licenses/agpl-3.0.html
# Année   : 2026
# ----------------------------------------------------------------------------
# Ce fichier expose 8 outils JUDILIBRE via le protocole MCP
# HTTP Streamable (spec 2025-03-26), utilisable par n'importe quel client MCP.
#
# Démarrage :
#   JUDILIBRE_CLIENT_ID=... JUDILIBRE_CLIENT_SECRET=... python server.py
#
# Ou via un fichier .env :
#   pip install python-dotenv
#   (le serveur charge automatiquement .env s'il est présent)
#
# L'endpoint MCP est accessible à :
#   http://localhost:6501/mcp
# ============================================================================

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP

# Chargement optionnel du .env (python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Création du serveur MCP
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="judilibre",
    instructions=(
        "Serveur MCP donnant accès à l'API JUDILIBRE (PISTE). "
        "8 outils couvrant : recherche plein texte de décisions de justice, "
        "récupération de décision complète par ID, export par lot par curseur (scan) "
        "ou par numéro de lot (export), taxonomie des valeurs de filtres, "
        "statistiques de la base, historique transactionnel des opérations, "
        "et healthcheck de disponibilité du service. "
        "Couvre la Cour de cassation, les cours d'appel et les tribunaux judiciaires. "
        "Prérequis : variables d'environnement JUDILIBRE_CLIENT_ID et JUDILIBRE_CLIENT_SECRET."
    ),
)

# ---------------------------------------------------------------------------
# Client JUDILIBRE (OAuth2 PISTE, singleton)
# ---------------------------------------------------------------------------

class _JudiLibreClient:
    """Client OAuth2 PISTE — singleton, partagé par tous les outils MCP."""

    BASE_URL  = "https://api.piste.gouv.fr/cassation/judilibre/v1.0"
    OAUTH_URL = "https://oauth.piste.gouv.fr/api/oauth/token"

    def __init__(self):
        self._token: Optional[str] = None
        self._token_expiry: float  = 0
        self._http = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )

    def _get_token(self) -> str:
        client_id     = os.getenv("JUDILIBRE_CLIENT_ID")
        client_secret = os.getenv("JUDILIBRE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError(
                "Identifiants JUDILIBRE manquants.\n"
                "Définissez JUDILIBRE_CLIENT_ID et JUDILIBRE_CLIENT_SECRET."
            )
        if self._token and time.time() < (self._token_expiry - 60):
            return self._token
        r = self._http.post(
            self.OAUTH_URL,
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
                "scope":         "openid",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        d = r.json()
        self._token        = d["access_token"]
        self._token_expiry = time.time() + d.get("expires_in", 3600)
        return self._token

    def _req(self, path: str, params: Optional[Dict] = None) -> Dict:
        """Effectue un GET authentifié sur l'API JUDILIBRE."""
        url = f"{self.BASE_URL}{path}"
        # Nettoyage des params None et listes vides
        clean_params: Dict[str, Any] = {}
        if params:
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, list) and len(v) == 0:
                    continue
                clean_params[k] = v

        for attempt in range(3):
            try:
                resp = self._http.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._get_token()}",
                        "Accept":        "application/json",
                    },
                    params=clean_params or None,
                )
                if resp.status_code == 401 and attempt == 0:
                    self._token = None
                    continue
                if resp.status_code == 429:
                    raise RuntimeError("Limite de débit API JUDILIBRE atteinte (429)")
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if attempt == 2 or e.response.status_code < 500:
                    raise RuntimeError(
                        f"Erreur HTTP {e.response.status_code} sur {path}"
                    )
                time.sleep(2 ** attempt)
            except httpx.RequestError as e:
                if attempt == 2:
                    raise RuntimeError(f"Erreur réseau : {e}")
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Échec après 3 tentatives sur {path}")


# Singleton partagé
_client: Optional[_JudiLibreClient] = None

def _get_client() -> _JudiLibreClient:
    global _client
    if _client is None:
        _client = _JudiLibreClient()
    return _client


# ---------------------------------------------------------------------------
# Helpers de formatage
# ---------------------------------------------------------------------------

def _fmt_decision_short(d: Dict) -> str:
    """Formate un résumé court d'une décision."""
    lines = []
    rid      = d.get("id", "")
    juri     = d.get("jurisdiction", "")
    chamber  = d.get("chamber", "")
    number   = d.get("number", "")
    date     = d.get("decision_date", "")
    solution = d.get("solution", "")
    summary  = d.get("summary", "")
    themes   = d.get("themes", [])
    ecli     = d.get("ecli", "")

    header = f"**{number}**" if number else "**Décision**"
    if date:
        header += f" — {date}"
    if solution:
        header += f" — *{solution}*"
    lines.append(header)

    meta = []
    if juri:
        meta.append(juri)
    if chamber:
        meta.append(chamber)
    if ecli:
        meta.append(f"ECLI : {ecli}")
    if rid:
        meta.append(f"ID : `{rid}`")
    if meta:
        lines.append("  " + " | ".join(meta))

    if themes:
        lines.append(f"  Matières : {', '.join(themes[:5])}")
    if summary:
        lines.append(f"  {summary[:300]}{'…' if len(summary) > 300 else ''}")

    return "\n".join(lines)


def _fmt_decision_full(d: Dict) -> str:
    """Formate le contenu complet d'une décision."""
    lines = [_fmt_decision_short(d)]

    visa = d.get("visa", [])
    if visa:
        lines.append(f"\n**Textes appliqués ({len(visa)})** :")
        for v in visa[:10]:
            lines.append(f"  - {v.get('title', '')}")

    rapprochements = d.get("rapprochements", [])
    if rapprochements:
        lines.append(f"\n**Rapprochements ({len(rapprochements)})** :")
        for r in rapprochements[:5]:
            lines.append(f"  - {r.get('title', '')} ({r.get('number', '')})")

    files = d.get("files", [])
    if files:
        lines.append(f"\n**Documents associés ({len(files)})** :")
        for f in files:
            lines.append(f"  - {f.get('name', '')} ({f.get('type', '')})")

    text = d.get("text", "") or d.get("text_highlight", "")
    if text:
        lines.append(f"\n**Texte intégral** :\n\n{text[:6000]}")
        if len(text) > 6000:
            lines.append(
                f"\n*[Texte tronqué — {len(text)} caractères au total. "
                "Consultez judilibre.fr pour le texte intégral]*"
            )

    return "\n".join(lines)


# ===========================================================================
# OUTILS MCP — Recherche et consultation
# ===========================================================================

@mcp.tool()
def judilibre_rechercher(
    query: str,
    operateur: str = "or",
    champs: Optional[List[str]] = None,
    juridiction: Optional[List[str]] = None,
    chambre: Optional[List[str]] = None,
    formation: Optional[List[str]] = None,
    type_decision: Optional[List[str]] = None,
    solution: Optional[List[str]] = None,
    publication: Optional[List[str]] = None,
    matiere: Optional[List[str]] = None,
    localisation: Optional[List[str]] = None,
    date_debut: Optional[str] = None,
    date_fin: Optional[str] = None,
    tri: str = "scorepub",
    ordre: str = "desc",
    nb_resultats: int = 10,
    page: int = 0,
    type_fichier: Optional[List[str]] = None,
    interet_particulier: Optional[bool] = None,
) -> str:
    """Recherche dans la base Open Data des décisions de justice françaises (JUDILIBRE).

    Couvre la Cour de cassation, les cours d'appel et les tribunaux judiciaires.
    Retourne une liste paginée de décisions avec métadonnées et sommaires.
    Utilisez judilibre_decision pour obtenir le texte intégral d'une décision.

    Args:
        query: Termes de recherche en texte libre (ex: 'responsabilité civile', 'licenciement').
        operateur: Opérateur logique : 'or' (défaut), 'and', ou 'exact' pour une expression exacte.
        champs: Zones ciblées parmi : expose, moyens, motivations, dispositif, annexes,
                sommaire, titrage. Si vide, recherche dans tout le texte intégral.
        juridiction: Filtrer par juridiction : 'cc' (Cour de cassation), 'ca' (cour d'appel),
                     'tj' (tribunal judiciaire).
        chambre: Filtrer par chambre (ex: 'civ1', 'civ2', 'civ3', 'com', 'soc', 'crim').
                 Valeurs via judilibre_taxonomie(id='chamber').
        formation: Filtrer par formation (ex: 'fs', 'fp', 'fplr').
                   Valeurs via judilibre_taxonomie(id='formation').
        type_decision: Filtrer par nature : 'arret', 'qpc', 'ordonnance', 'saisie'…
                       Valeurs via judilibre_taxonomie(id='type').
        solution: Filtrer par solution : 'cassation', 'rejet', 'annulation', 'avis', 'irrecevabilite'…
                  Valeurs via judilibre_taxonomie(id='solution').
        publication: Filtrer par niveau de publication : 'b' (Bulletin), 'r' (Rapport),
                     'l' (ECLI), 'c' (Communiqué). Valeurs via judilibre_taxonomie(id='publication').
        matiere: Filtrer par matière/thème. Valeurs via judilibre_taxonomie(id='theme').
        localisation: Filtrer par siège de juridiction (ex: 'ca_paris', 'tj33063').
                      Valeurs via judilibre_taxonomie(id='location').
        date_debut: Date de début au format YYYY-MM-DD.
        date_fin: Date de fin au format YYYY-MM-DD.
        tri: Critère de tri : 'scorepub' (pertinence + publication, défaut), 'score', 'date'.
        ordre: Ordre de tri : 'desc' (défaut) ou 'asc'.
        nb_resultats: Résultats par page (max 50, défaut 10).
        page: Numéro de page (commence à 0).
        type_fichier: Filtrer par type de documents associés : 'prep_rapp' (Rapport du rapporteur),
                      'prep_avis' (Avis de l'avocat général), 'prep_oral' (Avis oral de l'avocat
                      général), 'comm_comm' (Communiqué), 'comm_note' (Note explicative)…
        interet_particulier: Si true, restreint aux décisions d'intérêt particulier.
    """
    c = _get_client()
    params: Dict[str, Any] = {
        "query":              query,
        "operator":           operateur,
        "sort":               tri,
        "order":              ordre,
        "page_size":          min(max(1, nb_resultats), 50),
        "page":               page,
        "resolve_references": True,
    }
    if champs:
        params["field"]       = champs
    if juridiction:
        params["jurisdiction"] = juridiction
    if chambre:
        params["chamber"]     = chambre
    if formation:
        params["formation"]   = formation
    if type_decision:
        params["type"]        = type_decision
    if solution:
        params["solution"]    = solution
    if publication:
        params["publication"] = publication
    if matiere:
        params["theme"]       = matiere
    if localisation:
        params["location"]    = localisation
    if date_debut:
        params["date_start"]  = date_debut
    if date_fin:
        params["date_end"]    = date_fin
    if type_fichier:
        params["withFileOfType"] = type_fichier
    if interet_particulier is not None:
        params["particularInterest"] = interet_particulier

    data    = c._req("/search", params=params)
    total   = data.get("total", 0)
    results = data.get("results", [])
    took    = data.get("took", 0)

    if not results:
        return f"Aucun résultat pour : « {query} »"

    lines = [
        f"**{total} décision(s) pour « {query} »** — "
        f"{len(results)} affichée(s) — page {page} — {took} ms\n"
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_decision_short(r)}")
        lines.append("")

    next_page = data.get("next_page")
    if next_page:
        lines.append(f"*Page suivante disponible (page {page + 1})*")

    return "\n".join(lines)


@mcp.tool()
def judilibre_decision(
    id: str,
    query: Optional[str] = None,
    operateur: str = "or",
) -> str:
    """Récupère le texte intégral et toutes les métadonnées d'une décision par son identifiant.

    Retourne : texte complet pseudonymisé, zones structurées (introduction, exposé du litige,
    moyens, motivations, dispositif), titrage, sommaire, textes appliqués,
    rapprochements de jurisprudence et documents associés (communiqués, notes, rapports…).
    L'identifiant s'obtient via judilibre_rechercher.

    Args:
        id: Identifiant unique de la décision (ex: '5fca7d162a251e6bf9c78514').
        query: Termes à surligner dans le texte intégral retourné
               (les correspondances seront délimitées par des balises <em>).
        operateur: Opérateur logique pour le surlignage : 'or' (défaut), 'and', 'exact'.
    """
    c = _get_client()
    params: Dict[str, Any] = {
        "id":                 id,
        "resolve_references": True,
    }
    if query:
        params["query"]    = query
        params["operator"] = operateur

    data = c._req("/decision", params=params)
    if not data:
        return f"Décision `{id}` introuvable."
    return _fmt_decision_full(data)


# ===========================================================================
# OUTILS MCP — Export et taxonomie
# ===========================================================================

@mcp.tool()
def judilibre_scan(
    juridiction: Optional[List[str]] = None,
    chambre: Optional[List[str]] = None,
    formation: Optional[List[str]] = None,
    type_decision: Optional[List[str]] = None,
    solution: Optional[List[str]] = None,
    publication: Optional[List[str]] = None,
    matiere: Optional[List[str]] = None,
    localisation: Optional[List[str]] = None,
    date_debut: Optional[str] = None,
    date_fin: Optional[str] = None,
    type_date: Optional[str] = None,
    ordre: str = "asc",
    taille_lot: int = 10,
    search_after: Optional[str] = None,
    abrege: bool = False,
    type_fichier: Optional[List[str]] = None,
    interet_particulier: Optional[bool] = None,
) -> str:
    """Export par lot de décisions de justice complètes (texte intégral inclus).

    Destiné à l'indexation et la réutilisation du corpus. Remplace l'endpoint /export (déprécié).
    Retourne jusqu'à 1000 décisions par lot, navigables via search_after.
    Filtres identiques à judilibre_rechercher.

    Args:
        juridiction: Filtrer par juridiction : 'cc', 'ca', 'tj'.
        chambre: Filtrer par chambre. Valeurs via judilibre_taxonomie(id='chamber').
        formation: Filtrer par formation. Valeurs via judilibre_taxonomie(id='formation').
        type_decision: Filtrer par nature : 'arret', 'qpc', 'ordonnance'…
        solution: Filtrer par solution : 'cassation', 'rejet'…
        publication: Filtrer par niveau de publication.
        matiere: Filtrer par matière.
        localisation: Filtrer par siège de juridiction.
        date_debut: Date de début ISO-8601 (ex: '2024-01-01').
        date_fin: Date de fin ISO-8601 (ex: '2024-12-31').
        type_date: Type de date pour le filtre : 'creation' ou 'update'.
        ordre: Ordre de tri par date : 'asc' (défaut) ou 'desc'.
        taille_lot: Nombre de décisions par lot (max 1000, défaut 10).
        search_after: ID de la dernière décision du lot précédent pour paginer.
        abrege: Si true, retourne la version abrégée (sans texte intégral).
        type_fichier: Filtrer par type de document associé : 'prep_rapp', 'prep_avis',
                      'comm_comm', 'comm_note'…
        interet_particulier: Si true, restreint aux décisions d'intérêt particulier.
    """
    c = _get_client()
    params: Dict[str, Any] = {
        "order":              ordre,
        "batch_size":         min(max(1, taille_lot), 1000),
        "resolve_references": True,
        "abridged":           abrege,
    }
    if juridiction:
        params["jurisdiction"]  = juridiction
    if chambre:
        params["chamber"]       = chambre
    if formation:
        params["formation"]     = formation
    if type_decision:
        params["type"]          = type_decision
    if solution:
        params["solution"]      = solution
    if publication:
        params["publication"]   = publication
    if matiere:
        params["theme"]         = matiere
    if localisation:
        params["location"]      = localisation
    if date_debut:
        params["date_start"]    = date_debut
    if date_fin:
        params["date_end"]      = date_fin
    if type_date:
        params["date_type"]     = type_date
    if search_after:
        params["search_after"]  = search_after
    if type_fichier:
        params["withFileOfType"] = type_fichier
    if interet_particulier is not None:
        params["particularInterest"] = interet_particulier

    data    = c._req("/scan", params=params)
    total   = data.get("total", 0)
    results = data.get("results", [])
    took    = data.get("took", 0)

    if not results:
        return "Aucune décision trouvée pour ces critères."

    lines = [
        f"**{total} décision(s) au total** — "
        f"{len(results)} dans ce lot — {took} ms\n"
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_decision_short(r)}")
        lines.append("")

    next_batch = data.get("next_batch")
    if next_batch:
        last_id = results[-1].get("id", "") if results else ""
        lines.append(
            f"*Lot suivant disponible — utilisez `search_after='{last_id}'` pour continuer*"
        )

    return "\n".join(lines)


@mcp.tool()
def judilibre_taxonomie(
    id: Optional[str] = None,
    cle: Optional[str] = None,
    valeur: Optional[str] = None,
    contexte: Optional[str] = None,
) -> str:
    """Récupère les listes de termes (taxonomie) utilisés par l'API JUDILIBRE.

    Couvre : juridictions, chambres, formations, types de décision, solutions,
    niveaux de publication, matières, champs de recherche, types de fichiers,
    sièges de juridiction, etc.
    Indispensable pour connaître les valeurs valides des filtres de
    judilibre_rechercher et judilibre_scan.

    Args:
        id: Identifiant de l'entrée de taxonomie : 'type', 'jurisdiction', 'chamber',
            'formation', 'publication', 'theme', 'solution', 'field', 'zones',
            'location', 'filetype'. Sans paramètre, retourne la liste des taxonomies disponibles.
        cle: Clé dont on veut l'intitulé complet (ex: 'cc' → 'Cour de cassation'). Nécessite id.
        valeur: Intitulé dont on veut la clé (ex: 'Cour de cassation' → 'cc'). Nécessite id.
        contexte: Contextualise certaines listes : 'cc' pour Cour de cassation,
                  'ca' pour cours d'appel (défaut: 'cc').
    """
    c = _get_client()
    params: Dict[str, Any] = {}
    if id:
        params["id"] = id
    if cle:
        params["key"] = cle
    if valeur:
        params["value"] = valeur
    if contexte:
        params["context_value"] = contexte

    data = c._req("/taxonomy", params=params or None)

    # Résolution d'une clé ou d'une valeur spécifique
    if cle or valeur:
        result = data.get("result", {})
        if result:
            k = result.get("key", data.get("key", cle or ""))
            v = result.get("value", "")
            return f"**{k}** → {v}"
        return "Aucun résultat de taxonomie trouvé."

    # Liste complète d'une taxonomie
    if id:
        items = data.get("result", data.get("results", data))
        if isinstance(items, list):
            lines = [f"**Taxonomie '{id}'** ({len(items)} entrée(s))\n"]
            for item in items:
                if isinstance(item, dict):
                    k = item.get("key", "")
                    v = item.get("value", "")
                    lines.append(f"- `{k}` — {v}")
                else:
                    lines.append(f"- {item}")
            return "\n".join(lines)
        elif isinstance(items, dict):
            lines = [f"**Taxonomie '{id}'**\n"]
            for k, v in items.items():
                lines.append(f"- `{k}` — {v}")
            return "\n".join(lines)

    # Liste des taxonomies disponibles (sans paramètre)
    if isinstance(data, list):
        lines = [f"**Taxonomies disponibles ({len(data)})**\n"]
        for item in data:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('key', item.get('id', ''))}` — {item.get('value', '')}")
            else:
                lines.append(f"- {item}")
        return "\n".join(lines)

    # Fallback : retourner brut
    return str(data)


# ===========================================================================
# OUTILS MCP — Statistiques et historique
# ===========================================================================

@mcp.tool()
def judilibre_stats(
    juridiction: Optional[str] = None,
    localisation: Optional[str] = None,
    date_debut: Optional[str] = None,
    date_fin: Optional[str] = None,
    agregations: Optional[str] = None,
    interet_particulier: Optional[str] = None,
) -> str:
    """Récupère des statistiques sur le contenu de la base JUDILIBRE.

    Retourne : nombre total de décisions indexées, dates extrêmes,
    répartition par juridiction, chambre, année, solution, matière, etc.
    Les statistiques sont mises à jour quotidiennement.

    Args:
        juridiction: Filtrer par type de juridiction : 'cc', 'ca', 'tj', 'tcom'.
        localisation: Filtrer par juridiction précise (ex: 'ca_paris,ca_rennes').
        date_debut: Date minimale au format YYYY-MM-DD.
        date_fin: Date maximale au format YYYY-MM-DD.
        agregations: Variables d'agrégation séparées par des virgules parmi :
                     year, month, jurisdiction, source, location, theme,
                     formation, chamber, solution, type, publication.
                     Ex: 'jurisdiction,chamber' ou 'year,solution'.
        interet_particulier: Filtrer sur l'intérêt particulier : 'true'.
    """
    c = _get_client()
    params: Dict[str, Any] = {}
    if juridiction:
        params["jurisdiction"]       = juridiction
    if localisation:
        params["location"]           = localisation
    if date_debut:
        params["date_start"]         = date_debut
    if date_fin:
        params["date_end"]           = date_fin
    if agregations:
        params["keys"]               = agregations
    if interet_particulier:
        params["particularInterest"] = interet_particulier

    data    = c._req("/stats", params=params or None)
    results = data.get("results", {})
    query   = data.get("query", {})

    lines = ["**Statistiques JUDILIBRE**\n"]

    # Contexte de la requête
    if query:
        ctx = []
        if query.get("jurisdiction"):
            ctx.append(f"juridiction : {query['jurisdiction']}")
        if query.get("keys"):
            ctx.append(f"agrégation : {query['keys']}")
        if ctx:
            lines.append(f"*Filtres : {', '.join(ctx)}*\n")

    # Chiffres globaux
    total = results.get("total_decisions", 0)
    min_d = results.get("min_decision_date", "")
    max_d = results.get("max_decision_date", "")
    lines.append(f"**Total décisions indexées** : {total:,}".replace(",", "\u202f"))
    if min_d:
        lines.append(f"**Décision la plus ancienne** : {min_d}")
    if max_d:
        lines.append(f"**Décision la plus récente** : {max_d}")

    # Agrégations
    agg = results.get("aggregated_data", [])
    if agg:
        lines.append(f"\n**Répartition ({len(agg)} bucket(s))**\n")
        for bucket in agg[:50]:
            key   = bucket.get("key", {})
            count = bucket.get("decisions_count", 0)
            label = " | ".join(f"{k}: {v}" for k, v in key.items())
            lines.append(f"- {label} → **{count}** décision(s)")
        if len(agg) > 50:
            lines.append(f"*… {len(agg) - 50} bucket(s) supplémentaire(s)*")

    return "\n".join(lines)


@mcp.tool()
def judilibre_historique(
    date: str,
    taille_page: Optional[int] = None,
    from_id: Optional[str] = None,
) -> str:
    """Consulte l'historique transactionnel de la base JUDILIBRE.

    Liste les opérations create, update et delete effectuées sur les décisions
    depuis une date donnée. Destiné aux réutilisateurs souhaitant maintenir
    leur propre index à jour en synchronisant les créations, modifications et suppressions.

    Args:
        date: Date à partir de laquelle remonter les opérations,
              au format ISO-8601 (ex: '2024-01-01T00:00:00Z').
        taille_page: Nombre d'opérations par requête (10 minimum, 500 maximum, défaut 500).
        from_id: ID à partir duquel continuer la pagination
                 (fourni par la réponse précédente via next_page).
    """
    c = _get_client()
    params: Dict[str, Any] = {"date": date}
    if taille_page is not None:
        params["page_size"] = max(10, min(500, taille_page))
    if from_id:
        params["from_id"] = from_id

    data         = c._req("/transactionalhistory", params=params)
    transactions = data.get("transactions", [])
    total        = data.get("total", 0)
    query_date   = data.get("query_date", "")
    next_page    = data.get("next_page", "")

    if not transactions:
        return f"Aucune opération trouvée depuis le {date}."

    # Comptage par type d'opération
    counts: Dict[str, int] = {}
    for t in transactions:
        action = t.get("action", "unknown")
        counts[action] = counts.get(action, 0) + 1

    lines = [
        f"**{total} opération(s) depuis le {date}** — "
        f"{len(transactions)} affichée(s) — requête du {query_date}\n"
    ]

    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    lines.append(f"*Répartition : {summary}*\n")

    for t in transactions[:100]:
        action = t.get("action", "")
        tid    = t.get("id", "")
        tdate  = t.get("date", "")
        icon   = {"created": "✅", "updated": "🔄", "deleted": "🗑️"}.get(action, "•")
        lines.append(f"{icon} **{action}** — `{tid}` — {tdate}")

    if len(transactions) > 100:
        lines.append(f"\n*… {len(transactions) - 100} opération(s) supplémentaire(s) non affichée(s)*")

    if next_page:
        lines.append(
            f"\n*Suite disponible — utilisez `from_id` extrait du paramètre next_page*"
        )

    return "\n".join(lines)


# ===========================================================================
# OUTILS MCP — Export (pagination offset) et healthcheck
# ===========================================================================

@mcp.tool()
def judilibre_export(
    juridiction: Optional[List[str]] = None,
    chambre: Optional[List[str]] = None,
    formation: Optional[List[str]] = None,
    type_decision: Optional[List[str]] = None,
    solution: Optional[List[str]] = None,
    publication: Optional[List[str]] = None,
    matiere: Optional[List[str]] = None,
    localisation: Optional[List[str]] = None,
    date_debut: Optional[str] = None,
    date_fin: Optional[str] = None,
    type_date: Optional[str] = None,
    ordre: str = "asc",
    taille_lot: int = 10,
    lot: int = 0,
    abrege: bool = False,
    type_fichier: Optional[List[str]] = None,
    interet_particulier: Optional[bool] = None,
) -> str:
    """Export par lot de décisions via pagination par numéro de lot (offset).

    Contrairement à judilibre_scan (qui pagine par curseur via search_after),
    cet outil utilise un numéro de lot entier (`lot`) pour naviguer dans les résultats.
    Retourne jusqu'à 1000 décisions par lot, texte intégral inclus.
    Filtres identiques à judilibre_rechercher et judilibre_scan.

    Args:
        juridiction: Filtrer par juridiction : 'cc', 'ca', 'tj'.
        chambre: Filtrer par chambre. Valeurs via judilibre_taxonomie(id='chamber').
        formation: Filtrer par formation. Valeurs via judilibre_taxonomie(id='formation').
        type_decision: Filtrer par nature : 'arret', 'qpc', 'ordonnance'…
        solution: Filtrer par solution : 'cassation', 'rejet'…
        publication: Filtrer par niveau de publication.
        matiere: Filtrer par matière. Valeurs via judilibre_taxonomie(id='theme').
        localisation: Filtrer par siège de juridiction.
        date_debut: Date de début au format ISO-8601 (ex: '2024-01-01').
        date_fin: Date de fin au format ISO-8601 (ex: '2024-12-31').
        type_date: Type de date pour le filtre : 'creation' ou 'update'.
        ordre: Ordre de tri par date : 'asc' (défaut) ou 'desc'.
        taille_lot: Nombre de décisions par lot (max 1000, défaut 10).
        lot: Numéro du lot à retourner (le premier lot vaut 0).
        abrege: Si true, retourne la version abrégée (sans texte intégral ni métadonnées détaillées).
        type_fichier: Filtrer par type de document associé : 'prep_rapp', 'prep_avis',
                      'prep_oral', 'comm_comm', 'comm_note'…
        interet_particulier: Si true, restreint aux décisions d'intérêt particulier.
    """
    c = _get_client()
    params: Dict[str, Any] = {
        "order":              ordre,
        "batch_size":         min(max(1, taille_lot), 1000),
        "batch":              max(0, lot),
        "resolve_references": True,
        "abridged":           abrege,
    }
    if juridiction:
        params["jurisdiction"]   = juridiction
    if chambre:
        params["chamber"]        = chambre
    if formation:
        params["formation"]      = formation
    if type_decision:
        params["type"]           = type_decision
    if solution:
        params["solution"]       = solution
    if publication:
        params["publication"]    = publication
    if matiere:
        params["theme"]          = matiere
    if localisation:
        params["location"]       = localisation
    if date_debut:
        params["date_start"]     = date_debut
    if date_fin:
        params["date_end"]       = date_fin
    if type_date:
        params["date_type"]      = type_date
    if type_fichier:
        params["withFileOfType"] = type_fichier
    if interet_particulier is not None:
        params["particularInterest"] = interet_particulier

    data    = c._req("/export", params=params)
    total   = data.get("total", 0)
    results = data.get("results", [])
    took    = data.get("took", 0)

    if not results:
        return "Aucune décision trouvée pour ces critères."

    lines = [
        f"**{total} décision(s) au total** — "
        f"{len(results)} dans ce lot (lot n°{lot}) — {took} ms\n"
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_decision_short(r)}")
        lines.append("")

    nb_lots = (total + taille_lot - 1) // taille_lot if taille_lot else 1
    if lot + 1 < nb_lots:
        lines.append(f"*Lot suivant disponible — utilisez `lot={lot + 1}` pour continuer*")

    return "\n".join(lines)


@mcp.tool()
def judilibre_healthcheck() -> str:
    """Vérifie que l'API JUDILIBRE est opérationnelle.

    Retourne le statut de disponibilité du service.
    Utile pour diagnostiquer les problèmes de connectivité avant d'effectuer des recherches.
    """
    c = _get_client()
    data = c._req("/healthcheck")
    status = data.get("status", data.get("healthy", str(data)))
    return f"**JUDILIBRE healthcheck** : {status}"


# ===========================================================================
# Point d'entrée
# ===========================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serveur MCP HTTP Streamable — API JUDILIBRE")
    parser.add_argument("--host", default="0.0.0.0", help="Adresse d'écoute (défaut: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=6501, help="Port d'écoute (défaut: 6501)")
    parser.add_argument("--path", default="/mcp", help="Chemin MCP (défaut: /mcp)")
    args = parser.parse_args()

    mcp.settings.host                 = args.host
    mcp.settings.port                 = args.port
    mcp.settings.streamable_http_path = args.path

    logger.info(f"🚀 Démarrage du serveur MCP JUDILIBRE sur http://{args.host}:{args.port}{args.path}")
    logger.info("   8 outils disponibles — spec MCP 2025-03-26 HTTP Streamable")
    logger.info("   Prérequis : JUDILIBRE_CLIENT_ID et JUDILIBRE_CLIENT_SECRET dans l'environnement")

    mcp.settings.stateless_http = True

    mcp.run(transport="streamable-http")
