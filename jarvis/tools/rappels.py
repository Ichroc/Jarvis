"""Rappels et minuteurs."""

from __future__ import annotations

from datetime import datetime, timedelta

from jarvis.textutils import date_longue
from jarvis.tools import Contexte, outil


def _quand(d: datetime) -> str:
    aujourd_hui = datetime.now().date()
    if d.date() == aujourd_hui:
        jour = "aujourd'hui"
    elif d.date() == aujourd_hui + timedelta(days=1):
        jour = "demain"
    else:
        jour = date_longue(d)
    return f"{jour} à {d:%H:%M}"


def _lire_date(texte: str) -> datetime:
    t = texte.strip().replace(" ", "T", 1) if "T" not in texte else texte.strip()
    d = datetime.fromisoformat(t)
    if d.tzinfo is not None:  # on travaille en heure locale
        d = d.astimezone().replace(tzinfo=None)
    return d


@outil(
    description=("Crée un rappel. Donne SOIT « dans_minutes » (délai relatif), SOIT « date_heure » "
                 "(ISO 8601, ex. 2026-10-28T18:30, calculée à partir de la date du contexte)."),
    params={
        "texte": "Ce dont il faut se souvenir.",
        "dans_minutes": "Délai en minutes à partir de maintenant.",
        "date_heure": "Date et heure exactes au format ISO 8601 (heure locale).",
    },
    risque="action", categorie="rappels", icone="⏰", libelle="rappel",
)
def creer_rappel(ctx: Contexte, texte: str, dans_minutes: float | None = None,
                 date_heure: str | None = None) -> str:
    maintenant = datetime.now()
    if dans_minutes is not None:
        if dans_minutes <= 0:
            return "Erreur : le délai doit être positif."
        echeance = maintenant + timedelta(minutes=dans_minutes)
    elif date_heure:
        try:
            echeance = _lire_date(date_heure)
        except ValueError:
            return f"Erreur : date « {date_heure} » illisible. Utilise le format 2026-10-28T18:30."
        if echeance <= maintenant:
            return f"Erreur : {echeance:%d/%m/%Y %H:%M} est déjà passé. Vérifie la date."
    else:
        return "Erreur : précise « dans_minutes » ou « date_heure »."
    r = ctx.planificateur.ajouter(texte, echeance, "rappel")
    return f"Rappel n°{r.id} programmé {_quand(echeance)} : « {texte} »."


@outil(
    description="Lance un minuteur (compte à rebours) qui sonnera à la fin.",
    params={"minutes": "Durée en minutes (peut être décimale, 0.5 = 30 secondes).",
            "libelle": "Nom du minuteur, ex. « pâtes »."},
    risque="action", categorie="rappels", icone="⧗", libelle="minuteur",
)
def minuteur(ctx: Contexte, minutes: float, libelle: str = "Minuteur") -> str:
    if not 0 < minutes <= 24 * 60:
        return "Erreur : un minuteur dure entre quelques secondes et 24 heures."
    echeance = datetime.now() + timedelta(minutes=minutes)
    r = ctx.planificateur.ajouter(libelle, echeance, "minuteur")
    duree = f"{int(minutes)} min" if float(minutes).is_integer() else f"{int(minutes * 60)} s"
    return f"Minuteur n°{r.id} « {libelle} » lancé pour {duree} (fin à {echeance:%H:%M:%S})."


@outil(
    description="Liste les rappels et minuteurs en cours.",
    categorie="rappels", icone="☷", libelle="rappels",
)
def lister_rappels(ctx: Contexte) -> str:
    rappels = ctx.planificateur.lister()
    if not rappels:
        return "Aucun rappel ni minuteur en cours."
    return "\n".join(f"n°{r.id} · {r.type} · {_quand(r.echeance)} · {r.texte}" for r in rappels)


@outil(
    description="Annule un rappel ou un minuteur à partir de son numéro.",
    params={"numero": "Numéro du rappel (voir lister_rappels)."},
    risque="action", categorie="rappels", icone="⊘", libelle="annulation",
)
def annuler_rappel(ctx: Contexte, numero: int) -> str:
    r = ctx.planificateur.annuler(numero)
    if r is None:
        return f"Aucun rappel actif n°{numero}."
    return f"{r.type.capitalize()} n°{r.id} annulé : « {r.texte} »."
