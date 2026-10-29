"""La voix de Jarvis (synthèse vocale), toujours en local.

  ollama  Phonon de Gradium servi par Ollama (Mac Apple Silicon, Ollama ≥ 0.40.11).
          Voix française « Augustin », 141 Mo en mémoire, plus de 100× le temps réel.
  say     La synthèse intégrée à macOS (voix « Thomas »). Zéro installation.
  ptts    Phonon via le moteur Rust de Gradium (Linux/Mac, compte Hugging Face requis).
  espeak  espeak-ng (Linux, voix robotique mais universelle).
  aucun   Jarvis répond seulement par écrit.

L'Orateur reçoit les phrases une par une dès qu'elles sont écrites par le
modèle : Jarvis commence à parler pendant qu'il réfléchit encore à la suite.
"""

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
import time
from typing import Callable, Iterator

import numpy as np

from jarvis.config import IS_APPLE_SILICON, IS_MAC, Config
from jarvis.llm import Ollama
from jarvis.textutils import pour_la_voix
from jarvis.voice.audio import HautParleur, carillon

log = logging.getLogger("jarvis.tts")


class Voix:
    nom = "?"
    freq = 24000
    joue_seule = False  # True si la voix joue elle-même le son (say, espeak)

    def synthese(self, texte: str) -> Iterator[np.ndarray]:
        raise NotImplementedError

    def dire_seule(self, texte: str, stop: threading.Event) -> None:
        raise NotImplementedError


class VoixOllama(Voix):
    def __init__(self, llm: Ollama, modele: str, voix: str) -> None:
        self.llm, self.modele, self.voix = llm, modele, voix
        self.nom = f"Phonon · {voix}" if voix else "Phonon"

    def synthese(self, texte: str) -> Iterator[np.ndarray]:
        reste = b""
        for bloc in self.llm.parler(self.modele, texte, self.voix or None):
            donnees = reste + bloc
            utile = len(donnees) - (len(donnees) % 2)  # un échantillon = 2 octets
            reste = donnees[utile:]
            if utile:
                yield np.frombuffer(donnees[:utile], dtype="<i2").copy()


class VoixPtts(Voix):
    def __init__(self, modele: str, voix: str) -> None:
        import ptts

        depot = {"phonon:fr": "hf://gradium/phonon-40m-fr"}.get(modele, modele)
        self._tts = ptts.TTS(depot, lang="fr")
        self.freq = int(self._tts.sample_rate)
        self.voix = voix if voix in getattr(self._tts, "voices", []) else None
        self.nom = f"Phonon (ptts) · {self.voix or 'défaut'}"

    def synthese(self, texte: str) -> Iterator[np.ndarray]:
        with self._tts.stream(texte, voice=self.voix) as flux:
            for morceau in flux:
                yield (np.clip(np.asarray(morceau, dtype=np.float32), -1, 1) * 32767).astype(np.int16)


class VoixCommande(Voix):
    """Voix fournie par une commande système qui joue le son elle-même."""
    joue_seule = True

    def __init__(self, commande: list[str], nom: str) -> None:
        self.commande, self.nom = commande, nom
        self._processus: subprocess.Popen | None = None

    def dire_seule(self, texte: str, stop: threading.Event) -> None:
        self._processus = subprocess.Popen(self.commande + [texte], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
        while self._processus.poll() is None:
            if stop.wait(0.05):
                self._processus.terminate()
                break


class VoixMuette(Voix):
    nom = "aucune"
    joue_seule = True

    def dire_seule(self, texte: str, stop: threading.Event) -> None:
        return


def creer_voix(config: Config, llm: Ollama) -> Voix:
    v = config.voix
    moteur = v.moteur
    if moteur == "auto":
        moteur = _choisir_moteur(config, llm)
    if moteur == "ollama":
        return VoixOllama(llm, v.modele_tts, v.voix)
    if moteur == "say":
        return VoixCommande(["say", "-v", v.voix_say, "-r", str(v.vitesse_say)], f"macOS · {v.voix_say}")
    if moteur == "ptts":
        return VoixPtts(v.modele_tts, v.voix)
    if moteur == "espeak":
        commande = shutil.which("espeak-ng") or shutil.which("espeak")
        if not commande:
            raise RuntimeError("espeak-ng n'est pas installé (sudo apt install espeak-ng).")
        return VoixCommande([commande, "-v", "fr+m3", "-s", "165"], "espeak-ng")
    return VoixMuette()


def _choisir_moteur(config: Config, llm: Ollama) -> str:
    if IS_APPLE_SILICON:
        try:
            if "speech" in llm.capacites(config.voix.modele_tts):
                return "ollama"
        except Exception:
            log.info("Phonon indisponible dans Ollama : repli sur la voix de macOS.")
        return "say"
    if IS_MAC:
        return "say"
    try:
        import ptts  # noqa: F401
        return "ptts"
    except ImportError:
        pass
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return "espeak"
    return "aucun"


class Orateur:
    """File de phrases à dire, jouées dans l'ordre par un fil dédié.
    stop() coupe la parole immédiatement (et vide la file)."""

    def __init__(self, voix: Voix, *, sur_debut: Callable[[], None] | None = None,
                 sur_fin: Callable[[bool], None] | None = None,
                 sur_niveau: Callable[[float], None] | None = None) -> None:
        self.voix = voix
        self.sur_debut, self.sur_fin, self.sur_niveau = sur_debut, sur_fin, sur_niveau
        self._file: queue.Queue[str | None | np.ndarray] = queue.Queue()
        self._stop = threading.Event()
        self._occupe = threading.Event()
        self._fil = threading.Thread(target=self._boucle, name="jarvis-orateur", daemon=True)
        self._fil.start()

    @property
    def parle(self) -> bool:
        return self._occupe.is_set() or not self._file.empty()

    def dire(self, texte: str) -> None:
        texte = pour_la_voix(texte)
        if texte:
            self._file.put(texte)

    def jouer_son(self, echantillons: np.ndarray) -> None:
        self._file.put(echantillons)

    def bip(self) -> None:
        self.jouer_son(carillon(24000))

    def stop(self) -> None:
        self._stop.set()
        while True:
            try:
                self._file.get_nowait()
            except queue.Empty:
                break

    def attendre(self, delai: float = 120.0) -> None:
        fin = time.monotonic() + delai
        while self.parle and time.monotonic() < fin:
            time.sleep(0.05)

    def fermer(self) -> None:
        self.stop()
        self._file.put(None)

    def _boucle(self) -> None:
        haut_parleur = HautParleur(self.voix.freq)
        son = HautParleur(24000)
        while True:
            element = self._file.get()
            if element is None:
                return
            self._stop.clear()
            premier = not self._occupe.is_set()
            self._occupe.set()
            if premier and self.sur_debut and not isinstance(element, np.ndarray):
                self.sur_debut()
            complet = True
            try:
                if isinstance(element, np.ndarray):
                    son.jouer([element], self._stop)
                elif self.voix.joue_seule:
                    self.voix.dire_seule(element, self._stop)
                    complet = not self._stop.is_set()
                else:
                    complet = haut_parleur.jouer(self.voix.synthese(element), self._stop, self.sur_niveau)
            except Exception as e:
                log.warning("Synthèse vocale impossible : %s", e)
            if self._file.empty() or self._stop.is_set():
                self._occupe.clear()
                if self.sur_niveau:
                    self.sur_niveau(0.0)
                if self.sur_fin and not isinstance(element, np.ndarray):
                    self.sur_fin(complet and not self._stop.is_set())
