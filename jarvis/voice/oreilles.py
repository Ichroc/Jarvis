"""Boucle d'écoute : « Hey Jarvis » → enregistrement de la phrase → transcription → question."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from jarvis.textutils import normaliser
from jarvis.voice.audio import Micro, niveau

if TYPE_CHECKING:
    from jarvis.assistant import Assistant
    from jarvis.voice.stt import Transcripteur
    from jarvis.voice.vad import Enregistreur
    from jarvis.voice.wakeword import DetecteurMotActivation

log = logging.getLogger("jarvis.oreilles")

_OUI = ("oui", "ouais", "vas y", "allez y", "confirme", "d accord", "ok", "okay", "autorise", "fais le",
        "bien sur", "exactement", "affirmatif", "yes", "go", "valide", "c est bon")
_NON = ("non", "annule", "stop", "refuse", "surtout pas", "pas question", "negatif", "arrete", "no",
        "laisse tomber", "attends", "pas ok", "pas d accord", "pas maintenant", "pas la peine")


def interpreter_accord(texte: str) -> bool | None:
    """« oui », « vas-y », « non merci »… -> True / False / None (pas compris)."""
    phrase = f" {normaliser(texte)} "
    if any(f" {n} " in phrase for n in _NON):
        return False
    if any(f" {o} " in phrase for o in _OUI):
        return True
    return None


class Oreilles:
    def __init__(self, assistant: "Assistant", micro: Micro, detecteur: "DetecteurMotActivation | None",
                 enregistreur: "Enregistreur", transcripteur: "Transcripteur") -> None:
        self.assistant = assistant
        self.bus = assistant.bus
        self.micro = micro
        self.detecteur = detecteur
        self.enregistreur = enregistreur
        self.transcripteur = transcripteur
        self._stop = threading.Event()
        self._demande: float | None = None  # écoute demandée sans mot d'activation (délai en s)
        self._fil = threading.Thread(target=self._boucle, name="jarvis-oreilles", daemon=True)

    def demarrer(self) -> None:
        self.micro.demarrer()
        self._fil.start()

    def arreter(self) -> None:
        self._stop.set()

    def declencher(self, attente_s: float = 8.0) -> None:
        """Écoute immédiate (bouton micro / barre d'espace du HUD)."""
        self._demande = attente_s

    def ecouter_suite(self, attente_s: float = 5.0) -> None:
        """Après une réponse : quelques secondes pour enchaîner sans redire « Hey Jarvis »."""
        self._demande = attente_s

    def _boucle(self) -> None:
        mode = "veille"
        n = 0
        zeros = 0
        while not self._stop.is_set():
            bloc = self.micro.lire(0.3)
            if bloc is None:
                continue
            n += 1
            # Un micro qui ne renvoie que des zéros parfaits, c'est presque toujours une permission refusée.
            zeros = zeros + 1 if not bloc.any() else -1_000_000
            if zeros == 40:
                self.bus.publier("avertissement", texte=(
                    "Le micro ne renvoie que du silence. Sur Mac : Réglages Système > Confidentialité et "
                    "sécurité > Micro, puis autorise ton terminal et relance Jarvis."))
            if mode == "ecoute" or n % 2 == 0:
                self.bus.publier("niveau", source="micro", valeur=round(niveau(bloc), 3))

            if self._demande is not None:
                attente, self._demande = self._demande, None
                self.micro.vider()  # on jette ce qui a été capté pendant que Jarvis parlait
                self.enregistreur.commencer(attente_max_s=attente)
                mode = "ecoute"
                self.assistant.changer_etat("ecoute")
                continue

            if mode == "veille":
                if self.detecteur is not None and self.detecteur.detecte(bloc):
                    self.bus.publier("activation")
                    orateur = self.assistant.orateur
                    if orateur is not None:
                        if orateur.parle or self.assistant.occupe:
                            self.assistant.interrompre()
                        orateur.bip()
                    self.enregistreur.commencer(attente_max_s=6.0, ignorer_ms=400)
                    mode = "ecoute"
                    self.assistant.changer_etat("ecoute")
            else:
                resultat = self.enregistreur.ajouter(bloc)
                if resultat == "fin":
                    mode = "veille"
                    self._transcrire()
                elif resultat == "rien":
                    mode = "veille"
                    self.assistant.fin_ecoute_sans_parole()
        self.micro.arreter()

    def _transcrire(self) -> None:
        audio = self.enregistreur.audio()
        self.assistant.changer_etat("transcription")
        debut = time.perf_counter()
        try:
            texte = self.transcripteur.transcrire(audio)
        except Exception as e:
            log.exception("Transcription impossible")
            self.bus.publier("erreur", texte=f"Transcription impossible : {e}")
            self.assistant.fin_ecoute_sans_parole()
            return
        ms = round((time.perf_counter() - debut) * 1000)
        self.bus.publier("transcription", texte=texte, ms=ms, duree_audio_s=round(len(audio) / 16000, 2))
        if not texte:
            self.assistant.fin_ecoute_sans_parole()
            return
        if self.assistant.confirmation_en_attente():
            self.assistant.reponse_vocale_confirmation(texte)
            return
        self.assistant.demander(texte, source="voix", ms_transcription=ms)
