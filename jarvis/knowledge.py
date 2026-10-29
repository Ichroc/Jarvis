"""Recherche dans tes documents (RAG local).

« jarvis index ~/Documents/ESEO » découpe tes cours, PDF et notes en morceaux
d'environ 900 caractères, calcule un vecteur pour chacun et les range dans
SQLite. Quand tu poses une question, Jarvis retrouve les morceaux les plus
proches et répond en citant les fichiers. Rien ne quitte ta machine.

Une simple multiplication matricielle (numpy) suffit pour quelques dizaines
de milliers de morceaux : pas besoin de base vectorielle dédiée.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from jarvis.memory import Vectoriseur, score_lexical
from jarvis.textutils import html_vers_texte

log = logging.getLogger("jarvis.documents")

DOSSIERS_IGNORES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".cache", ".Trash",
    "Library", ".idea", ".vscode", "dist", "build", ".pytest_cache", ".mypy_cache",
}
TAILLE_MAX_FICHIER = 25 * 1024 * 1024


@dataclass
class Extrait:
    chemin: str
    rang: int
    texte: str
    score: float


def extraire_texte(chemin: Path) -> str:
    ext = chemin.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        lecteur = PdfReader(str(chemin))
        pages = []
        for i, page in enumerate(lecteur.pages[:400]):
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                log.debug("Page %d illisible dans %s", i, chemin)
        return "\n\n".join(pages)
    if ext == ".docx":
        import docx
        document = docx.Document(str(chemin))
        return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())
    if ext in (".html", ".htm"):
        titre, texte = html_vers_texte(chemin.read_text(encoding="utf-8", errors="replace"))
        return f"{titre}\n\n{texte}" if titre else texte
    return chemin.read_text(encoding="utf-8", errors="replace")


def decouper(texte: str, taille: int = 900, chevauchement: int = 150) -> list[str]:
    """Découpe en morceaux qui respectent autant que possible les paragraphes."""
    paragraphes = [p.strip() for p in texte.replace("\r", "").split("\n\n") if p.strip()]
    morceaux: list[str] = []
    courant = ""
    for p in paragraphes:
        while len(p) > taille:  # paragraphe géant : on le coupe sur une fin de phrase
            coupe = max(p.rfind(". ", 0, taille), p.rfind("\n", 0, taille))
            coupe = coupe + 1 if coupe > taille // 3 else taille
            if courant:
                morceaux.append(courant)
                courant = ""
            morceaux.append(p[:coupe].strip())
            p = p[coupe:].strip()
        if len(courant) + len(p) + 2 > taille and courant:
            morceaux.append(courant)
            courant = courant[-chevauchement:].split(" ", 1)[-1] if chevauchement else ""
        courant = f"{courant}\n\n{p}".strip() if courant else p
    if courant:
        morceaux.append(courant)
    return [m for m in morceaux if len(m.strip()) > 20]


class BaseDocuments:
    def __init__(self, chemin_db: Path | str, vectoriseur: Vectoriseur | None = None) -> None:
        self.vectoriseur = vectoriseur
        self._verrou = threading.RLock()
        self.db = sqlite3.connect(str(chemin_db), check_same_thread=False)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS fichiers (chemin TEXT PRIMARY KEY, empreinte TEXT, indexe_le TEXT, morceaux INT);"
            "CREATE TABLE IF NOT EXISTS morceaux (id INTEGER PRIMARY KEY, chemin TEXT, rang INT, texte TEXT,"
            " vecteur BLOB, modele TEXT);"
            "CREATE INDEX IF NOT EXISTS idx_morceaux_chemin ON morceaux(chemin);"
        )
        self.db.commit()
        self._cache: tuple[list[int], np.ndarray] | None = None

    # ------------------------------------------------------------------ indexation
    def indexer(self, dossiers: Iterable[str | Path], extensions: Iterable[str],
                rapport: Callable[[str, int, int], None] | None = None) -> dict:
        extensions = {e.lower() for e in extensions}
        racines = [Path(d).expanduser().resolve() for d in dossiers]
        fichiers = [f for r in racines if r.exists() for f in _parcourir(r, extensions)]
        stats = {"vus": len(fichiers), "nouveaux": 0, "mis_a_jour": 0, "inchanges": 0,
                 "retires": 0, "morceaux": 0, "erreurs": 0}
        presents = set()
        for i, f in enumerate(fichiers, 1):
            chemin = str(f)
            presents.add(chemin)
            try:
                st = f.stat()
                empreinte = f"{st.st_size}-{int(st.st_mtime)}"
                with self._verrou:
                    ancien = self.db.execute("SELECT empreinte FROM fichiers WHERE chemin=?", (chemin,)).fetchone()
                if ancien and ancien[0] == empreinte:
                    stats["inchanges"] += 1
                    continue
                if rapport:
                    rapport(chemin, i, len(fichiers))
                morceaux = decouper(extraire_texte(f))
                vecteurs = self._vecteurs_par_lots(morceaux)
                with self._verrou:
                    self.db.execute("DELETE FROM morceaux WHERE chemin=?", (chemin,))
                    for rang, texte in enumerate(morceaux):
                        v = None if vecteurs is None else vecteurs[rang].astype(np.float32).tobytes()
                        modele = None if vecteurs is None else self.vectoriseur.modele
                        self.db.execute(
                            "INSERT INTO morceaux (chemin, rang, texte, vecteur, modele) VALUES (?, ?, ?, ?, ?)",
                            (chemin, rang, texte, v, modele),
                        )
                    self.db.execute(
                        "INSERT OR REPLACE INTO fichiers (chemin, empreinte, indexe_le, morceaux) VALUES (?, ?, ?, ?)",
                        (chemin, empreinte, datetime.now().isoformat(timespec="seconds"), len(morceaux)),
                    )
                    self.db.commit()
                stats["mis_a_jour" if ancien else "nouveaux"] += 1
                stats["morceaux"] += len(morceaux)
            except Exception as e:
                stats["erreurs"] += 1
                log.warning("Impossible d'indexer %s : %s", f, e)
        # Fichiers supprimés du disque : on les retire de l'index.
        with self._verrou:
            for (chemin,) in self.db.execute("SELECT chemin FROM fichiers").fetchall():
                if chemin not in presents and any(chemin.startswith(str(r) + os.sep) for r in racines):
                    self.db.execute("DELETE FROM morceaux WHERE chemin=?", (chemin,))
                    self.db.execute("DELETE FROM fichiers WHERE chemin=?", (chemin,))
                    stats["retires"] += 1
            self.db.commit()
            self._cache = None
        return stats

    def _vecteurs_par_lots(self, textes: list[str], lot: int = 32) -> np.ndarray | None:
        if not textes or not self.vectoriseur:
            return None
        parties = []
        for i in range(0, len(textes), lot):
            v = self.vectoriseur(textes[i:i + lot])
            if v is None:
                return None
            parties.append(v)
        return np.concatenate(parties)

    # ------------------------------------------------------------------ recherche
    def rechercher(self, question: str, k: int = 5, max_par_fichier: int = 2) -> list[Extrait]:
        vq = self.vectoriseur.requete(question, "documents") if self.vectoriseur else None
        candidats: list[tuple[float, int]] = []
        if vq is not None:
            ids, matrice = self._matrice()
            if len(ids) and matrice.shape[1] == vq.shape[1]:
                scores = matrice @ vq[0]
                ordre = np.argsort(-scores)[: k * 6]
                candidats = [(float(scores[i]), ids[i]) for i in ordre if scores[i] > 0.3]
        if not candidats:  # repli : mots-clés
            with self._verrou:
                lignes = self.db.execute("SELECT id, texte FROM morceaux").fetchall()
            notes = sorted(((score_lexical(question, t), i) for i, t in lignes), reverse=True)
            candidats = [(s, i) for s, i in notes[: k * 6] if s > 0.15]
        resultat: list[Extrait] = []
        par_fichier: dict[str, int] = {}
        with self._verrou:
            for score, ident in candidats:
                ligne = self.db.execute("SELECT chemin, rang, texte FROM morceaux WHERE id=?", (ident,)).fetchone()
                if not ligne:
                    continue
                if par_fichier.get(ligne[0], 0) >= max_par_fichier:
                    continue
                par_fichier[ligne[0]] = par_fichier.get(ligne[0], 0) + 1
                resultat.append(Extrait(ligne[0], ligne[1], ligne[2], round(score, 3)))
                if len(resultat) >= k:
                    break
        return resultat

    def _matrice(self) -> tuple[list[int], np.ndarray]:
        if self._cache is None:
            modele = self.vectoriseur.modele if self.vectoriseur else ""
            with self._verrou:
                lignes = self.db.execute(
                    "SELECT id, vecteur FROM morceaux WHERE vecteur IS NOT NULL AND modele=?", (modele,)
                ).fetchall()
            if lignes:
                self._cache = ([l[0] for l in lignes],
                               np.stack([np.frombuffer(l[1], dtype=np.float32) for l in lignes]))
            else:
                self._cache = ([], np.zeros((0, 0), dtype=np.float32))
        return self._cache

    def stats(self) -> dict:
        with self._verrou:
            f = self.db.execute("SELECT COUNT(*) FROM fichiers").fetchone()[0]
            m = self.db.execute("SELECT COUNT(*) FROM morceaux").fetchone()[0]
        return {"fichiers": f, "morceaux": m}


def _parcourir(racine: Path, extensions: set[str]):
    if racine.is_file():
        if racine.suffix.lower() in extensions:
            yield racine
        return
    for dossier, sous_dossiers, fichiers in os.walk(racine):
        sous_dossiers[:] = [d for d in sous_dossiers if not d.startswith(".") and d not in DOSSIERS_IGNORES]
        for nom in fichiers:
            if nom.startswith(".") or nom.startswith("~$"):
                continue
            chemin = Path(dossier) / nom
            if chemin.suffix.lower() in extensions:
                try:
                    if chemin.stat().st_size <= TAILLE_MAX_FICHIER:
                        yield chemin
                except OSError:
                    continue
