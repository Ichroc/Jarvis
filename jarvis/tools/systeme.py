"""Piloter le Mac : applications, volume, musique, presse-papiers, notifications, écran.

Toutes les commandes sont lancées SANS shell (liste d'arguments) : un texte
fourni par le modèle ne peut jamais être interprété comme une commande.
Les textes passés à AppleScript transitent par « argv », jamais par
concaténation, pour la même raison.
"""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

from jarvis.textutils import tronquer
from jarvis.tools import Contexte, outil

MAC = sys.platform == "darwin"


class CommandeIndisponible(RuntimeError):
    pass


def lancer(args: list[str], *, entree: str | None = None, delai: float = 10) -> str:
    if not shutil.which(args[0]):
        raise CommandeIndisponible(f"« {args[0]} » n'est pas disponible sur ce système.")
    r = subprocess.run(args, input=entree, capture_output=True, text=True, timeout=delai)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"code {r.returncode}").strip()[:300])
    return r.stdout


def applescript(script: str, *arguments: str) -> str:
    """Exécute un AppleScript ; les textes variables sont passés en arguments (argv)."""
    lignes = ["on run argv", *script.strip().splitlines(), "end run"]
    args = ["osascript"]
    for ligne in lignes:
        args += ["-e", ligne]
    return lancer(args + list(arguments)).strip()


def _premiere_dispo(*commandes: list[str], entree: str | None = None) -> str:
    erreurs = []
    for c in commandes:
        try:
            return lancer(c, entree=entree)
        except (CommandeIndisponible, RuntimeError) as e:
            erreurs.append(str(e))
    raise CommandeIndisponible(" ; ".join(erreurs))


@outil(
    description="Ouvre une application par son nom (ex. « Safari », « Visual Studio Code », « Notes »).",
    params={"nom": "Nom exact de l'application."},
    risque="action", categorie="systeme", icone="⇱", libelle="ouvrir",
)
def ouvrir_application(nom: str) -> str:
    if MAC:
        lancer(["open", "-a", nom])
    else:
        lancer(["gtk-launch", nom])
    return f"Application « {nom} » ouverte."


@outil(
    description="Règle le volume du son de l'ordinateur (0 = muet, 100 = maximum).",
    params={"niveau": "Niveau entre 0 et 100."},
    risque="action", categorie="systeme", icone="◖", libelle="volume",
)
def regler_volume(niveau: int) -> str:
    niveau = max(0, min(100, niveau))
    if MAC:
        applescript("set volume output volume (item 1 of argv as integer)", str(niveau))
    else:
        _premiere_dispo(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{niveau}%"],
                        ["amixer", "-q", "sset", "Master", f"{niveau}%"])
    return f"Volume réglé à {niveau} %."


@outil(
    description="Contrôle la musique : lecture, pause, morceau suivant ou précédent, ou titre en cours (info).",
    params={"action": "lecture, pause, suivant, precedent ou info."},
    risque="action", categorie="systeme", icone="♪", libelle="musique",
)
def controle_musique(ctx: Contexte, action: Literal["lecture", "pause", "suivant", "precedent", "info"]) -> str:
    if MAC:
        # Le nom vient de ta configuration, mais on le filtre quand même : il finit dans un AppleScript.
        app = re.sub(r"[^\w .-]", "", ctx.config.interface.lecteur_musique) or "Music"
        commandes = {"lecture": "play", "pause": "pause", "suivant": "next track", "precedent": "previous track"}
        if action == "info":
            info = applescript(
                f'tell application "{app}"\n'
                'if player state is playing then\n'
                'return (name of current track) & " — " & (artist of current track)\n'
                'else\nreturn ""\nend if\nend tell'
            )
            return f"En cours : {info}." if info else "Aucune musique en lecture."
        applescript(f'tell application "{app}" to {commandes[action]}')
        return f"Musique : {action}."
    commandes = {"lecture": "play", "pause": "pause", "suivant": "next", "precedent": "previous"}
    if action == "info":
        info = lancer(["playerctl", "metadata", "--format", "{{title}} — {{artist}}"]).strip()
        return f"En cours : {info}." if info else "Aucune musique en lecture."
    lancer(["playerctl", commandes[action]])
    return f"Musique : {action}."


