import threading
import time

import numpy as np

from jarvis.llm import Ollama
from jarvis.voice.tts import Orateur, Voix, VoixOllama


def test_voix_ollama_reconstruit_les_echantillons(faux_ollama):
    voix = VoixOllama(Ollama(faux_ollama.url), "phonon:fr", "Augustin")
    morceaux = list(voix.synthese("Bonjour Monsieur."))
    pcm = np.concatenate(morceaux)
    assert pcm.dtype == np.int16 and len(pcm) == 6000  # les blocs HTTP impairs sont recollés proprement
    t = np.arange(6000) / 24000
    attendu = (8000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    assert np.abs(pcm.astype(int) - attendu.astype(int)).max() <= 1
    assert voix.nom == "Phonon · Augustin"


class VoixTest(Voix):
    nom = "test"
    joue_seule = True

    def __init__(self, duree=0.0):
        self.dites = []
        self.duree = duree

    def dire_seule(self, texte, stop: threading.Event):
        if stop.wait(self.duree):
            return
        self.dites.append(texte)


def test_orateur_dit_les_phrases_dans_l_ordre():
    voix = VoixTest()
    debuts = []
    orateur = Orateur(voix, sur_debut=lambda: debuts.append(1))
    orateur.dire("**Bonjour** Monsieur.")
    orateur.dire("Il fait beau 🌞.")
    time.sleep(0.05)
    orateur.attendre(2)
    assert voix.dites == ["Bonjour Monsieur.", "Il fait beau ."]
    assert debuts and not orateur.parle


def test_orateur_s_interrompt_immediatement():
    voix = VoixTest(duree=5.0)
    orateur = Orateur(voix)
    for i in range(5):
        orateur.dire(f"Phrase numéro {i}.")
    time.sleep(0.1)
    debut = time.perf_counter()
    orateur.stop()
    orateur.attendre(2)
    assert time.perf_counter() - debut < 0.5 and voix.dites == []
