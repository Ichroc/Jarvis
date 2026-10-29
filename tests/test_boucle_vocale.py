"""La boucle vocale complète, sans matériel : un faux micro rejoue de vrais enregistrements,
une fausse voix note ce que Jarvis dit. On vérifie l'enchaînement des fils et des états."""

import queue
import threading
import time
import wave

import numpy as np
from conftest import DONNEES
from faux_ollama import reponse_outil, reponse_texte

from jarvis.assistant import Assistant
from jarvis.llm import Ollama
from jarvis.voice.audio import BLOC
from jarvis.voice.oreilles import Oreilles
from jarvis.voice.tts import Orateur, Voix
from jarvis.voice.vad import Enregistreur, SileroVAD
from jarvis.voice.wakeword import DetecteurMotActivation


def wav(nom):
    with wave.open(str(DONNEES / nom)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


class FauxMicro:
    """Produit du silence en continu (8× plus vite que le temps réel) et joue ce qu'on lui demande."""

    def __init__(self):
        self.file: queue.Queue = queue.Queue()
        self._a_dire: queue.Queue = queue.Queue()
        self._stop = threading.Event()

    def demarrer(self):
        threading.Thread(target=self._boucle, daemon=True).start()

    def _boucle(self):
        while not self._stop.is_set():
            try:
                audio = self._a_dire.get_nowait()
            except queue.Empty:
                audio = np.zeros(BLOC, dtype=np.int16)
            for i in range(0, len(audio) - BLOC + 1, BLOC):
                self.file.put(audio[i:i + BLOC])
                time.sleep(0.01)

    def dire(self, audio):
        self._a_dire.put(np.concatenate([audio, np.zeros(16000, dtype=np.int16)]))

    def lire(self, delai=0.5):
        try:
            return self.file.get(timeout=delai)
        except queue.Empty:
            return None

    def vider(self):
        while not self.file.empty():
            self.file.get_nowait()

    def arreter(self):
        self._stop.set()


class FauxTranscripteur:
    nom = "faux"

    def __init__(self, textes):
        self.textes = list(textes)
        self.durees = []

    def transcrire(self, audio):
        self.durees.append(len(audio) / 16000)
        return self.textes.pop(0) if self.textes else ""

    def prechauffer(self):
        pass


class VoixNotee(Voix):
    nom = "notée"
    joue_seule = True

    def __init__(self):
        self.phrases = []

    def dire_seule(self, texte, stop):
        self.phrases.append(texte)
        stop.wait(0.05)


def attendre(condition, delai=15.0):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.02)
    return False


def assemble(config, faux_ollama, textes):
    config.oreilles.conversation_continue = False
    a = Assistant(config, avec_voix=False, avec_oreilles=False, llm=Ollama(faux_ollama.url))
    evts = []
    a.bus.abonner(evts.append)
    a.demarrer()
    voix = VoixNotee()
    a.orateur = Orateur(voix, sur_debut=lambda: a.changer_etat("parole"))
    a.voix_active = True
    micro = FauxMicro()
    transcripteur = FauxTranscripteur(textes)
    a.oreilles = Oreilles(a, micro, DetecteurMotActivation(), Enregistreur(SileroVAD(), silence_fin_ms=600),
                          transcripteur)
    a.oreilles.demarrer()
    a.changer_etat("veille")
    return a, evts, voix, micro, transcripteur


def test_hey_jarvis_puis_question(config, faux_ollama):
    faux_ollama.scenario(reponse_outil("heure_et_date", {}), reponse_texte("Il est midi, Monsieur. Bon appétit."))
    a, evts, voix, micro, transcripteur = assemble(config, faux_ollama, ["Quelle heure est-il ?"])
    micro.dire(np.concatenate([wav("hey_jarvis.wav"), np.zeros(4000, dtype=np.int16), wav("hey_jarvis.wav")]))
    assert attendre(lambda: any(e["type"] == "reponse" for e in evts))
    assert attendre(lambda: not a.orateur.parle and a.etat == "veille")
    types = [e["type"] for e in evts]
    assert types.index("activation") < types.index("transcription") < types.index("utilisateur") < types.index("reponse")
    question = [e for e in evts if e["type"] == "utilisateur"][0]
    assert question["source"] == "voix" and question["texte"] == "Quelle heure est-il ?"
    assert 0.5 < transcripteur.durees[0] < 4.0
    assert voix.phrases == ["Il est midi, Monsieur.", "Bon appétit."]
    reponse = [e for e in evts if e["type"] == "reponse"][0]
    assert reponse["stats"]["transcription_ms"] is not None and reponse["stats"]["premiere_phrase_ms"] is not None
    a.arreter()


def test_confirmation_a_la_voix(config, faux_ollama):
    faux_ollama.scenario(reponse_outil("executer_commande", {"commande": "echo vocal"}),
                         reponse_texte("C'est fait."))
    a, evts, voix, micro, _ = assemble(config, faux_ollama, ["Lance la commande echo", "Oui, vas-y."])
    micro.dire(np.concatenate([wav("hey_jarvis.wav"), np.zeros(4000, dtype=np.int16), wav("hey_jarvis.wav")]))
    assert attendre(lambda: any(e["type"] == "confirmation" for e in evts))
    assert attendre(lambda: a.etat == "ecoute")  # Jarvis a posé la question et attend la réponse
    assert any("J'ai besoin de votre accord" in p for p in voix.phrases)
    micro.dire(wav("hey_jarvis.wav"))  # la « réponse orale » (le faux transcripteur la lit « Oui, vas-y. »)
    assert attendre(lambda: any(e["type"] == "reponse" for e in evts))
    fin = [e for e in evts if e["type"] == "confirmation_fin"][0]
    assert fin["accord"] is True
    resultat = [e for e in evts if e["type"] == "outil" and e.get("phase") == "resultat"][0]
    assert "vocal" in resultat["resultat"]
    a.arreter()


def test_hey_jarvis_interrompt_la_parole(config, faux_ollama):
    a, evts, voix, micro, _ = assemble(config, faux_ollama, [])

    class VoixLente(VoixNotee):
        def dire_seule(self, texte, stop):
            if not stop.wait(3.0):
                self.phrases.append(texte)

    lente = VoixLente()
    a.orateur = Orateur(lente, sur_debut=lambda: a.changer_etat("parole"))
    a.orateur.dire("Une très longue tirade que personne ne veut écouter jusqu'au bout.")
    assert attendre(lambda: a.orateur.parle, 2)
    micro.dire(wav("hey_jarvis.wav"))
    assert attendre(lambda: any(e["type"] == "interruption" for e in evts), 10)
    assert attendre(lambda: a.etat == "ecoute", 5)
    assert lente.phrases == []  # la tirade a été coupée
    a.arreter()