@outil(
    description="Lit le texte actuellement copié dans le presse-papiers (pour le résumer, traduire, corriger…).",
    categorie="systeme", prive=True, externe=True, icone="⎘", libelle="presse-papiers",
)
def lire_presse_papiers() -> str:
    if MAC:
        texte = lancer(["pbpaste"])
    else:
        texte = _premiere_dispo(["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"])
    if not texte.strip():
        return "Le presse-papiers est vide (ou contient une image)."
    from jarvis.security import envelopper_externe
    return envelopper_externe("presse-papiers", tronquer(texte, 6000))


@outil(
    description="Copie un texte dans le presse-papiers pour que l'utilisateur puisse le coller.",
    params={"texte": "Texte à copier."},
    risque="action", categorie="systeme", icone="⎗", libelle="copier",
)
def copier_presse_papiers(texte: str) -> str:
    if MAC:
        lancer(["pbcopy"], entree=texte)
    else:
        _premiere_dispo(["wl-copy"], ["xclip", "-selection", "clipboard"], entree=texte)
    return f"Copié dans le presse-papiers ({len(texte)} caractères)."


@outil(
    description="Affiche une notification système à l'écran.",
    params={"titre": "Titre court.", "message": "Texte de la notification."},
    risque="action", categorie="systeme", icone="◉", libelle="notification",
)
def notifier(titre: str, message: str) -> str:
    envoyer_notification(titre, message)
    return "Notification affichée."


def envoyer_notification(titre: str, message: str) -> None:
    if MAC:
        applescript('display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
                    titre, message)
    else:
        lancer(["notify-send", "--app-name=Jarvis", titre, message])


@outil(
    description=("Prend une capture de l'écran et l'analyse avec la vision du modèle pour répondre à une "
                 "question sur ce qui est affiché (code, erreur, page, document…)."),
    params={"question": "Ce que l'utilisateur veut savoir sur son écran."},
    categorie="systeme", prive=True, icone="◐", libelle="vision",
)
def regarder_ecran(ctx: Contexte, question: str) -> str:
    modele = ctx.config.cerveau.modele
    if "vision" not in ctx.llm.capacites(modele):
        return f"Le modèle {modele} ne voit pas les images. Utilise un modèle « vision » (ex. qwen3.5:9b)."
    with tempfile.TemporaryDirectory() as dossier:
        image = Path(dossier) / "ecran.jpg"
        if MAC:
            lancer(["screencapture", "-x", "-t", "jpg", str(image)])
            if shutil.which("sips"):  # réduit l'image : plus rapide et moins de mémoire
                lancer(["sips", "-Z", "1800", str(image)])
        else:
            _premiere_dispo(["grim", str(image)], ["gnome-screenshot", "-f", str(image)],
                            ["import", "-window", "root", str(image)])
        if not image.exists() or image.stat().st_size == 0:
            return ("Capture impossible. Sur Mac, autorise ton terminal dans Réglages Système > "
                    "Confidentialité et sécurité > Enregistrement de l'écran.")
        donnees = base64.b64encode(image.read_bytes()).decode()
    reponse = ctx.llm.demander(
        modele,
        [{"role": "user", "content": (
            "Voici une capture de l'écran de l'utilisateur. Réponds en français, précisément et sans "
            f"inventer, à cette question : {question}"), "images": [donnees]}],
        reflexion=False if "thinking" in ctx.llm.capacites(modele) else None,
        options={"temperature": 0.2},
    )
    return f"Analyse de l'écran : {reponse.strip()}"


@outil(
    description="Met l'écran en veille (et le verrouille si un mot de passe est exigé au réveil).",
    risque="action", categorie="systeme", icone="⏻", libelle="verrouiller",
)
def verrouiller_ecran() -> str:
    if MAC:
        lancer(["pmset", "displaysleepnow"])
    else:
        lancer(["loginctl", "lock-session"])
    return "Écran verrouillé."
