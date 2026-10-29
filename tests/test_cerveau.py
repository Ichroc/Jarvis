import threading

import pytest
from faux_ollama import reponse_outil, reponse_texte

from jarvis import tools as registre
from jarvis.brain import Cerveau
from jarvis.llm import Ollama
from jarvis.memory import Memoire, Vectoriseur
from jarvis.scheduler import Planificateur
from jarvis.security import Journal, Politique
from jarvis.tools import Contexte

registre.charger_tous()


@pytest.fixture
def cerveau(config):
    llm = Ollama(config.cerveau.hote_ollama)
    memoire = Memoire(config.base, Vectoriseur(llm, config.cerveau.modele_embeddings))
    ctx = Contexte(config=config, llm=llm, memoire=memoire, planificateur=Planificateur(config.base))
    return Cerveau(config, llm, ctx, Politique(), Journal(config.journal_audit), memoire)


def jamais(demande):
    raise AssertionError("aucune confirmation ne devait être demandée")


def evenements(cerveau, texte, confirmer=jamais, **kwargs):
    return list(cerveau.demander(texte, confirmer=confirmer, **kwargs))


def test_reponse_simple(cerveau, faux_ollama):
    faux_ollama.scenario(reponse_texte("Bonjour Monsieur."))
    evts = evenements(cerveau, "Bonjour")
    assert [e["type"] for e in evts][0] == "debut"
    fin = evts[-1]
    assert fin["type"] == "fin" and fin["texte"] == "Bonjour Monsieur." and not fin["interrompu"]
    assert fin["stats"]["jetons"] == 40 and fin["stats"]["premier_mot_ms"] is not None
    requete = faux_ollama.requetes_chat()[0]
    assert requete["messages"][0]["role"] == "system" and "J.A.R.V.I.S." in requete["messages"][0]["content"]
    assert "[Contexte :" in requete["messages"][1]["content"] and "Mode écrit" in requete["messages"][1]["content"]
    assert requete["think"] is False and len(requete["tools"]) == 26
    assert len(cerveau.tours) == 1


def test_boucle_avec_outil(cerveau, faux_ollama):
    faux_ollama.scenario(reponse_outil("calculer", {"expression": "17*23"}), reponse_texte("Cela fait 391."))
    evts = evenements(cerveau, "Combien font 17 fois 23 ?")
    outils = [e for e in evts if e["type"] == "outil"]
    assert [e["phase"] for e in outils] == ["appel", "resultat"]
    assert outils[1]["ok"] and "= 391" in outils[1]["resultat"]
    assert evts[-1]["texte"] == "Cela fait 391." and evts[-1]["stats"]["outils"] == 1
    seconde = faux_ollama.requetes_chat()[1]["messages"]
    assert seconde[-2]["role"] == "assistant" and seconde[-2]["tool_calls"][0]["function"]["name"] == "calculer"
    assert seconde[-1] == {"role": "tool", "tool_name": "calculer", "content": "17*23 = 391"}
    journal = Journal(cerveau.config.journal_audit).derniers()
    assert journal[-1]["outil"] == "calculer" and journal[-1]["decision"] == "auto"


def test_action_sensible_refusee(cerveau, faux_ollama):
    faux_ollama.scenario(reponse_outil("executer_commande", {"commande": "ls ~"}), reponse_texte("Très bien."))
    demandes = []
    evts = evenements(cerveau, "Liste mon dossier", confirmer=lambda d: demandes.append(d) or False)
    assert demandes[0].outil.nom == "executer_commande" and demandes[0].arguments == {"commande": "ls ~"}
    assert [e["phase"] for e in evts if e["type"] == "outil"] == ["appel", "refuse"]
    retour = faux_ollama.requetes_chat()[1]["messages"][-1]
    assert retour["role"] == "tool" and "refusé" in retour["content"]
    assert Journal(cerveau.config.journal_audit).derniers()[-1]["decision"] == "refuse"


def test_action_sensible_acceptee(cerveau, faux_ollama):
    faux_ollama.scenario(reponse_outil("executer_commande", {"commande": "echo 42"}), reponse_texte("La réponse est 42."))
    evts = evenements(cerveau, "Lance echo 42", confirmer=lambda d: True)
    resultat = [e for e in evts if e["type"] == "outil" and e["phase"] == "resultat"][0]
    assert "42" in resultat["resultat"] and "CONTENU_EXTERNE" not in resultat["resultat"]
    assert "CONTENU_EXTERNE" in faux_ollama.requetes_chat()[1]["messages"][-1]["content"]


