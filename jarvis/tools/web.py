"""Outils internet (désactivables : [securite] internet = false, ou le mode confidentiel).

Seules des recherches partent sur le réseau, jamais tes conversations. Chaque
requête est comptée et affichée dans le HUD. Le contenu récupéré est encadré
comme « donnée externe » pour résister aux injections de prompt.
"""

from __future__ import annotations

import io
import ipaddress
import socket
from urllib.parse import quote, urljoin, urlparse

import requests

from jarvis.security import envelopper_externe
from jarvis.textutils import html_vers_texte, tronquer
from jarvis.tools import Contexte, outil

AGENT = "JarvisLocal/1.0 (assistant personnel open source)"
_session = requests.Session()
_session.headers["User-Agent"] = AGENT

METEO_CODES = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant", 51: "bruine légère", 53: "bruine", 55: "bruine dense",
    56: "bruine verglaçante", 57: "bruine verglaçante dense", 61: "pluie faible", 63: "pluie modérée",
    65: "forte pluie", 66: "pluie verglaçante", 67: "forte pluie verglaçante", 71: "neige faible",
    73: "neige modérée", 75: "forte neige", 77: "grains de neige", 80: "averses faibles", 81: "averses",
    82: "violentes averses", 85: "averses de neige", 86: "fortes averses de neige", 95: "orage",
    96: "orage avec grêle", 99: "orage avec forte grêle",
}


def _get(ctx: Contexte, url: str, **kwargs) -> requests.Response:
    ctx.requetes_internet += 1
    if ctx.bus:
        ctx.bus.publier("reseau", url=url.split("?")[0], total=ctx.requetes_internet)
    r = _session.get(url, timeout=kwargs.pop("timeout", 10), **kwargs)
    r.raise_for_status()
    return r


def _nombre(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}".replace(".", ",")


@outil(
    description=("Météo actuelle et prévisions (heure par heure pour les prochaines heures, puis par jour). "
                 "Sans ville précisée, utilise la ville de l'utilisateur."),
    params={"ville": "Nom de la ville (vide = ville de l'utilisateur).",
            "jours": "Nombre de jours de prévision (1 à 7)."},
    categorie="web", reseau=True, icone="☁", libelle="météo",
)
def meteo(ctx: Contexte, ville: str = "", jours: int = 2) -> str:
    ville = ville.strip() or ctx.config.general.ville
    jours = max(1, min(7, jours))
    geo = _get(ctx, "https://geocoding-api.open-meteo.com/v1/search",
               params={"name": ville, "count": 1, "language": "fr", "format": "json"}).json()
    if not geo.get("results"):
        return f"Ville « {ville} » introuvable."
    lieu = geo["results"][0]
    p = _get(ctx, "https://api.open-meteo.com/v1/forecast", params={
        "latitude": lieu["latitude"], "longitude": lieu["longitude"], "timezone": "auto",
        "forecast_days": jours, "wind_speed_unit": "kmh",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }).json()
    c = p["current"]
    nom = ", ".join(x for x in (lieu.get("name"), lieu.get("admin1"), lieu.get("country")) if x)
    lignes = [
        f"{nom}. Maintenant ({c['time'][-5:]}) : {_nombre(c['temperature_2m'])} °C "
        f"(ressenti {_nombre(c['apparent_temperature'])} °C), {METEO_CODES.get(c['weather_code'], 'temps inconnu')}, "
        f"vent {_nombre(c['wind_speed_10m'])} km/h, humidité {c['relative_humidity_2m']} %."
    ]
    h = p.get("hourly", {})
    heures = h.get("time", [])
    if heures:
        # Les 12 prochaines heures par tranches de 3 h : on garde la pluie la plus probable et le
        # temps le plus marqué de chaque tranche, pour ne jamais rater une averse entre deux relevés.
        debut = next((i for i, t in enumerate(heures) if t >= c["time"]), 0)
        tranches = []
        for d in range(debut, min(debut + 12, len(heures)), 3):
            idx = range(d, min(d + 3, len(heures)))
            temps = [h["temperature_2m"][i] for i in idx]
            pluie = max((h["precipitation_probability"][i] or 0) for i in idx)
            code = max(h["weather_code"][i] for i in idx)
            t_min, t_max = min(temps), max(temps)
            plage = f"{_nombre(t_min)} °C" if t_max - t_min < 1 else f"{_nombre(t_min)} à {_nombre(t_max)} °C"
            heure = int(heures[d][11:13])
            tranches.append(f"{heure} h-{(heure + len(idx)) % 24} h : {plage}, {METEO_CODES.get(code, '?')}"
                            + (f", pluie {pluie} %" if pluie >= 20 else ""))
        lignes.append("Prochaines heures : " + " · ".join(tranches) + ".")
    d = p.get("daily", {})
    for i, jour in enumerate(d.get("time", [])):
        lignes.append(
            f"{jour} : {_nombre(d['temperature_2m_min'][i])} à {_nombre(d['temperature_2m_max'][i])} °C, "
            f"{METEO_CODES.get(d['weather_code'][i], '?')}, risque de pluie {d['precipitation_probability_max'][i]} %."
        )
    return "\n".join(lignes)


