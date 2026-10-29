"""Détection de la parole (VAD) avec Silero VAD v6 en ONNX (2,3 Mo, MIT).

Le modèle reçoit des trames de 32 ms (512 échantillons) et renvoie la
probabilité qu'elles contiennent de la voix. L'Enregistreur s'en sert pour
savoir quand tu commences à parler et quand tu as fini.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np

from jarvis.config import MODELES_DIR

TRAME = 512
CONTEXTE = 64


class SileroVAD:
    def __init__(self, chemin: Path = MODELES_DIR / "silero_vad.onnx") -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(chemin), sess_options=options, providers=["CPUExecutionProvider"])
        self.reinitialiser()

    def reinitialiser(self) -> None:
        self.etat = np.zeros((2, 1, 128), dtype=np.float32)
        self.contexte = np.zeros((1, CONTEXTE), dtype=np.float32)
        self.tampon = np.zeros(0, dtype=np.float32)

    def probabilite(self, trame: np.ndarray) -> float:
        x = np.concatenate([self.contexte, trame.reshape(1, TRAME).astype(np.float32)], axis=1)
        sortie, self.etat = self.session.run(
            None, {"input": x, "state": self.etat, "sr": np.array(16000, dtype=np.int64)}
        )
        self.contexte = x[:, -CONTEXTE:]
        return float(sortie[0][0])

    def traiter(self, bloc: np.ndarray) -> list[float]:
        """Accepte un bloc de n'importe quelle taille (int16 ou float32)."""
        x = bloc.astype(np.float32) / 32768.0 if bloc.dtype == np.int16 else bloc.astype(np.float32)
        self.tampon = np.concatenate([self.tampon, x])
        probas = []
        while len(self.tampon) >= TRAME:
            probas.append(self.probabilite(self.tampon[:TRAME]))
            self.tampon = self.tampon[TRAME:]
        return probas


class Enregistreur:
    """Capte une phrase : attend que tu parles, puis s'arrête après un court silence."""

    def __init__(self, vad: SileroVAD, *, seuil: float = 0.5, silence_fin_ms: int = 700,
                 duree_max_s: float = 20.0, attente_max_s: float = 6.0, preroll_ms: int = 320,
                 parole_min_ms: int = 250) -> None:
        self.vad = vad
        self.seuil = seuil
        self.silence_fin_ms = silence_fin_ms
        self.duree_max_ms = duree_max_s * 1000
        self.attente_max_ms = attente_max_s * 1000
        self.parole_min_ms = parole_min_ms
        self._preroll = deque(maxlen=max(1, preroll_ms // 80))
        self.commencer()

    def commencer(self, attente_max_s: float | None = None, ignorer_ms: float = 0.0) -> None:
        """ignorer_ms : juste après « Hey Jarvis », la fin du mot d'activation ne doit pas être
        prise pour le début de la question."""
        if attente_max_s is not None:
            self.attente_max_ms = attente_max_s * 1000
        self.vad.reinitialiser()
        self._preroll.clear()
        self.blocs: list[np.ndarray] = []
        self.parle = False
        self.attente_ms = 0.0
        self.silence_ms = 0.0
        self.parole_ms = 0.0
        self.ignorer_ms = ignorer_ms

    def ajouter(self, bloc: np.ndarray) -> str:
        """Renvoie « attente », « parole », « fin » (phrase complète) ou « rien » (personne n'a parlé)."""
        ms = len(bloc) / 16.0
        if self.ignorer_ms > 0:
            self.ignorer_ms -= ms
            self.attente_ms += ms
            self._preroll.append(bloc)
            return "attente"
        probas = self.vad.traiter(bloc)
        p = max(probas) if probas else 0.0
        if not self.parle:
            self._preroll.append(bloc)
            self.attente_ms += ms
            if p >= self.seuil:
                self.parle = True
                self.blocs = list(self._preroll)  # on garde le début du premier mot
                self.parole_ms = ms
                return "parole"
            return "rien" if self.attente_ms >= self.attente_max_ms else "attente"
        self.blocs.append(bloc)
        if p >= self.seuil - 0.15:  # hystérésis : évite de couper sur une hésitation
            self.silence_ms = 0.0
            self.parole_ms += ms
        else:
            self.silence_ms += ms
        duree = sum(len(b) for b in self.blocs) / 16.0
        if duree >= self.duree_max_ms:
            return "fin"
        if self.silence_ms >= self.silence_fin_ms:
            if self.parole_ms >= self.parole_min_ms:
                return "fin"
            # Bruit trop bref (toux, porte, écho du carillon) : on se remet à attendre la vraie phrase.
            self.parle = False
            self.blocs = []
            self.silence_ms = self.parole_ms = 0.0
            return "rien" if self.attente_ms >= self.attente_max_ms else "attente"
        return "parole"

    def audio(self) -> np.ndarray:
        if not self.blocs:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.blocs).astype(np.float32) / 32768.0
