"""Entrée/sortie audio avec sounddevice (PortAudio).

Le micro est lu en blocs de 80 ms (1280 échantillons à 16 kHz) : c'est la
taille attendue par le détecteur de mot d'activation. Le rappel audio ne fait
que copier le bloc dans une file : on ne bloque jamais le fil temps réel.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
from typing import Callable, Iterable

import numpy as np

log = logging.getLogger("jarvis.audio")

FREQ = 16000
BLOC = 1280


class AudioIndisponible(RuntimeError):
    pass


def _sounddevice():
    try:
        import sounddevice as sd
    except ImportError as e:
        raise AudioIndisponible("Le module sounddevice n'est pas installé (uv sync).") from e
    except OSError as e:
        raise AudioIndisponible(
            "PortAudio est introuvable. Sur Linux : sudo apt install libportaudio2 ; sur Mac, réinstalle sounddevice."
        ) from e
    return sd


def niveau(bloc: np.ndarray) -> float:
    """Niveau sonore perçu entre 0 et 1 (échelle en décibels, de -60 dB à -10 dB)."""
    if bloc.size == 0:
        return 0.0
    x = bloc.astype(np.float32)
    if bloc.dtype == np.int16:
        x /= 32768.0
    rms = float(np.sqrt(np.mean(x * x))) + 1e-9
    db = 20 * math.log10(rms)
    return max(0.0, min(1.0, (db + 60) / 50))


def peripheriques() -> dict:
    sd = _sounddevice()
    entree, sortie = sd.default.device
    liste = sd.query_devices()
    return {
        "entree": liste[entree]["name"] if entree is not None and entree >= 0 else None,
        "sortie": liste[sortie]["name"] if sortie is not None and sortie >= 0 else None,
        "tous": [d["name"] for d in liste],
    }


class Micro:
    def __init__(self, peripherique: str = "") -> None:
        self.peripherique = int(peripherique) if peripherique.isdigit() else (peripherique or None)
        self.file: queue.Queue[np.ndarray] = queue.Queue(maxsize=250)
        self._flux = None

    def demarrer(self) -> None:
        sd = _sounddevice()
        self._flux = sd.InputStream(samplerate=FREQ, channels=1, dtype="int16", blocksize=BLOC,
                                    device=self.peripherique, callback=self._rappel)
        self._flux.start()

    def _rappel(self, donnees, frames, temps, statut) -> None:  # fil temps réel : aucune attente ici
        try:
            self.file.put_nowait(donnees[:, 0].copy())
        except queue.Full:
            pass

    def lire(self, delai: float = 0.5) -> np.ndarray | None:
        try:
            return self.file.get(timeout=delai)
        except queue.Empty:
            return None

    def vider(self) -> None:
        while True:
            try:
                self.file.get_nowait()
            except queue.Empty:
                return

    def arreter(self) -> None:
        if self._flux is not None:
            self._flux.stop()
            self._flux.close()
            self._flux = None


def _doubler_frequence(x: np.ndarray) -> np.ndarray:
    """24 kHz -> 48 kHz par interpolation linéaire (si la carte son refuse 24 kHz)."""
    if x.size == 0:
        return x
    y = np.empty(x.size * 2, dtype=np.float32)
    xf = x.astype(np.float32)
    y[0::2] = xf
    y[1::2] = np.concatenate([(xf[:-1] + xf[1:]) / 2, xf[-1:]])
    return y.astype(np.int16)


class HautParleur:
    """Joue un flux d'échantillons int16 au fur et à mesure de leur arrivée."""

    def __init__(self, freq: int = 24000) -> None:
        self.freq = freq

    def jouer(self, morceaux: Iterable[np.ndarray], stop: threading.Event,
              sur_niveau: Callable[[float], None] | None = None) -> bool:
        sd = _sounddevice()
        doubler = False
        try:
            flux = sd.OutputStream(samplerate=self.freq, channels=1, dtype="int16")
        except Exception:
            flux = sd.OutputStream(samplerate=self.freq * 2, channels=1, dtype="int16")
            doubler = True
        pas = self.freq // 10  # écrit par tranches de 100 ms : l'interruption reste immédiate
        with flux:
            for morceau in morceaux:
                for i in range(0, len(morceau), pas):
                    if stop.is_set():
                        flux.abort()
                        return False
                    part = morceau[i:i + pas]
                    if sur_niveau:
                        sur_niveau(niveau(part))
                    flux.write((_doubler_frequence(part) if doubler else part).reshape(-1, 1))
            if stop.is_set():
                flux.abort()
                return False
        return True


def carillon(freq: int = 24000) -> np.ndarray:
    """Petit son d'activation (deux notes douces), généré mathématiquement."""
    morceaux = []
    for note, duree in ((880.0, 0.07), (1318.5, 0.11)):
        t = np.arange(int(freq * duree)) / freq
        enveloppe = np.minimum(1, t * 120) * np.exp(-t * 18)
        morceaux.append(0.22 * np.sin(2 * np.pi * note * t) * enveloppe)
    return (np.concatenate(morceaux) * 32767).astype(np.int16)
