import base64
import hashlib
import re

import pytest

pytest.importorskip("httpx")  # requis par le client de test de Starlette
from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from jarvis.events import Bus  # noqa: E402
from jarvis.hud.server import PAGE, assembler_page, creer_app  # noqa: E402

JETON = "jeton-de-test"
ORIGINE = {"origin": "http://127.0.0.1:7777"}


class FauxAssistant:
    def __init__(self):
        self.bus = Bus()
        self.etat = "veille"
        self.recu = []

    def resume(self):
        return {"modele": "qwen3.5:9b", "etat": self.etat, "composants": {}}

    def demander(self, texte, source="texte"):
        self.recu.append(("demander", texte, source))

    def ecouter(self):
        self.recu.append(("ecouter",))

    def interrompre(self):
        self.recu.append(("interrompre",))

    def repondre_confirmation(self, ident, accord):
        self.recu.append(("confirmation", ident, accord))

    def regler(self, **kwargs):
        self.recu.append(("regler", kwargs))

    def oublier_conversation(self):
        self.recu.append(("oublier",))


@pytest.fixture
def client():
    assistant = FauxAssistant()
    with TestClient(creer_app(assistant, JETON, 7777)) as c:
        c.assistant = assistant
        yield c


def test_page_et_entetes_de_securite(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert '<meta name="jarvis-mode" content="local">' in r.text and r.text.startswith("<!doctype html>")
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "default-src 'none'" in csp and "unsafe-inline' ;" not in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    script = re.search(r"<script>(.*?)</script>", r.text, re.S).group(1)
    empreinte = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{empreinte}'" in csp  # seul ce script exact peut s'exécuter


def test_le_fragment_reste_publiable_tel_quel():
    fragment = PAGE.read_text(encoding="utf-8")
    assert fragment.startswith("<title>") and "<html" not in fragment and "<body" not in fragment
    html, empreintes = assembler_page(fragment)
    assert len(empreintes) == 1 and html.count("<title>") == 1


def test_websocket_sans_jeton_refuse(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws?t=mauvais", headers=ORIGINE) as ws:
            ws.receive_json()
    assert e.value.code == 1008


def test_websocket_origine_etrangere_refusee(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws?t={JETON}", headers={"origin": "https://site-malveillant.example"}) as ws:
            ws.receive_json()
    assert e.value.code == 1008


def test_websocket_dialogue_complet(client):
    with client.websocket_connect(f"/ws?t={JETON}", headers=ORIGINE) as ws:
        bonjour = ws.receive_json()
        assert bonjour["type"] == "bonjour" and bonjour["resume"]["modele"] == "qwen3.5:9b"
        ws.send_json({"type": "question", "texte": "Quelle heure est-il ?"})
        ws.send_json({"type": "confirmation", "id": "abc", "accord": True})
        ws.send_json({"type": "reglages", "confidentiel": True})
        ws.send_text("pas du json")
        ws.send_json({"type": "ecouter"})
        client.assistant.bus.publier("etat", etat="reflexion")
        evt = ws.receive_json()
        assert evt["type"] == "etat" and evt["etat"] == "reflexion"
    recu = client.assistant.recu
    assert ("demander", "Quelle heure est-il ?", "texte") in recu
    assert ("confirmation", "abc", True) in recu
    assert ("regler", {"confidentiel": True, "voix_active": None}) in recu
    assert ("ecouter",) in recu


def test_sante(client):
    assert client.get("/sante").json() == {"ok": True, "etat": "veille"}
