"""Bus d'événements : chaque partie de Jarvis publie ce qu'elle fait,
et l'interface (HUD ou terminal) s'abonne pour l'afficher.

C'est ce découplage qui permet d'avoir plusieurs interfaces en même temps
sans que le cerveau ne sache qu'elles existent."""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Callable

log = logging.getLogger("jarvis.events")

# Événements gardés en mémoire pour qu'une interface qui se (re)connecte
# retrouve la conversation en cours.
HISTORISES = {"utilisateur", "reponse", "outil", "rappel", "souvenir", "erreur", "confirmation_fin"}


class Bus:
    def __init__(self, taille_historique: int = 300) -> None:
        self._abonnes: list[Callable[[dict], None]] = []
        self._verrou = threading.Lock()
        self.historique: collections.deque[dict] = collections.deque(maxlen=taille_historique)

    def abonner(self, rappel: Callable[[dict], None]) -> Callable[[], None]:
        with self._verrou:
            self._abonnes.append(rappel)

        def desabonner() -> None:
            with self._verrou:
                if rappel in self._abonnes:
                    self._abonnes.remove(rappel)

        return desabonner

    def publier(self, type_: str, **donnees) -> dict:
        evt = {"type": type_, "t": time.time(), **donnees}
        with self._verrou:
            if type_ in HISTORISES:
                self.historique.append(evt)
            abonnes = list(self._abonnes)
        for rappel in abonnes:
            try:
                rappel(evt)
            except Exception:  # une interface en panne ne doit pas arrêter Jarvis
                log.exception("Abonné en erreur pour l'événement %s", type_)
        return evt
