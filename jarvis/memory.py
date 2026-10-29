"""Mémoire à long terme de Jarvis.

Chaque souvenir est une phrase (« Ichroc commence son stage le 2 février »)
transformée en vecteur par un petit modèle d'embeddings. À chaque question, on
cherche les souvenirs dont le vecteur est le plus proche (similarité cosinus)
et on les glisse dans le contexte. C'est du RAG appliqué à ta vie, en local.

Si le modèle d'embeddings est absent, Jarvis se rabat sur une recherche par
mots-clés : moins fine, mais la mémoire continue de fonctionner.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from jarvis.textutils import normaliser

if TYPE_CHECKING:
    from jarvis.llm import Ollama

log = logging.getLogger("jarvis.memoire")

MOTS_VIDES = set(
    "le la les de du des un une et a au aux en que qui quoi est je tu il elle on nous vous ils elles "
    "mon ma mes ton ta tes son sa ses ce cet cette ces pour par sur dans avec pas ne se y d l j m t s n "
    "qu c ou mais donc or ni car moi toi lui leur leurs notre nos votre vos tout tous plus tres bien "
    "etre avoir fait faire suis es sont ai as avons avez ont quel quelle quels quelles comment quand".split()
)


def mots_cles(texte: str) -> set[str]:
    return {m for m in normaliser(texte).split() if m not in MOTS_VIDES and len(m) > 1}


def score_lexical(a: str, b: str) -> float:
    ma, mb = mots_cles(a), mots_cles(b)
    if not ma or not mb:
        return 0.0
    return len(ma & mb) / math.sqrt(len(ma) * len(mb))


INSTRUCTIONS = {
    "souvenirs": "Given a question or statement from the user, retrieve personal facts about the user that help answer it",
    "documents": "Given a question, retrieve passages from personal documents that answer it",
}


class Vectoriseur:
    """Transforme des textes en vecteurs normalisés via Ollama, sans jamais faire planter Jarvis."""

    def __init__(self, llm: "Ollama | None", modele: str) -> None:
        self.llm = llm
        self.modele = modele
        self._en_panne_depuis = 0.0

    @property
    def disponible(self) -> bool:
        # Après un échec, on réessaie toutes les 60 s (Ollama a pu être relancé).
        return self.llm is not None and time.monotonic() - self._en_panne_depuis > 60

    def __call__(self, textes: list[str]) -> np.ndarray | None:
        if not textes or not self.disponible:
            return None
        try:
            v = np.asarray(self.llm.vecteurs(self.modele, textes), dtype=np.float32)
        except Exception as e:
            log.warning("Vecteurs indisponibles (%s) : recherche par mots-clés.", e)
            self._en_panne_depuis = time.monotonic()
            return None
        if v.ndim != 2 or len(v) != len(textes):
            return None
        normes = np.linalg.norm(v, axis=1, keepdims=True)
        normes[normes == 0] = 1.0
        return v / normes

    def requete(self, texte: str, tache: str) -> np.ndarray | None:
        """Vecteur d'une question. Les modèles Qwen3-Embedding sont plus précis quand la question
        est précédée d'une consigne (les textes stockés, eux, restent bruts)."""
        if self.modele.startswith("qwen3-embedding"):
            texte = f"Instruct: {INSTRUCTIONS[tache]}\nQuery: {texte}"
        return self([texte])


@dataclass
class Souvenir:
    id: int
    texte: str
    cree_le: str
    score: float = 0.0


