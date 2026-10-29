"""Mémoire à long terme : retenir, retrouver, oublier."""

from __future__ import annotations

from jarvis.tools import Contexte, outil


@outil(
    description=("Retient durablement une information sur l'utilisateur. À utiliser seulement quand il "
                 "demande de retenir quelque chose, ou pour une préférence durable clairement exprimée."),
    params={"fait": "Phrase autonome et précise, ex. « Le stage d'Ichroc commence le 2 février 2027 »."},
    risque="action", categorie="memoire", icone="◈", libelle="mémoriser",
)
def memoriser(ctx: Contexte, fait: str) -> str:
    souvenir, nouveau = ctx.memoire.ajouter(fait)
    if ctx.bus:
        ctx.bus.publier("souvenir", texte=souvenir.texte, nouveau=nouveau)
    return f"{'Mémorisé' if nouveau else 'Souvenir mis à jour'} (n°{souvenir.id}) : {souvenir.texte}"


@outil(
    description="Cherche dans la mémoire à long terme ce que l'utilisateur a demandé de retenir.",
    params={"recherche": "Sujet à retrouver, ex. « stage », « allergies », « anniversaire de Sam »."},
    categorie="memoire", prive=True, icone="◇", libelle="souvenirs",
)
def rappeler_souvenirs(ctx: Contexte, recherche: str) -> str:
    trouves = ctx.memoire.rechercher(recherche, k=6)
    if not trouves:
        return f"Aucun souvenir sur « {recherche} »."
    return "\n".join(f"n°{s.id} ({s.cree_le[:10]}) : {s.texte}" for s in trouves)


@outil(
    description="Efface définitivement de la mémoire les souvenirs correspondant à une recherche.",
    params={"recherche": "Ce qu'il faut oublier."},
    risque="sensible", categorie="memoire", icone="⌫", libelle="oubli",
)
def oublier(ctx: Contexte, recherche: str) -> str:
    effaces = ctx.memoire.oublier_requete(recherche)
    if not effaces:
        return f"Aucun souvenir ne correspond à « {recherche} »."
    return "Oublié :\n" + "\n".join(f"- {s.texte}" for s in effaces)
