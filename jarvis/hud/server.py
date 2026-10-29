"""Serveur du HUD : une page + un WebSocket, sur 127.0.0.1 uniquement.

Sécurité (c'est un petit serveur web sur ta machine, il mérite qu'on soit sérieux) :
- écoute seulement sur 127.0.0.1 : invisible depuis le réseau ;
- jeton aléatoire dans l'URL ouverte par Jarvis, exigé pour le WebSocket ;
- vérification de l'en-tête Origin : un site web malveillant ouvert dans ton
  navigateur ne peut pas piloter Jarvis (attaque « Cross-Site WebSocket Hijacking ») ;
- Content-Security-Policy stricte : seuls les scripts dont l'empreinte SHA-256
  est connue peuvent s'exécuter, la page ne contacte aucun autre serveur ;
- la page ne peut pas être intégrée dans un autre site (anti-clickjacking).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from jarvis.assistant import Assistant

log = logging.getLogger("jarvis.hud")

PAGE = Path(__file__).resolve().parent / "static" / "index.html"
SQUELETTE = (
    '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
    "{tete}</head><body>{corps}</body></html>"
)


def assembler_page(fragment: str) -> tuple[str, list[str]]:
    """La page est écrite comme un fragment (titre, style, contenu, script).
    On l'emballe dans un document complet et on calcule l'empreinte de chaque script."""
    fragment = fragment.replace('<meta name="jarvis-mode" content="demo">', '<meta name="jarvis-mode" content="local">')
    fin_style = fragment.find("</style>")
    tete, corps = (fragment[: fin_style + 8], fragment[fin_style + 8:]) if fin_style >= 0 else ("", fragment)
    scripts = re.findall(r"<script>(.*?)</script>", corps, flags=re.S)
    empreintes = [
        "'sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode() + "'" for s in scripts
    ]
    return SQUELETTE.format(tete=tete, corps=corps), empreintes


def traiter_message(assistant: "Assistant", d: dict) -> None:
    genre = d.get("type")
    if genre == "question" and isinstance(d.get("texte"), str):
        assistant.demander(d["texte"][:4000], source="texte")
    elif genre == "ecouter":
        assistant.ecouter()
    elif genre == "interrompre":
        assistant.interrompre()
    elif genre == "confirmation":
        assistant.repondre_confirmation(str(d.get("id", "")), d.get("accord") is True)
    elif genre == "reglages":
        assistant.regler(
            confidentiel=d["confidentiel"] if isinstance(d.get("confidentiel"), bool) else None,
            voix_active=d["voix_active"] if isinstance(d.get("voix_active"), bool) else None,
        )
    elif genre == "oublier_conversation":
        assistant.oublier_conversation()


def creer_app(assistant: "Assistant", jeton: str, port: int) -> Starlette:
    html, empreintes = assembler_page(PAGE.read_text(encoding="utf-8"))
    origines = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    csp = (
        "default-src 'none'; "
        f"script-src {' '.join(empreintes)}; "
        "style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
        f"connect-src ws://127.0.0.1:{port} ws://localhost:{port}; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    entetes = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    clients: set[asyncio.Queue] = set()

    async def page(request):
        return Response(html, media_type="text/html; charset=utf-8", headers=entetes)

    async def sante(request):
        return JSONResponse({"ok": True, "etat": assistant.etat})

    async def ws(websocket: WebSocket):
        recu = websocket.query_params.get("t", "")
        origine = websocket.headers.get("origin", "")
        if not hmac.compare_digest(recu.encode(), jeton.encode()) or origine not in origines:
            log.warning("Connexion WebSocket refusée (origine %r)", origine)
            await websocket.close(code=1008)
            return
        await websocket.accept()
        file: asyncio.Queue = asyncio.Queue(maxsize=2000)
        clients.add(file)
        bonjour = {"type": "bonjour", "resume": assistant.resume(), "historique": list(assistant.bus.historique)}
        await websocket.send_text(json.dumps(bonjour, ensure_ascii=False, default=str))

        async def emettre():
            while True:
                evt = await file.get()
                await websocket.send_text(json.dumps(evt, ensure_ascii=False, default=str))

        tache = asyncio.create_task(emettre())
        try:
            while True:
                message = await websocket.receive_text()
                if len(message) > 20000:
                    continue
                try:
                    d = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict):
                    traiter_message(assistant, d)
        except WebSocketDisconnect:
            pass
        finally:
            tache.cancel()
            clients.discard(file)

    @contextlib.asynccontextmanager
    async def cycle(app):
        boucle = asyncio.get_running_loop()

        def diffuser(evt: dict) -> None:
            for f in list(clients):
                try:
                    f.put_nowait(evt)
                except asyncio.QueueFull:
                    if evt.get("type") != "niveau":
                        with contextlib.suppress(Exception):
                            f.get_nowait()
                            f.put_nowait(evt)

        desabonner = assistant.bus.abonner(lambda evt: boucle.call_soon_threadsafe(diffuser, evt))
        try:
            yield
        finally:
            desabonner()

    return Starlette(
        routes=[Route("/", page), Route("/sante", sante), WebSocketRoute("/ws", ws)],
        lifespan=cycle,
    )


def lancer(assistant: "Assistant", port: int = 7777, ouvrir: bool = True) -> None:
    import uvicorn

    jeton = secrets.token_urlsafe(24)
    app = creer_app(assistant, jeton, port)
    url = f"http://127.0.0.1:{port}/?t={jeton}"
    print(f"\n  HUD de Jarvis : {url}\n  (lien personnel : ne le partage pas)\n")
    if ouvrir:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", ws="auto")
