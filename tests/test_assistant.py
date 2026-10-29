"""Tests d'intégration : l'Assistant complet (sans micro ni haut-parleur) contre le faux Ollama."""

import threading
import time
from datetime import datetime, timedelta

from faux_ollama import reponse_outil, reponse_texte

from jarvis.assistant import Assistant
from jarvis.llm import Ollama


def collecter(bus):
    evts = []
    bus.abonner(evts.append)
    return evts


def attendre(condition, delai=5.0):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_demarrage_et_reponse(config, faux_ollama):
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url),
                  confirmateur=lambda d: True)
    evts = collecter(a.bus)
    a.demarrer()
    assert a.composants["cerveau"]["ok"] and "tools" in a.composants["cerveau"]["detail"]
    faux_ollama.scenario(reponse_outil("heure_et_date", {}), reponse_texte("Il est l'heure du thé."))
    reponse = a.traiter("Quelle heure est-il ?")
    assert reponse == "Il est l'heure du thé."
    types = [e["type"] for e in evts]
    for attendu in ("pret", "utilisateur", "outil", "jeton", "reponse"):
        assert attendu in types
    assert a.etat == "inactif" and not a.occupe
    assert a.resume()["stats"]["outils"] == 1
    a.arreter()


def test_confirmation_par_le_hud(config, faux_ollama):
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url))
    evts = collecter(a.bus)
    a.demarrer()

    def repondre(evt):
        if evt["type"] == "confirmation":
            threading.Timer(0.05, a.repondre_confirmation, (evt["id"], True)).start()

    a.bus.abonner(repondre)
    faux_ollama.scenario(reponse_outil("executer_commande", {"commande": "echo HUD"}), reponse_texte("Fait."))
    a.demander("Lance echo HUD")
    assert attendre(lambda: any(e["type"] == "reponse" for e in evts))
    phases = [e.get("phase") for e in evts if e["type"] == "outil"]
    assert phases == ["appel", "resultat"]
    fin = [e for e in evts if e["type"] == "confirmation_fin"][0]
    assert fin["accord"] is True
    a.arreter()


def test_confirmation_expiree_vaut_refus(config, faux_ollama):
    config.securite.delai_confirmation_s = 1
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url))
    evts = collecter(a.bus)
    a.demarrer()
    faux_ollama.scenario(reponse_outil("oublier", {"recherche": "tout"}), reponse_texte("Rien n'a été effacé."))
    a.traiter("Oublie tout")
    assert [e.get("phase") for e in evts if e["type"] == "outil"] == ["appel", "refuse"]
    a.arreter()


def test_rappel_publie_et_mode_confidentiel(config, faux_ollama):
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url))
    evts = collecter(a.bus)
    a.demarrer()
    a.planificateur.ajouter("sortir les pâtes", datetime.now() - timedelta(seconds=1), "minuteur")
    assert attendre(lambda: any(e["type"] == "rappel" for e in evts), 3)
    rappel = [e for e in evts if e["type"] == "rappel"][0]
    assert rappel["texte"] == "sortir les pâtes" and rappel["genre"] == "minuteur"

    a.regler(confidentiel=True)
    faux_ollama.scenario(reponse_texte("Mode confidentiel."))
    a.traiter("Test")
    noms = {t["function"]["name"] for t in faux_ollama.requetes_chat()[-1]["tools"]}
    assert "meteo" not in noms and "recherche_web" not in noms and "calculer" in noms
    a.arreter()


def test_prechauffage(config, faux_ollama):
    config.cerveau.prechauffage = True
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url))
    evts = collecter(a.bus)
    a.demarrer()
    assert a.prechauffage_fini.wait(5)
    cerveau = a.composants["cerveau"]
    assert cerveau["ok"] is True and cerveau["nom"] == "qwen3.5:9b" and "préchauffé en" in cerveau["detail"]
    assert any(e["type"] == "info" and "Cerveau prêt" in e["texte"] for e in evts)
    chauffe = faux_ollama.requetes_chat()[0]
    assert chauffe["options"]["num_predict"] == 1
    assert any(r["chemin"] == "/api/embed" for r in faux_ollama.requetes)  # la mémoire est chargée aussi

    faux_ollama.scenario(reponse_texte("Prêt, Monsieur."))
    a.traiter("Bonjour")
    vraie = faux_ollama.requetes_chat()[1]
    # Même début, octet pour octet : Ollama réutilise le calcul du préchauffage (cache de préfixe).
    assert vraie["messages"][0] == chauffe["messages"][0]
    assert vraie["tools"] == chauffe["tools"]
    # Mêmes réglages de chargement : sinon Ollama rechargerait le modèle et tout serait perdu.
    assert vraie["options"]["num_ctx"] == chauffe["options"]["num_ctx"]
    assert vraie["keep_alive"] == chauffe["keep_alive"]
    a.arreter()


def test_prechauffage_sans_modele_de_memoire(config, faux_ollama):
    config.cerveau.prechauffage = True
    del faux_ollama.modeles["qwen3-embedding:0.6b"]  # la mémoire manque : le cerveau reste utilisable
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url))
    evts = collecter(a.bus)
    a.demarrer()
    assert a.prechauffage_fini.wait(5)
    assert a.composants["cerveau"]["ok"] is True
    assert not [e for e in evts if e["type"] == "avertissement" and "Préchauffage" in e["texte"]]
    a.arreter()


def test_prechauffage_en_echec_signale(config, faux_ollama):
    config.cerveau.prechauffage = True
    faux_ollama.erreur_chat = "model requires more system memory (9.8 GiB) than is available (6.1 GiB)"
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url))
    evts = collecter(a.bus)
    a.demarrer()
    assert a.prechauffage_fini.wait(5)
    assert a.composants["cerveau"]["ok"] is False and "more system memory" in a.composants["cerveau"]["detail"]
    assert any(e["type"] == "avertissement" and "Préchauffage du cerveau impossible" in e["texte"] for e in evts)
    a.arreter()


def test_modele_manquant_signale(config, faux_ollama):
    config.cerveau.modele = "modele-absent:7b"
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url))
    evts = collecter(a.bus)
    a.demarrer()
    assert not a.composants["cerveau"]["ok"]
    assert any("ollama pull modele-absent:7b" in e.get("texte", "") for e in evts if e["type"] == "avertissement")
    a.arreter()
