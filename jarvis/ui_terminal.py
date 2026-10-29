"""Affichage soigné dans le terminal, sans dépendance (codes couleur ANSI)."""

from __future__ import annotations

import os
import shutil
import sys
import threading

from jarvis.brain import DemandeConfirmation
from jarvis.events import Bus

COULEUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _style(code: str):
    return (lambda t: f"\033[{code}m{t}\033[0m") if COULEUR else (lambda t: str(t))


arc = _style("38;2;116;220;255")
dore = _style("38;2;230;191;110")
rouge = _style("38;2;255;93;80")
vert = _style("38;2;127;224;166")
gris = _style("38;2;129;152;174")
pale = _style("38;2;75;99;120")
blanc = _style("38;2;243;251;255")
italique = _style("3")
gras = _style("1")


def degrade(texte: str, debut=(116, 220, 255), fin=(230, 191, 110)) -> str:
    if not COULEUR:
        return texte
    n = max(1, len(texte) - 1)
    sortie = []
    for i, c in enumerate(texte):
        r, g, b = (round(debut[k] + (fin[k] - debut[k]) * i / n) for k in range(3))
        sortie.append(f"\033[38;2;{r};{g};{b}m{c}")
    return "".join(sortie) + "\033[0m"


def banniere(sous_titre: str = "") -> None:
    largeur = max(44, min(64, shutil.get_terminal_size((80, 20)).columns - 4))
    titre = "J . A . R . V . I . S ."
    devise = "Just A Rather Very Intelligent System"
    print()
    print("  " + arc("╭" + "─" * (largeur - 2) + "╮"))
    print("  " + arc("│") + degrade(titre.center(largeur - 2)) + arc("│"))
    print("  " + arc("│") + italique(gris(devise.center(largeur - 2))) + arc("│"))
    print("  " + arc("╰" + "─" * (largeur - 2) + "╯"))
    if sous_titre:
        print("  " + gris(sous_titre))
    print()


def ligne_etat(statut: str, titre: str, detail: str = "") -> str:
    symboles = {"ok": vert("✓"), "ko": rouge("✗"), "attention": dore("!"), "info": arc("·")}
    return f"  {symboles.get(statut, '·')} {titre:<26} {gris(detail)}"


class AffichageTerminal:
    """Affiche les événements de Jarvis au fil de l'eau."""

    ETATS = {"ecoute": "◉ À l'écoute…", "transcription": "◌ Transcription…", "reflexion": "◎ Réflexion…",
             "veille": "○ En veille : dites « Hey Jarvis »", "confirmation": "⚠ Autorisation requise"}

    def __init__(self, bus: Bus, *, vocal: bool = False) -> None:
        self.vocal = vocal
        self.en_reponse = False
        self._verrou = threading.Lock()
        bus.abonner(self.evenement)

    def _fin_de_ligne(self) -> None:
        if self.en_reponse:
            print()
            self.en_reponse = False

    def evenement(self, e: dict) -> None:
        with self._verrou:
            genre = e["type"]
            if genre == "jeton":
                if not self.en_reponse:
                    print("  " + arc("Jarvis › "), end="")
                    self.en_reponse = True
                print(blanc(e["texte"]), end="", flush=True)
            elif genre == "outil":
                self._outil(e)
            elif genre == "reponse":
                self._fin_de_ligne()
                s = e.get("stats") or {}
                infos = []
                if s.get("premier_mot_ms") is not None:
                    infos.append(f"1er mot {s['premier_mot_ms']} ms")
                if s.get("jetons_par_s"):
                    infos.append(f"{s['jetons_par_s']:.1f} jetons/s".replace(".", ","))
                if s.get("outils"):
                    infos.append(f"{s['outils']} outil(s)")
                if e.get("interrompu"):
                    infos.append("interrompu")
                if infos:
                    print("  " + pale(" · ".join(infos)))
                print()
            elif genre == "transcription" and self.vocal:
                print("  " + gris("Vous (voix) › ") + italique(e.get("texte") or "…"))
            elif genre == "etat" and self.vocal and e["etat"] in self.ETATS:
                print("  " + pale(self.ETATS[e["etat"]]))
            elif genre == "rappel":
                self._fin_de_ligne()
                titre = "Minuteur terminé" if e.get("genre") == "minuteur" else "Rappel"
                print("\n  " + dore(f"⏰ {titre} : {e['texte']}") + "\n")
            elif genre == "souvenir":
                self._fin_de_ligne()
                print("  " + arc("◈ ") + gris(("Mémorisé : " if e.get("nouveau") else "Mis à jour : ") + e["texte"]))
            elif genre == "erreur":
                self._fin_de_ligne()
                print("  " + rouge("✗ " + e["texte"]))
            elif genre == "avertissement":
                self._fin_de_ligne()
                print("  " + dore("! " + e["texte"]))
            elif genre == "confirmation" and self.vocal:
                self._fin_de_ligne()
                print("  " + dore("⚠ Autorisation requise : ") + e.get("libelle", e.get("outil", "")))
                for k, v in (e.get("args") or {}).items():
                    print("    " + gris(f"{k} : ") + blanc(str(v)))
                print("  " + gris("Répondez « oui » / « non » à voix haute, ou tapez o / n puis Entrée."))

    def _outil(self, e: dict) -> None:
        phase = e.get("phase")
        if phase == "appel":
            self._fin_de_ligne()
            args = ", ".join(f"{k} : {v}" for k, v in (e.get("args") or {}).items())
            reseau = dore(" ↗ internet") if e.get("reseau") else ""
            print("  " + arc(f"{e.get('icone', '◆')} {e.get('libelle', e.get('nom'))}") + reseau
                  + (gris(f"  ({args})") if args else ""))
        elif phase == "resultat":
            symbole = vert("✓") if e.get("ok") else rouge("✗")
            print("    " + symbole + pale(f" {e.get('duree_ms', 0)} ms"))
        elif phase in ("refuse", "bloque", "erreur"):
            libelle = {"refuse": "refusé", "bloque": "bloqué par le pare-feu", "erreur": "erreur"}[phase]
            print("    " + rouge(f"✗ {libelle}") + (gris(f" · {e['resultat'][:140]}") if phase != "refuse" else ""))


def confirmer_terminal(demande: DemandeConfirmation) -> bool:
    """Demande d'autorisation dans le terminal (mode chat)."""
    print()
    print("  " + rouge("⚠ AUTORISATION REQUISE") + gris(f" · {demande.outil.libelle}"))
    for cle, valeur in demande.arguments.items():
        print("    " + gris(f"{cle} : ") + blanc(str(valeur)))
    if demande.raison:
        print("    " + italique(gris(demande.raison)))
    try:
        reponse = input("  " + dore("Autoriser ? [o/N] ")).strip().lower()
    except EOFError:
        return False
    return reponse in ("o", "oui", "y", "yes")
