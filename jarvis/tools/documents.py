"""Recherche dans les documents indexés (cours, PDF, notes de projet…)."""

from __future__ import annotations

from pathlib import Path

from jarvis.security import envelopper_externe
from jarvis.tools import Contexte, outil


@outil(
    description=("Cherche dans les documents personnels indexés (cours, PDF, rapports, code) les passages "
                 "qui répondent à une question. Cite ensuite le nom des fichiers utilisés."),
    params={"question": "La question ou les mots-clés à chercher."},
    categorie="documents", prive=True, externe=True, icone="⌕", libelle="documents",
)
def chercher_documents(ctx: Contexte, question: str) -> str:
    stats = ctx.documents.stats()
    if not stats["morceaux"]:
        return ("Aucun document indexé. Pour en ajouter, lance dans un terminal : "
                "jarvis index ~/Documents/MonDossier")
    extraits = ctx.documents.rechercher(question, k=5)
    if not extraits:
        return f"Rien de pertinent dans les {stats['fichiers']} documents indexés."
    blocs = []
    for e in extraits:
        nom = Path(e.chemin).name
        blocs.append(f"[{nom} · extrait {e.rang + 1} · pertinence {e.score:.2f}]\n{e.texte}")
    return envelopper_externe("documents personnels", "\n\n".join(blocs))
