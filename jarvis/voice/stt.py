"""Transcription de la voix en texte, en local.

  Mac Apple Silicon : mlx-whisper (Whisper large-v3 turbo accéléré par le GPU via MLX)
                      ou parakeet-mlx (encore plus rapide, 25 langues européennes)
  Autres systèmes   : faster-whisper sur le processeur

La langue est forcée en français : sur des phrases courtes (« Jarvis, quelle
heure est-il ? ») c'est nettement plus fiable que la détection automatique.
Le « vocabulaire » de la configuration aide Whisper à bien écrire tes noms
propres (prénoms, ESEO, Kubernetes, Wazuh…).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Protocol

import numpy as np

from jarvis.config import IS_APPLE_SILICON, Config

log = logging.getLogger("jarvis.stt")

# Phrases que Whisper « entend » parfois dans le silence ou le bruit (il a été
# entraîné sur beaucoup de vidéos sous-titrées). On les jette.
HALLUCINATIONS = (
    "sous-titres réalisés par", "sous-titrage", "amara.org", "merci d'avoir regardé",
    "abonnez-vous", "n'oubliez pas de vous abonner", "sous titres", "♪",
)


SEUL_MOT_ACTIVATION = {"jarvis", "hey jarvis", "he jarvis", "eh jarvis", "et jarvis", "ok jarvis"}


def nettoyer(texte: str) -> str:
    t = " ".join(texte.split()).strip()
    bas = t.lower()
    if any(h in bas for h in HALLUCINATIONS):
        return ""
    if not re.search(r"\w", t):
        return ""
    if re.sub(r"[^\w ]", "", bas).strip() in SEUL_MOT_ACTIVATION:
        return ""  # seulement la fin de « Hey Jarvis » : ce n'est pas une question
    return t.strip("\"«» ")


class Transcripteur(Protocol):
    nom: str

    def transcrire(self, audio: np.ndarray) -> str: ...

    def prechauffer(self) -> None: ...


class MlxWhisper:
    def __init__(self, modele: str, invite: str | None) -> None:
        import mlx_whisper  # noqa: F401  (vérifie la présence du module dès la création)

        self.modele = modele
        self.invite = invite
        self.nom = f"Whisper · {modele.split('/')[-1]}"

    def transcrire(self, audio: np.ndarray) -> str:
        import mlx_whisper

        r = mlx_whisper.transcribe(
            audio.astype(np.float32), path_or_hf_repo=self.modele, language="fr", temperature=0.0,
            condition_on_previous_text=False, initial_prompt=self.invite, verbose=None,
        )
        return nettoyer(r.get("text", ""))

    def prechauffer(self) -> None:
        self.transcrire(np.zeros(16000, dtype=np.float32))


class Parakeet:
    def __init__(self, modele: str) -> None:
        from parakeet_mlx import from_pretrained

        self._modele = from_pretrained(modele)
        self.nom = f"Parakeet · {modele.split('/')[-1]}"

    def transcrire(self, audio: np.ndarray) -> str:
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        mel = get_logmel(mx.array(audio.astype(np.float32)), self._modele.preprocessor_config)
        return nettoyer(self._modele.generate(mel)[0].text)

    def prechauffer(self) -> None:
        self.transcrire(np.zeros(16000, dtype=np.float32))


class FasterWhisper:
    def __init__(self, modele: str, invite: str | None) -> None:
        from faster_whisper import WhisperModel

        self._modele = WhisperModel(modele, device="cpu", compute_type="int8")
        self.invite = invite
        self.nom = f"faster-whisper · {modele}"

    def transcrire(self, audio: np.ndarray) -> str:
        segments, _ = self._modele.transcribe(
            audio.astype(np.float32), language="fr", beam_size=1, condition_on_previous_text=False,
            initial_prompt=self.invite, vad_filter=False,
        )
        return nettoyer(" ".join(s.text for s in segments))

    def prechauffer(self) -> None:
        self.transcrire(np.zeros(16000, dtype=np.float32))


def creer_transcripteur(config: Config) -> Transcripteur:
    o = config.oreilles
    noms = [n for n in (config.general.nom, "Jarvis", *o.vocabulaire) if n]
    invite = ("Conversation avec Jarvis. Vocabulaire : " + ", ".join(noms) + ".") if noms else None
    moteur = o.moteur
    if moteur == "auto":
        moteur = "mlx-whisper" if IS_APPLE_SILICON else "faster-whisper"
    debut = time.perf_counter()
    if moteur == "mlx-whisper":
        t: Transcripteur = MlxWhisper(o.modele_whisper, invite)
    elif moteur == "parakeet":
        t = Parakeet(o.modele_parakeet)
    elif moteur == "faster-whisper":
        t = FasterWhisper(o.modele_faster_whisper, invite)
    else:
        raise ValueError(f"Moteur de transcription inconnu : {moteur}")
    log.info("Transcription : %s (prêt en %.1f s)", t.nom, time.perf_counter() - debut)
    return t