@outil(
    description="Résumé encyclopédique d'un sujet depuis Wikipédia en français.",
    params={"sujet": "Sujet, personne, lieu ou notion à rechercher."},
    categorie="web", reseau=True, externe=True, icone="W", libelle="wikipédia",
)
def wikipedia(ctx: Contexte, sujet: str) -> str:
    recherche = _get(ctx, "https://fr.wikipedia.org/w/api.php", params={
        "action": "query", "list": "search", "srsearch": sujet, "srlimit": 1, "format": "json", "utf8": 1,
    }).json()
    resultats = recherche.get("query", {}).get("search", [])
    if not resultats:
        return f"Aucun article Wikipédia pour « {sujet} »."
    titre = resultats[0]["title"]
    resume = _get(ctx, "https://fr.wikipedia.org/api/rest_v1/page/summary/" + quote(titre.replace(" ", "_"))).json()
    texte = resume.get("extract", "")
    lien = resume.get("content_urls", {}).get("desktop", {}).get("page", "")
    return envelopper_externe("wikipedia", f"{titre} — {resume.get('description', '')}\n{tronquer(texte, 1800)}\n{lien}")


@outil(
    description="Recherche sur le web (DuckDuckGo) pour une information récente ou introuvable ailleurs.",
    params={"requete": "Requête courte, en mots-clés.", "nombre": "Nombre de résultats (1 à 8)."},
    categorie="web", reseau=True, externe=True, icone="⌖", libelle="recherche web",
)
def recherche_web(ctx: Contexte, requete: str, nombre: int = 5) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        return "La recherche web demande le module « ddgs » (uv sync l'installe)."
    ctx.requetes_internet += 1
    if ctx.bus:
        ctx.bus.publier("reseau", url="duckduckgo.com", total=ctx.requetes_internet)
    resultats = DDGS().text(requete, region="fr-fr", safesearch="moderate", max_results=max(1, min(8, nombre)))
    if not resultats:
        return f"Aucun résultat pour « {requete} »."
    lignes = [f"{i}. {r.get('title', '')}\n   {r.get('href', '')}\n   {tronquer(r.get('body', ''), 300)}"
              for i, r in enumerate(resultats, 1)]
    return envelopper_externe("recherche web", "\n".join(lignes))


def adresse_publique(url: str) -> bool:
    """Protection SSRF : on refuse d'aller lire une adresse locale ou privée
    (ta box, Ollama, un service interne…), même si une page web le demande."""
    hote = urlparse(url).hostname
    if not hote:
        return False
    try:
        adresses = {info[4][0] for info in socket.getaddrinfo(hote, None)}
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(a.split("%")[0]).is_global for a in adresses)


@outil(
    description="Lit le contenu texte d'une page web (article, documentation) à partir de son adresse.",
    params={"url": "Adresse complète commençant par https://"},
    categorie="web", reseau=True, externe=True, icone="⇲", libelle="lire une page",
)
def lire_page_web(ctx: Contexte, url: str) -> str:
    for _ in range(4):  # on suit les redirections nous-mêmes pour vérifier chaque étape
        if urlparse(url).scheme not in ("http", "https"):
            return "Seules les adresses http(s) sont acceptées."
        if not adresse_publique(url):
            return "Adresse refusée : elle pointe vers le réseau local ou une machine privée."
        ctx.requetes_internet += 1
        if ctx.bus:
            ctx.bus.publier("reseau", url=url.split("?")[0], total=ctx.requetes_internet)
        r = _session.get(url, timeout=12, stream=True, allow_redirects=False)
        if r.is_redirect or r.is_permanent_redirect:
            url = urljoin(url, r.headers.get("Location", ""))
            r.close()
            continue
        break
    else:
        return "Trop de redirections."
    r.raise_for_status()
    contenu = io.BytesIO()
    for bloc in r.iter_content(65536):
        contenu.write(bloc)
        if contenu.tell() > 3_000_000:
            break
    r.close()
    type_ = r.headers.get("Content-Type", "")
    if "pdf" in type_:
        from pypdf import PdfReader
        lecteur = PdfReader(io.BytesIO(contenu.getvalue()))
        titre, texte = url, "\n".join((p.extract_text() or "") for p in lecteur.pages[:30])
    else:
        brut = contenu.getvalue().decode(r.encoding or "utf-8", errors="replace")
        titre, texte = html_vers_texte(brut) if "html" in type_ or "<html" in brut[:500].lower() else (url, brut)
    return envelopper_externe(url, f"{titre}\n\n{tronquer(texte, 7000)}")