class Memoire:
    SEUIL_VECTEUR = 0.45      # similarité minimale pour qu'un souvenir soit jugé utile
    ECART_MAX = 0.15          # et pas trop loin du meilleur (évite les souvenirs « vaguement liés »)
    SEUIL_MOTS = 0.34
    SEUIL_DOUBLON_VECTEUR = 0.93
    SEUIL_DOUBLON_MOTS = 0.85

    def __init__(self, chemin_db: Path | str, vectoriseur: Vectoriseur | None = None) -> None:
        self.vectoriseur = vectoriseur
        self._verrou = threading.RLock()
        self.db = sqlite3.connect(str(chemin_db), check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS souvenirs ("
            " id INTEGER PRIMARY KEY, texte TEXT NOT NULL, cree_le TEXT NOT NULL,"
            " vecteur BLOB, modele TEXT)"
        )
        self.db.commit()

    # ------------------------------------------------------------------ écriture
    def ajouter(self, texte: str) -> tuple[Souvenir, bool]:
        """Ajoute un souvenir. Renvoie (souvenir, nouveau) ; un quasi-doublon met à jour l'existant."""
        texte = " ".join(texte.split())
        if not texte:
            raise ValueError("souvenir vide")
        vecteur = self._vecteurs([texte])
        proches = self._classer(texte, None if vecteur is None else vecteur[0], k=1)
        with self._verrou:
            if proches:
                meilleur, mode = proches[0]
                seuil = self.SEUIL_DOUBLON_VECTEUR if mode == "vecteur" else self.SEUIL_DOUBLON_MOTS
                if meilleur.score >= seuil:
                    self.db.execute(
                        "UPDATE souvenirs SET texte=?, vecteur=?, modele=? WHERE id=?",
                        (texte, self._blob(vecteur), self._modele(vecteur), meilleur.id),
                    )
                    self.db.commit()
                    return Souvenir(meilleur.id, texte, meilleur.cree_le, meilleur.score), False
            cree_le = datetime.now().isoformat(timespec="seconds")
            cur = self.db.execute(
                "INSERT INTO souvenirs (texte, cree_le, vecteur, modele) VALUES (?, ?, ?, ?)",
                (texte, cree_le, self._blob(vecteur), self._modele(vecteur)),
            )
            self.db.commit()
            return Souvenir(cur.lastrowid, texte, cree_le), True

    def oublier(self, ident: int) -> Souvenir | None:
        with self._verrou:
            ligne = self.db.execute("SELECT id, texte, cree_le FROM souvenirs WHERE id=?", (ident,)).fetchone()
            if not ligne:
                return None
            self.db.execute("DELETE FROM souvenirs WHERE id=?", (ident,))
            self.db.commit()
            return Souvenir(*ligne)

    def oublier_requete(self, requete: str) -> list[Souvenir]:
        cibles = self.rechercher(requete, k=10)
        return [s for s in (self.oublier(c.id) for c in cibles) if s]

    # ------------------------------------------------------------------ lecture
    def rechercher(self, requete: str, k: int = 3) -> list[Souvenir]:
        vecteur = self.vectoriseur.requete(requete, "souvenirs") if self.vectoriseur else None
        classes = self._classer(requete, None if vecteur is None else vecteur[0], k=k)
        resultat = []
        meilleur = classes[0][0].score if classes else 0.0
        for s, mode in classes:
            if mode == "vecteur":
                if s.score >= self.SEUIL_VECTEUR and s.score >= meilleur - self.ECART_MAX:
                    resultat.append(s)
            elif s.score >= self.SEUIL_MOTS:
                resultat.append(s)
        return resultat

    def tout(self) -> list[Souvenir]:
        with self._verrou:
            lignes = self.db.execute("SELECT id, texte, cree_le FROM souvenirs ORDER BY id").fetchall()
        return [Souvenir(*l) for l in lignes]

    def compter(self) -> int:
        with self._verrou:
            return self.db.execute("SELECT COUNT(*) FROM souvenirs").fetchone()[0]

    # ------------------------------------------------------------------ interne
    def _vecteurs(self, textes: list[str]) -> np.ndarray | None:
        return self.vectoriseur(textes) if self.vectoriseur else None

    def _modele(self, vecteur) -> str | None:
        return self.vectoriseur.modele if (vecteur is not None and self.vectoriseur) else None

    @staticmethod
    def _blob(vecteur) -> bytes | None:
        return None if vecteur is None else np.asarray(vecteur[0], dtype=np.float32).tobytes()

    def _classer(self, requete: str, vecteur: np.ndarray | None, k: int) -> list[tuple[Souvenir, str]]:
        with self._verrou:
            lignes = self.db.execute("SELECT id, texte, cree_le, vecteur, modele FROM souvenirs").fetchall()
        if not lignes:
            return []
        if vecteur is not None:
            modele = self.vectoriseur.modele
            # Souvenirs encore sans vecteur (créés hors ligne ou autre modèle) : on les complète.
            a_completer = [l for l in lignes if l[3] is None or l[4] != modele]
            if a_completer:
                nouveaux = self._vecteurs([l[1] for l in a_completer])
                if nouveaux is not None:
                    with self._verrou:
                        for l, v in zip(a_completer, nouveaux):
                            self.db.execute("UPDATE souvenirs SET vecteur=?, modele=? WHERE id=?",
                                            (v.astype(np.float32).tobytes(), modele, l[0]))
                        self.db.commit()
                    return self._classer(requete, vecteur, k)
            matrice = np.stack([np.frombuffer(l[3], dtype=np.float32) for l in lignes])
            if matrice.shape[1] == vecteur.shape[0]:
                scores = matrice @ vecteur
                ordre = np.argsort(-scores)[:k]
                return [(Souvenir(lignes[i][0], lignes[i][1], lignes[i][2], float(scores[i])), "vecteur")
                        for i in ordre]
        scores = [score_lexical(requete, l[1]) for l in lignes]
        ordre = sorted(range(len(lignes)), key=lambda i: -scores[i])[:k]
        return [(Souvenir(lignes[i][0], lignes[i][1], lignes[i][2], scores[i]), "mots") for i in ordre]