def test_commande_destructrice_bloquee_sans_meme_demander(cerveau, faux_ollama):
    faux_ollama.scenario(reponse_outil("executer_commande", {"commande": "sudo rm -rf /"}), reponse_texte("Non."))
    evts = evenements(cerveau, "Efface tout")
    assert [e["phase"] for e in evts if e["type"] == "outil"] == ["appel", "bloque"]


def test_outil_inconnu_et_arguments_invalides(cerveau, faux_ollama):
    faux_ollama.scenario(reponse_outil("lancer_fusee", {}), reponse_outil("minuteur", {"libelle": "x"}),
                         reponse_texte("Désolé."))
    evts = evenements(cerveau, "Fais quelque chose")
    erreurs = [e for e in evts if e["type"] == "outil" and e["phase"] == "erreur"]
    assert len(erreurs) == 2
    assert "n'existe pas" in erreurs[0]["resultat"] and "minutes" in erreurs[1]["resultat"]
    assert evts[-1]["texte"] == "Désolé."


def test_garde_fou_exfiltration_dans_un_meme_tour(cerveau, faux_ollama):
    faux_ollama.scenario(reponse_outil("lire_notes", {}), reponse_outil("wikipedia", {"sujet": "secret"}),
                         reponse_texte("Fini."))
    demandes = []
    evenements(cerveau, "Cherche mes notes sur Wikipédia", confirmer=lambda d: demandes.append(d) or False)
    assert len(demandes) == 1 and demandes[0].outil.nom == "wikipedia"
    assert "notes" in demandes[0].raison


def test_souvenirs_injectes_dans_le_contexte(cerveau, faux_ollama):
    cerveau.memoire.ajouter("Ichroc boit son café sans sucre.")
    faux_ollama.scenario(reponse_texte("Sans sucre, Monsieur."))
    evts = evenements(cerveau, "Comment je prends mon café sans sucre ou avec ?", mode_vocal=True)
    assert any(e["type"] == "contexte" for e in evts)
    message = faux_ollama.requetes_chat()[0]["messages"][-1]["content"]
    assert "Souvenirs utiles : Ichroc boit son café sans sucre." in message and "Mode vocal" in message
    assert faux_ollama.requetes_chat()[0]["options"]["num_predict"] == 500


def test_historique_et_budget(cerveau, faux_ollama):
    for i in range(3):
        faux_ollama.scenario(reponse_texte(f"Réponse {i}. " + "bla " * 300))
        evenements(cerveau, f"Question {i}")
    complet = faux_ollama.requetes_chat()[-1]["messages"]
    assert len(complet) == 1 + 2 * 2 + 1  # système + 2 échanges précédents + question
    cerveau.config.cerveau.contexte = 6000  # contexte minuscule : l'historique doit rétrécir
    faux_ollama.scenario(reponse_texte("ok"))
    evenements(cerveau, "Encore une")
    reduit = faux_ollama.requetes_chat()[-1]["messages"]
    assert len(reduit) < 1 + 3 * 2 + 1
    assert reduit[0]["role"] == "system" and reduit[-1]["role"] == "user"


def test_interruption(cerveau, faux_ollama):
    faux_ollama.delai_morceau = 0.05
    faux_ollama.scenario(reponse_texte("Une très longue réponse qui va être interrompue en plein milieu.", morceaux=20))
    annuler = threading.Event()
    evts = []
    for e in cerveau.demander("Raconte", confirmer=jamais, annuler=annuler):
        evts.append(e)
        if e["type"] == "jeton":
            annuler.set()
    assert evts[-1]["type"] == "fin" and evts[-1]["interrompu"]
    assert cerveau.tours[-1][-1]["content"].endswith("[interrompu]")


def test_derniere_etape_sans_outils(cerveau, faux_ollama):
    cerveau.config.cerveau.etapes_max = 2
    faux_ollama.scenario(reponse_outil("heure_et_date", {}), reponse_outil("heure_et_date", {}),
                         reponse_texte("Il est l'heure."))
    evts = evenements(cerveau, "Heure ?")
    requetes = faux_ollama.requetes_chat()
    assert len(requetes) == 3 and "tools" in requetes[0] and "tools" not in requetes[2]
    assert evts[-1]["texte"] == "Il est l'heure."


def test_ollama_absent(config):
    config.cerveau.hote_ollama = "http://127.0.0.1:9"
    llm = Ollama(config.cerveau.hote_ollama)
    c = Cerveau(config, llm, Contexte(config=config, llm=llm), Politique(), Journal(config.journal_audit))
    evts = evenements(c, "Bonjour")
    assert evts[-1]["type"] == "erreur" and "Ollama ne répond pas" in evts[-1]["message"]
