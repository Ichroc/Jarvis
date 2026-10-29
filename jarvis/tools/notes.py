"""Notes personnelles, rangées en Markdown dans ~/.jarvis/notes (lisibles sans Jarvis)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from jarvis.textutils import normaliser, tronquer
from jarvis.tools import Contexte, outil


def _nom_fichier(titre: str) -> str:
    base = normaliser(titre).replace(" ", "-")[:60].strip("-")
    return (base or "note") + ".md"


@outil(
    description="Écrit une note (crée le fichier ou ajoute à la suite d'une note existante du même titre).",
    params={"titre": "Titre court de la note, ex. « Idées PFE ».", "contenu": "Texte à noter."},
    risque="action", categorie="notes", icone="✎", libelle="note",
)
def ecrire_note(ctx: Contexte, titre: str, contenu: str) -> str:
    dossier = ctx.config.dossier_notes
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / _nom_fichier(titre)
    horodatage = datetime.now().strftime("%d/%m/%Y %H:%M")
    nouveau = not chemin.exists()
    with open(chemin, "a", encoding="utf-8") as f:
        if nouveau:
            f.write(f"# {titre.strip()}\n")
        f.write(f"\n## {horodatage}\n\n{contenu.strip()}\n")
    return f"Note {'créée' if nouveau else 'complétée'} : {chemin.name}"


@outil(
    description="Liste les notes récentes ou cherche un mot dans les notes personnelles.",
    params={"recherche": "Mot ou expression à chercher (laisser vide pour lister les notes récentes)."},
    categorie="notes", prive=True, icone="☰", libelle="notes",
)
def lire_notes(ctx: Contexte, recherche: str = "") -> str:
    dossier = ctx.config.dossier_notes
    fichiers = sorted(dossier.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if dossier.exists() else []
    if not fichiers:
        return "Aucune note pour l'instant."
    if not recherche.strip():
        lignes = [f"- {_titre(f)} (modifiée le {datetime.fromtimestamp(f.stat().st_mtime):%d/%m %H:%M})"
                  for f in fichiers[:15]]
        return "Notes récentes :\n" + "\n".join(lignes)
    cible = normaliser(recherche)
    trouvailles = []
    for f in fichiers:
        texte = f.read_text(encoding="utf-8", errors="replace")
        if cible in normaliser(texte):
            trouvailles.append(f"### {_titre(f)}\n{tronquer(texte, 1500)}")
        if len(trouvailles) >= 3:
            break
    if not trouvailles:
        return f"Aucune note ne contient « {recherche} »."
    return "\n\n".join(trouvailles)


def _titre(chemin: Path) -> str:
    try:
        premiere = chemin.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        return re.sub(r"^#\s*", "", premiere).strip() or chemin.stem
    except (IndexError, OSError):
        return chemin.stem
