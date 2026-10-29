import wave

import numpy as np
import pytest
from conftest import DONNEES

from jarvis.voice.audio import BLOC, _doubler_frequence, carillon, niveau
from jarvis.voice.oreilles import interpreter_accord
from jarvis.voice.stt import nettoyer
from jarvis.voice.vad import Enregistreur, SileroVAD
from jarvis.voice.wakeword import DetecteurMotActivation


def lire_wav(nom: str) -> np.ndarray:
    with wave.open(str(DONNEES / nom)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def blocs(audio: np.ndarray):
    for i in range(0, len(audio) - BLOC + 1, BLOC):
        yield audio[i:i + BLOC]


SILENCE = np.zeros(16000, dtype=np.int16)


# ------------------------------------------------------------------ « Hey Jarvis »
def test_mot_activation_detecte():
    d = DetecteurMotActivation()
    audio = np.concatenate([SILENCE, lire_wav("hey_jarvis.wav"), SILENCE])
    scores = [d.score(b) for b in blocs(audio)]
    assert max(scores) > 0.9


@pytest.mark.parametrize("audio", [
    lambda: np.concatenate([SILENCE, lire_wav("alexa.wav"), SILENCE]),
    lambda: np.zeros(16000 * 3, dtype=np.int16),
    lambda: (np.random.default_rng(1).normal(0, 3000, 16000 * 3)).astype(np.int16),
])
def test_mot_activation_ne_se_declenche_pas_a_tort(audio):
    d = DetecteurMotActivation()
    assert not any(d.detecte(b) for b in blocs(audio()))


def test_mot_activation_se_rearme():
    d = DetecteurMotActivation()
    audio = np.concatenate([SILENCE, lire_wav("hey_jarvis.wav"), SILENCE])
    detections = sum(d.detecte(b) for b in blocs(audio))
    assert detections == 1  # un seul déclenchement pour un seul « Hey Jarvis »


# ------------------------------------------------------------------ détection de parole
def test_vad_distingue_voix_et_silence():
    vad = SileroVAD()
    assert max(vad.traiter(np.zeros(16000, dtype=np.int16))) < 0.2
    vad.reinitialiser()
    assert max(vad.traiter(lire_wav("hey_jarvis.wav"))) > 0.8


def test_enregistreur_capte_une_phrase():
    e = Enregistreur(SileroVAD(), silence_fin_ms=600)
    audio = np.concatenate([SILENCE[:8000], lire_wav("hey_jarvis.wav"), np.zeros(24000, dtype=np.int16)])
    etats = []
    for b in blocs(audio):
        etats.append(e.ajouter(b))
        if etats[-1] in ("fin", "rien"):
            break
    assert etats[-1] == "fin" and "parole" in etats and etats[0] == "attente"
    capte = e.audio()
    assert 0.8 < len(capte) / 16000 < 3.2


def test_enregistreur_abandonne_sans_parole():
    e = Enregistreur(SileroVAD(), attente_max_s=1.0)
    etats = [e.ajouter(b) for b in blocs(np.zeros(16000 * 2, dtype=np.int16))]
    assert "rien" in etats and "parole" not in etats


# ------------------------------------------------------------------ petits outils audio
def test_niveau_sonore():
    assert niveau(np.zeros(1280, dtype=np.int16)) == 0.0
    t = np.arange(1280) / 16000
    assert niveau((np.sin(2 * np.pi * 440 * t) * 30000).astype(np.int16)) > 0.95


def test_carillon_et_reechantillonnage():
    son = carillon()
    assert son.dtype == np.int16 and 0.15 < len(son) / 24000 < 0.25
    assert len(_doubler_frequence(son)) == 2 * len(son)


# ------------------------------------------------------------------ confirmation vocale et transcription
@pytest.mark.parametrize("phrase,attendu", [
    ("Oui, vas-y.", True), ("Ok je confirme", True), ("D'accord", True), ("Non merci", False),
    ("Annule ça", False), ("Pas d'accord", False), ("Euh… attends", False), ("Je ne sais pas trop", None),
])
def test_interpreter_accord(phrase, attendu):
    assert interpreter_accord(phrase) is attendu


def test_filtre_des_hallucinations():
    assert nettoyer("Sous-titres réalisés par la communauté d'Amara.org") == ""
    assert nettoyer(" ... ") == ""
    assert nettoyer("  Quelle heure   est-il ? ") == "Quelle heure est-il ?"
