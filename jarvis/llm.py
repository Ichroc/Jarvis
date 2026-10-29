"""Client minimal pour l'API locale d'Ollama.

On parle directement HTTP plutôt que d'utiliser une bibliothèque : c'est
court, sans magie, et tu vois exactement ce qui passe entre Jarvis et le
modèle. Tout reste sur 127.0.0.1 : rien ne sort de la machine.

Routes utilisées :
  POST /api/chat           conversation en flux (texte, réflexion, appels d'outils)
  POST /api/embed          vecteurs pour la mémoire et la recherche de documents
  POST /v1/audio/speech    synthèse vocale (modèles « speech » comme Phonon)
  GET  /api/version, /api/tags, /api/ps ; POST /api/show
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator

import requests


class OllamaErreur(RuntimeError):
    """Erreur renvoyée par Ollama."""


class OllamaIndisponible(OllamaErreur):
    """Ollama ne répond pas (application fermée ?)."""


class ModeleIntrouvable(OllamaErreur):
    def __init__(self, modele: str) -> None:
        super().__init__(f"Le modèle « {modele} » n'est pas installé. Lance : ollama pull {modele}")
        self.modele = modele


@dataclass
class AppelOutil:
    nom: str
    arguments: dict
    ident: str | None = None

    @classmethod
    def depuis(cls, brut: dict) -> "AppelOutil":
        fonction = brut.get("function") or {}
        args = fonction.get("arguments") or {}
        if isinstance(args, str):  # certains modèles renvoient du JSON en texte
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        return cls(nom=str(fonction.get("name", "")), arguments=args, ident=brut.get("id"))

    def en_message(self) -> dict:
        m: dict = {"type": "function", "function": {"name": self.nom, "arguments": self.arguments}}
        if self.ident:
            m["id"] = self.ident
        return m


@dataclass
class Morceau:
    """Un morceau de réponse reçu en flux."""
    contenu: str = ""
    pensee: str = ""
    appels: list[AppelOutil] = field(default_factory=list)
    fini: bool = False
    stats: dict | None = None


def _stats(d: dict) -> dict:
    """Ollama donne les durées en nanosecondes ; on calcule aussi le débit."""
    s = {k: d.get(k, 0) or 0 for k in (
        "total_duration", "load_duration", "prompt_eval_count",
        "prompt_eval_duration", "eval_count", "eval_duration",
    )}
    s["jetons_par_s"] = round(s["eval_count"] / (s["eval_duration"] / 1e9), 1) if s["eval_duration"] else 0.0
    s["lecture_par_s"] = (
        round(s["prompt_eval_count"] / (s["prompt_eval_duration"] / 1e9), 1) if s["prompt_eval_duration"] else 0.0
    )
    return s


class Ollama:
    def __init__(self, hote: str = "http://127.0.0.1:11434", delai: float = 600.0) -> None:
        self.hote = hote.rstrip("/")
        self.delai = delai
        self.session = requests.Session()
        # Jamais de proxy pour parler à un service local.
        self.session.trust_env = False
        self._infos: dict[str, dict] = {}

    # ------------------------------------------------------------------ bas niveau
    def _requete(self, methode: str, chemin: str, *, corps: dict | None = None,
                 flux: bool = False, delai: float | None = None, modele: str | None = None):
        try:
            r = self.session.request(
                methode, f"{self.hote}{chemin}", json=corps, stream=flux,
                timeout=(3.0, delai or self.delai),
            )
        except requests.ConnectionError as e:
            raise OllamaIndisponible(
                f"Ollama ne répond pas sur {self.hote}. Ouvre l'application Ollama (ou lance « ollama serve »)."
            ) from e
        except requests.Timeout as e:
            raise OllamaErreur("Ollama met trop de temps à répondre.") from e
        if r.status_code >= 400:
            message = _message_erreur(r)
            r.close()
            if r.status_code == 404 and modele and "not found" in message.lower():
                raise ModeleIntrouvable(modele)
            raise OllamaErreur(f"Ollama a répondu {r.status_code} : {message}")
        return r

    # ------------------------------------------------------------------ informations
    def version(self) -> str:
        return self._requete("GET", "/api/version", delai=5).json().get("version", "?")

    def modeles(self) -> list[dict]:
        return self._requete("GET", "/api/tags", delai=10).json().get("models", [])

    def noms_modeles(self) -> set[str]:
        noms = set()
        for m in self.modeles():
            nom = m.get("name") or m.get("model") or ""
            noms.add(nom)
            if nom.endswith(":latest"):
                noms.add(nom[: -len(":latest")])
        return noms

    def charges(self) -> list[dict]:
        """Modèles actuellement en mémoire (et combien ils occupent)."""
        return self._requete("GET", "/api/ps", delai=5).json().get("models", [])

    def infos(self, modele: str) -> dict:
        if modele not in self._infos:
            self._infos[modele] = self._requete("POST", "/api/show", corps={"model": modele},
                                                delai=15, modele=modele).json()
        return self._infos[modele]

    def capacites(self, modele: str) -> set[str]:
        """Par exemple {"completion", "tools", "thinking", "vision"} ou {"speech"}."""
        return set(self.infos(modele).get("capabilities") or [])

    def voix(self, modele: str) -> list[str]:
        brut = self.infos(modele).get("voices") or []
        noms = []
        for v in brut:
            noms.append(v.get("name", "") if isinstance(v, dict) else str(v))
        return [n for n in noms if n]

    # ------------------------------------------------------------------ conversation
    def discuter(self, modele: str, messages: list[dict], *, outils: list[dict] | None = None,
                 reflexion: bool | None = None, options: dict | None = None,
                 garder: str | None = None) -> Iterator[Morceau]:
        """Envoie la conversation et renvoie la réponse morceau par morceau."""
        corps: dict = {"model": modele, "messages": messages, "stream": True}
        if outils:
            corps["tools"] = outils
        if reflexion is not None:
            corps["think"] = reflexion
        if options:
            corps["options"] = options
        if garder:
            corps["keep_alive"] = garder
        r = self._requete("POST", "/api/chat", corps=corps, flux=True, modele=modele)
        # Si l'appelant s'arrête (interruption), la connexion se ferme et Ollama
        # arrête de générer : aucune ressource gaspillée.
        with r:
            for ligne in r.iter_lines():
                if not ligne:
                    continue
                d = json.loads(ligne)
                if "error" in d:
                    raise OllamaErreur(str(d["error"]))
                msg = d.get("message") or {}
                fini = bool(d.get("done"))
                yield Morceau(
                    contenu=msg.get("content") or "",
                    pensee=msg.get("thinking") or "",
                    appels=[AppelOutil.depuis(a) for a in (msg.get("tool_calls") or [])],
                    fini=fini,
                    stats=_stats(d) if fini else None,
                )

    def demander(self, modele: str, messages: list[dict], **kwargs) -> str:
        """Version non interactive : renvoie le texte complet."""
        return "".join(m.contenu for m in self.discuter(modele, messages, **kwargs))

    # ------------------------------------------------------------------ vecteurs
    def vecteurs(self, modele: str, textes: list[str], garder: str = "10m") -> list[list[float]]:
        r = self._requete("POST", "/api/embed", corps={"model": modele, "input": textes, "keep_alive": garder},
                          delai=120, modele=modele)
        return r.json().get("embeddings", [])

    # ------------------------------------------------------------------ voix
    def parler(self, modele: str, texte: str, voix: str | None = None) -> Iterator[bytes]:
        """Synthèse vocale en flux : PCM 16 bits, mono, 24 kHz, dès les premiers mots."""
        corps = {"model": modele, "input": texte[:4000], "response_format": "pcm"}
        if voix:
            corps["voice"] = voix
        r = self._requete("POST", "/v1/audio/speech", corps=corps, flux=True, delai=60, modele=modele)
        with r:
            for bloc in r.iter_content(chunk_size=4800):
                if bloc:
                    yield bloc


def _message_erreur(r: requests.Response) -> str:
    try:
        d = r.json()
        if isinstance(d, dict):
            err = d.get("error")
            if isinstance(err, dict):
                return str(err.get("message", err))
            if err:
                return str(err)
    except ValueError:
        pass
    return (r.text or r.reason or "").strip()[:300]
