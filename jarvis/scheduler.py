"""Rappels et minuteurs, stockés dans SQLite pour survivre à un redémarrage."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

log = logging.getLogger("jarvis.rappels")


@dataclass
class Rappel:
    id: int
    texte: str
    echeance: datetime
    type: str = "rappel"      # rappel | minuteur
    statut: str = "actif"     # actif | fait | annule | manque


class Planificateur:
    RETARD_MAX = timedelta(hours=12)  # au-delà, un rappel manqué n'est plus annoncé

    def __init__(self, chemin_db: Path | str,
                 quand_echu: Callable[[Rappel, bool], None] | None = None,
                 horloge: Callable[[], datetime] = datetime.now) -> None:
        self.quand_echu = quand_echu
        self.horloge = horloge
        self._verrou = threading.RLock()
        self._stop = threading.Event()
        self._fil: threading.Thread | None = None
        self.db = sqlite3.connect(str(chemin_db), check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS rappels (id INTEGER PRIMARY KEY, texte TEXT, echeance TEXT,"
            " type TEXT, statut TEXT, cree_le TEXT)"
        )
        self.db.commit()

    def demarrer(self) -> None:
        if self._fil and self._fil.is_alive():
            return
        self._stop.clear()
        self._fil = threading.Thread(target=self._boucle, name="jarvis-rappels", daemon=True)
        self._fil.start()

    def arreter(self) -> None:
        self._stop.set()

    def _boucle(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.verifier()
            except Exception:
                log.exception("Erreur pendant la vérification des rappels")

    # ------------------------------------------------------------------ API
    def ajouter(self, texte: str, echeance: datetime, type_: str = "rappel") -> Rappel:
        with self._verrou:
            cur = self.db.execute(
                "INSERT INTO rappels (texte, echeance, type, statut, cree_le) VALUES (?, ?, ?, 'actif', ?)",
                (texte, echeance.isoformat(timespec="seconds"), type_,
                 self.horloge().isoformat(timespec="seconds")),
            )
            self.db.commit()
            return Rappel(cur.lastrowid, texte, echeance, type_)

    def lister(self, actifs_seulement: bool = True) -> list[Rappel]:
        requete = "SELECT id, texte, echeance, type, statut FROM rappels"
        if actifs_seulement:
            requete += " WHERE statut='actif'"
        with self._verrou:
            lignes = self.db.execute(requete + " ORDER BY echeance").fetchall()
        return [Rappel(i, t, datetime.fromisoformat(e), ty, s) for i, t, e, ty, s in lignes]

    def annuler(self, ident: int) -> Rappel | None:
        with self._verrou:
            ligne = self.db.execute(
                "SELECT id, texte, echeance, type, statut FROM rappels WHERE id=? AND statut='actif'", (ident,)
            ).fetchone()
            if not ligne:
                return None
            self.db.execute("UPDATE rappels SET statut='annule' WHERE id=?", (ident,))
            self.db.commit()
        return Rappel(ligne[0], ligne[1], datetime.fromisoformat(ligne[2]), ligne[3], "annule")

    def verifier(self) -> list[Rappel]:
        """Déclenche les rappels arrivés à échéance. Renvoie ceux qui ont sonné."""
        maintenant = self.horloge()
        sonnes = []
        for r in self.lister():
            if r.echeance > maintenant:
                break  # triés par échéance : les suivants sont dans le futur
            retard = maintenant - r.echeance
            statut = "manque" if retard > self.RETARD_MAX else "fait"
            with self._verrou:
                self.db.execute("UPDATE rappels SET statut=? WHERE id=?", (statut, r.id))
                self.db.commit()
            if statut == "fait":
                r.statut = "fait"
                sonnes.append(r)
                if self.quand_echu:
                    try:
                        self.quand_echu(r, retard > timedelta(minutes=1))
                    except Exception:
                        log.exception("Erreur dans la notification du rappel %s", r.id)
        return sonnes
