import pytest
from faux_ollama import reponse_outil, reponse_texte

from jarvis.llm import AppelOutil, ModeleIntrouvable, Ollama, OllamaErreur, OllamaIndisponible


def test_informations(faux_ollama):
    llm = Ollama(faux_ollama.url)
    assert llm.version() == "0.40.11"
    assert {"qwen3.5:9b", "phonon:fr"} <= llm.noms_modeles()
    assert "tools" in llm.capacites("qwen3.5:9b")
    assert llm.capacites("phonon:fr") == {"speech"}
    assert llm.voix("phonon:fr") == ["Augustin", "Lucien", "Noémie"]
    assert llm.charges()[0]["name"] == "qwen3.5:9b"


def test_conversation_en_flux(faux_ollama):
    faux_ollama.scenario(reponse_texte("Bonjour Monsieur, tous les systèmes sont prêts."))
    llm = Ollama(faux_ollama.url)
    morceaux = list(llm.discuter("qwen3.5:9b", [{"role": "user", "content": "Salut"}], reflexion=False,
                                 options={"num_ctx": 4096}, garder="30m"))
    assert "".join(m.contenu for m in morceaux) == "Bonjour Monsieur, tous les systèmes sont prêts."
    assert morceaux[-1].fini and morceaux[-1].stats["jetons_par_s"] == 50.0
    envoye = faux_ollama.requetes_chat()[0]
    assert envoye["think"] is False and envoye["stream"] is True and envoye["keep_alive"] == "30m"
    assert envoye["options"] == {"num_ctx": 4096}


def test_appels_d_outils(faux_ollama):
    faux_ollama.scenario(reponse_outil("meteo", {"ville": "Angers"}, ident="appel_1"),
                         reponse_outil("calculer", '{"expression": "2+2"}'))
    llm = Ollama(faux_ollama.url)
    appels = [a for m in llm.discuter("qwen3.5:9b", []) for a in m.appels]
    assert appels[0].nom == "meteo" and appels[0].arguments == {"ville": "Angers"} and appels[0].ident == "appel_1"
    assert appels[0].en_message() == {"type": "function", "function": {"name": "meteo", "arguments": {"ville": "Angers"}},
                                      "id": "appel_1"}
    appels = [a for m in llm.discuter("qwen3.5:9b", []) for a in m.appels]
    assert appels[0].arguments == {"expression": "2+2"}  # arguments reçus en texte JSON


def test_appel_mal_forme():
    a = AppelOutil.depuis({"function": {"name": "x", "arguments": "pas du json"}})
    assert a.arguments == {}


def test_vecteurs(faux_ollama):
    v = Ollama(faux_ollama.url).vecteurs("qwen3-embedding:0.6b", ["un", "deux trois"])
    assert len(v) == 2 and len(v[0]) == 256


def test_synthese_vocale(faux_ollama):
    blocs = list(Ollama(faux_ollama.url).parler("phonon:fr", "Bonjour", "Augustin"))
    assert sum(len(b) for b in blocs) == 12000
    assert faux_ollama.requetes[-1]["corps"]["response_format"] == "pcm"


def test_erreurs(faux_ollama):
    llm = Ollama(faux_ollama.url)
    with pytest.raises(ModeleIntrouvable) as e:
        list(llm.discuter("llama-inexistant", []))
    assert "ollama pull llama-inexistant" in str(e.value)
    with pytest.raises(OllamaErreur) as e:
        list(llm.parler("phonon:fr", "x", "Inconnu"))
    assert "unknown voice" in str(e.value)
    with pytest.raises(OllamaIndisponible):
        Ollama("http://127.0.0.1:9").version()
