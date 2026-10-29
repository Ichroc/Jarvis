"""Un faux serveur Ollama pour les tests : il parle exactement le même protocole HTTP
(flux NDJSON pour /api/chat, PCM pour /v1/audio/speech) avec des réponses scénarisées."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def vecteur_texte(texte: str, dim: int = 256) -> list[float]:
    """Embedding déterministe : sac de mots haché (deux textes qui partagent des mots se ressemblent)."""
    if "\nQuery: " in texte:  # consigne des modèles Qwen3-Embedding : on ne garde que la question
        texte = texte.split("\nQuery: ", 1)[1]
    v = [0.0] * dim
    for mot in texte.lower().replace(",", " ").replace(".", " ").replace("'", " ").split():
        if len(mot) < 3:
            continue
        h = int(hashlib.md5(mot.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def reponse_texte(texte: str, morceaux: int = 4) -> list[dict]:
    pas = max(1, len(texte) // morceaux)
    parts = [texte[i:i + pas] for i in range(0, len(texte), pas)]
    lignes = [{"message": {"role": "assistant", "content": p}, "done": False} for p in parts]
    lignes.append({"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                   "total_duration": 900_000_000, "load_duration": 1_000_000, "prompt_eval_count": 120,
                   "prompt_eval_duration": 100_000_000, "eval_count": 40, "eval_duration": 800_000_000})
    return lignes


def reponse_outil(nom: str, arguments, *, texte_avant: str = "", ident: str | None = None) -> list[dict]:
    appel = {"function": {"index": 0, "name": nom, "arguments": arguments}}
    if ident:
        appel["id"] = ident
    lignes = []
    if texte_avant:
        lignes.append({"message": {"role": "assistant", "content": texte_avant}, "done": False})
    lignes.append({"message": {"role": "assistant", "content": "", "tool_calls": [appel]}, "done": False})
    lignes.append({"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                   "eval_count": 12, "eval_duration": 300_000_000, "prompt_eval_count": 200,
                   "prompt_eval_duration": 150_000_000})
    return lignes


class FauxOllama:
    def __init__(self, port: int = 0) -> None:
        self.scenarios: deque[list[dict]] = deque()
        self.requetes: list[dict] = []
        self.modeles = {
            "qwen3.5:9b": ["completion", "tools", "thinking", "vision"],
            "qwen3-embedding:0.6b": ["embedding"],
            "phonon:fr": ["speech"],
        }
        self.voix = ["Augustin", "Lucien", "Noémie"]
        self.delai_morceau = 0.0
        self.version = "0.40.11"
        self.erreur_chat: str | None = None  # simule une panne d'Ollama sur /api/chat
        serveur = self

        class Gestionnaire(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _json(self, code: int, donnees) -> None:
                corps = json.dumps(donnees).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)

            def _corps(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                if self.path == "/api/version":
                    return self._json(200, {"version": serveur.version})
                if self.path == "/api/tags":
                    return self._json(200, {"models": [{"name": n if ":" in n else f"{n}:latest"} for n in serveur.modeles]})
                if self.path == "/api/ps":
                    return self._json(200, {"models": [{"name": "qwen3.5:9b", "size_vram": 7_100_000_000}]})
                self._json(404, {"error": "not found"})

            def do_POST(self):
                corps = self._corps()
                serveur.requetes.append({"chemin": self.path, "corps": corps})
                modele = corps.get("model")
                if self.path == "/api/show":
                    if modele not in serveur.modeles:
                        return self._json(404, {"error": f"model '{modele}' not found"})
                    infos = {"capabilities": serveur.modeles[modele]}
                    if "speech" in serveur.modeles[modele]:
                        infos["voices"] = serveur.voix
                    return self._json(200, infos)
                if self.path == "/api/embed":
                    if modele not in serveur.modeles:
                        return self._json(404, {"error": f"model '{modele}' not found"})
                    entrees = corps["input"] if isinstance(corps["input"], list) else [corps["input"]]
                    return self._json(200, {"model": modele, "embeddings": [vecteur_texte(t) for t in entrees]})
                if self.path == "/api/chat":
                    if modele not in serveur.modeles:
                        return self._json(404, {"error": f"model '{modele}' not found"})
                    if serveur.erreur_chat:
                        return self._json(500, {"error": serveur.erreur_chat})
                    lignes = serveur.scenarios.popleft() if serveur.scenarios else reponse_texte("D'accord.")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        for ligne in lignes:
                            donnees = (json.dumps({"model": modele, **ligne}) + "\n").encode()
                            self.wfile.write(f"{len(donnees):X}\r\n".encode() + donnees + b"\r\n")
                            self.wfile.flush()
                            if serveur.delai_morceau:
                                time.sleep(serveur.delai_morceau)
                        self.wfile.write(b"0\r\n\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if self.path == "/v1/audio/speech":
                    voix = corps.get("voice")
                    if voix and voix not in serveur.voix:
                        return self._json(400, {"error": {"message": f"unknown voice; available: {serveur.voix}"}})
                    # 0,25 s de sinus à 24 kHz, envoyé en morceaux de taille impaire
                    pcm = b"".join(int(8000 * math.sin(2 * math.pi * 440 * i / 24000)).to_bytes(2, "little", signed=True)
                                   for i in range(6000))
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/pcm")
                    self.send_header("Content-Length", str(len(pcm)))
                    self.end_headers()
                    for i in range(0, len(pcm), 1001):
                        self.wfile.write(pcm[i:i + 1001])
                    return
                self._json(404, {"error": "not found"})

        self._serveur = ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire)
        self._serveur.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._serveur.server_address[1]}"
        self._fil = threading.Thread(target=self._serveur.serve_forever, daemon=True)

    def demarrer(self) -> "FauxOllama":
        self._fil.start()
        return self

    def arreter(self) -> None:
        self._serveur.shutdown()
        self._serveur.server_close()

    def scenario(self, *reponses: list[dict]) -> None:
        self.scenarios.extend(reponses)

    def requetes_chat(self) -> list[dict]:
        return [r["corps"] for r in self.requetes if r["chemin"] == "/api/chat"]
