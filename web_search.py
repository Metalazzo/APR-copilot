"""Recherche web pour l'etat de l'art — pilotee par l'orchestrateur.

Le LLM n'appelle jamais d'outil de recherche : c'est l'orchestrateur qui
cherche AVANT de construire la tache et qui injecte les resultats comme
contexte (pattern fiable, cf. v1.3.7 sur la fragilite de la boucle d'outils).

Backends :
- ddg     : DuckDuckGo via le paquet `ddgs` (sans cle)
- searxng : instance auto-hebergee (anonymisee) avec `json` dans search.formats
- tavily  : API cloud (cle requise)

Configuration via variables d'environnement, relues A CHAQUE appel (la GUI
peut basculer en live) :
  WEB_SEARCH_ENABLED      false (defaut) — confidentialite : les requetes
                          partent vers l'exterieur
  WEB_SEARCH_BACKEND      ddg (defaut) | searxng | tavily
  WEB_SEARCH_MAX_RESULTS  5
  WEB_SEARCH_TIMEOUT      10 (secondes)
  SEARXNG_URL             ex. http://localhost:8888
  TAVILY_API_KEY
"""

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class WebSearchConfig:
    enabled: bool
    backend: str
    max_results: int
    timeout: float
    searxng_url: str
    tavily_api_key: str


def get_web_config() -> WebSearchConfig:
    """Lit la configuration depuis l'environnement a chaque appel."""
    truthy = ("1", "true", "yes")
    return WebSearchConfig(
        enabled=os.getenv("WEB_SEARCH_ENABLED", "false").lower() in truthy,
        backend=os.getenv("WEB_SEARCH_BACKEND", "ddg").strip().lower(),
        max_results=max(1, min(20, int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")))),
        timeout=float(os.getenv("WEB_SEARCH_TIMEOUT", "10")),
        searxng_url=os.getenv("SEARXNG_URL", "").strip().rstrip("/"),
        tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
    )


def _http_json(url: str, timeout: float, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload else None,
        headers={"User-Agent": "Mozilla/5.0 (compatible; APR-Copilot/1.0)"},
        method="POST" if payload else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _norm(r: dict) -> dict:
    """Normalise un resultat de recherche : {title, url, snippet}."""
    return {
        "title": (r.get("title") or "").strip()[:200],
        "url": (r.get("url") or r.get("href") or "").strip(),
        "snippet": (r.get("snippet") or r.get("content") or r.get("body") or r.get("description") or "").strip()[:400],
    }


def _search_ddg(query: str, cfg: WebSearchConfig) -> list[dict]:
    from ddgs import DDGS  # import paresseux : dependance optionnelle

    out = []
    with DDGS(timeout=cfg.timeout) as ddgs:
        for r in ddgs.text(query, max_results=cfg.max_results):
            item = _norm(r)
            if item["url"]:
                out.append(item)
    return out


def _search_searxng(query: str, cfg: WebSearchConfig) -> list[dict]:
    if not cfg.searxng_url:
        raise ValueError("SEARXNG_URL non configuree (instance auto-hebergee requise)")
    url = f"{cfg.searxng_url}/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json"}
    )
    data = _http_json(url, cfg.timeout)
    out = []
    for r in data.get("results", [])[: cfg.max_results]:
        item = _norm(r)
        if item["url"]:
            out.append(item)
    return out


def _search_tavily(query: str, cfg: WebSearchConfig) -> list[dict]:
    if not cfg.tavily_api_key:
        raise ValueError("TAVILY_API_KEY non configuree")
    data = _http_json(
        "https://api.tavily.com/search",
        cfg.timeout,
        payload={
            "api_key": cfg.tavily_api_key,
            "query": query,
            "max_results": cfg.max_results,
        },
    )
    out = []
    for r in data.get("results", []):
        item = _norm(r)
        if item["url"]:
            out.append(item)
    return out


_BACKENDS = {"ddg": _search_ddg, "searxng": _search_searxng, "tavily": _search_tavily}


def search_web(query: str) -> list[dict]:
    """Recherche web via le backend configure. Leve une exception en cas
    d'echec : l'appelant (orchestrateur) degrade proprement."""
    cfg = get_web_config()
    if cfg.backend not in _BACKENDS:
        raise ValueError(
            f"Backend de recherche web inconnu : {cfg.backend!r} "
            f"(attendus : ddg, searxng, tavily)"
        )
    return _BACKENDS[cfg.backend](query, cfg)
