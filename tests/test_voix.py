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


# ------------------------------------------------------------------ Piper (module simulé : rien n'est téléchargé)
import importlib.machinery  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from jarvis.voice import tts  # noqa: E402

SIWIS = "fr_FR-siwis-medium"


@pytest.fixture
def faux_piper(monkeypatch):
    etat = {"telechargements": [], "panne": False}
    piper = types.ModuleType("piper")
    piper.__spec__ = importlib.machinery.ModuleSpec("piper", None)
    module_dl = types.ModuleType("piper.download_voices")

    def download_voice(nom, dossier):
        etat["telechargements"].append(nom)
        (dossier / f"{nom}.onnx").write_bytes(b"\0" * 2_000_000)
        if etat["panne"]:
            raise OSError("connexion coupée")
        (dossier / f"{nom}.onnx.json").write_text("{}", encoding="utf-8")

    class Morceau:
        def __init__(self, echantillons):
            self.audio_int16_array = echantillons

    class FauxPiperVoice:
        config = SimpleNamespace(sample_rate=22050)

        @classmethod
        def load(cls, chemin):
            voix = cls()
            voix.chemin = chemin
            return voix

        def synthesize(self, texte, syn_config=None):
            etat["reglages"] = syn_config
            for phrase in texte.split(". "):
                yield Morceau(np.full(220, len(phrase), dtype=np.int16))

    class SynthesisConfig:
        def __init__(self, length_scale=None):
            self.length_scale = length_scale

    piper.PiperVoice, piper.SynthesisConfig = FauxPiperVoice, SynthesisConfig
    module_dl.download_voice = download_voice
    monkeypatch.setitem(sys.modules, "piper", piper)
    monkeypatch.setitem(sys.modules, "piper.download_voices", module_dl)
    return etat


def test_voix_piper_telechargee_une_seule_fois(tmp_path, faux_piper):
    voix = tts.VoixPiper(SIWIS, tmp_path / "voix", debit=1.25)
    assert voix.nom == "Piper · Siwis" and voix.freq == 22050
    morceaux = list(voix.synthese("Bonjour Ichroc. Il fait beau."))
    assert len(morceaux) == 2 and all(m.dtype == np.int16 for m in morceaux)
    assert faux_piper["reglages"].length_scale == pytest.approx(0.8)  # débit 1,25 = phonèmes 20 % plus courts
    tts.VoixPiper(SIWIS, tmp_path / "voix")
    assert faux_piper["telechargements"] == [SIWIS]
    assert sorted(f.name for f in (tmp_path / "voix").iterdir()) == [f"{SIWIS}.onnx", f"{SIWIS}.onnx.json"]


def test_telechargement_coupe_ne_laisse_aucun_fichier_casse(tmp_path, faux_piper):
    faux_piper["panne"] = True
    with pytest.raises(OSError):
        tts.VoixPiper(SIWIS, tmp_path / "voix")
    assert list((tmp_path / "voix").iterdir()) == []  # au prochain lancement, on retéléchargera proprement


def test_voix_auto_sur_linux(config, faux_ollama, faux_piper, monkeypatch):
    monkeypatch.setattr(tts, "IS_APPLE_SILICON", False)
    monkeypatch.setattr(tts, "IS_MAC", False)
    monkeypatch.setattr(tts.shutil, "which", lambda commande: None)
    llm = Ollama(faux_ollama.url)
    assert tts.moteurs_possibles(config, llm) == ["piper"]
    assert isinstance(tts.creer_voix(config, llm), tts.VoixPiper)
    faux_piper["panne"] = True
    config.voix.voix_piper = "fr_FR-tom-medium"
    assert isinstance(tts.creer_voix(config, llm), tts.VoixMuette)  # repli silencieux, sans planter


def test_voix_auto_sur_mac(config, faux_ollama, faux_piper, monkeypatch):
    monkeypatch.setattr(tts, "IS_APPLE_SILICON", True)
    monkeypatch.setattr(tts, "IS_MAC", True)
    monkeypatch.setattr(tts.shutil, "which", lambda commande: None)
    llm = Ollama(faux_ollama.url)
    assert tts.moteurs_possibles(config, llm) == ["ollama", "piper", "say"]
    voix = tts.creer_voix(config, llm)
    assert isinstance(voix, tts.VoixOllama) and voix.nom == "Phonon · Noémie"


def test_voix_phonon_inconnue_repli_sur_la_voix_par_defaut(faux_ollama):
    voix = VoixOllama(Ollama(faux_ollama.url), "phonon:fr", "Inexistante")
    pcm = np.concatenate(list(voix.synthese("Bonjour.")))
    assert len(pcm) == 6000 and voix.nom == "Phonon · voix par défaut"
