"""Le cerveau de Jarvis : la boucle « agent ».

    question ─► modèle ─► réponse finale
                  │  ▲
                  ▼  │
             appel d'outil ─► pare-feu ─► (ton accord ?) ─► exécution ─► résultat

Le modèle ne fait jamais rien lui-même : il demande un outil, Jarvis vérifie
que c'est permis, l'exécute et lui renvoie le résultat. La boucle s'arrête
quand le modèle répond sans demander d'outil (ou après quelques étapes).

Tout est produit sous forme d'événements (dict) : jetons de texte, appels
d'outils, fin de réponse avec statistiques. L'interface choisit quoi en faire.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Iterator

from jarvis import tools as registre
from jarvis.llm import OllamaErreur
from jarvis.persona import bloc_contexte, prompt_systeme
from jarvis.security import Decision, Journal, Politique, Tour
from jarvis.textutils import tronquer

if TYPE_CHECKING:
    import threading

    from jarvis.config import Config
    from jarvis.llm import AppelOutil, Ollama
    from jarvis.memory import Memoire
    from jarvis.tools import Contexte, Outil

log = logging.getLogger("jarvis.cerveau")

LIMITE_RESULTAT = 6000  # caractères renvoyés au modèle par outil (le reste est tronqué)


@dataclass
class DemandeConfirmation:
    ident: str
    outil: "Outil"
    arguments: dict
    raison: str


Confirmateur = Callable[[DemandeConfirmation], bool]


class Cerveau:
    def __init__(self, config: "Config", llm: "Ollama", ctx: "Contexte", politique: Politique,
                 journal: Journal, memoire: "Memoire | None" = None) -> None:
        self.config = config
        self.llm = llm
        self.ctx = ctx
        self.politique = politique
        self.journal = journal
        self.memoire = memoire
        self.tours: list[list[dict]] = []
        self._capacites: set[str] | None = None
        self._executeur = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-outil")

    # ------------------------------------------------------------------ utilitaires
    def capacites(self) -> set[str]:
        if self._capacites is None:
            try:
                self._capacites = self.llm.capacites(self.config.cerveau.modele)
            except OllamaErreur:
                return {"completion", "tools"}  # on réessaiera à la prochaine demande
        return self._capacites

    def oublier_conversation(self) -> None:
        self.tours.clear()

    def _historique(self, budget_car: int | None = None) -> list[dict]:
        """Les échanges précédents, en partant des plus récents, tant qu'ils tiennent dans le budget.
        On retire toujours des tours entiers pour ne jamais couper un appel d'outil de son résultat."""
        if budget_car is None:
            return [m for tour in self.tours for m in tour]
        gardes: list[list[dict]] = []
        total = 0
        for tour in reversed(self.tours):
            taille = sum(len(json.dumps(m, ensure_ascii=False)) for m in tour)
            if total + taille > budget_car:
                break
            gardes.append(tour)
            total += taille
        return [m for tour in reversed(gardes) for m in tour]

    def _ranger(self, messages: list[dict]) -> None:
        self.tours.append([{k: v for k, v in m.items() if k not in ("thinking", "images")} for m in messages])
        del self.tours[:-40]  # au-delà, ces échanges ne tiendraient de toute façon plus dans le contexte

    @staticmethod
    def budget_historique(contexte: int, taille_fixe_car: int, reserve_reponse: int) -> int:
        """Place laissée à l'historique, en caractères (≈ 3 caractères par jeton en français)."""
        jetons_fixes = taille_fixe_car / 3.0
        return max(1500, int((contexte - jetons_fixes - reserve_reponse - 400) * 3.0))

    def debut_fixe(self, confidentiel: bool = False) -> tuple[dict, list[dict] | None]:
        """Le début de chaque demande, identique d'une fois sur l'autre : la personnalité et les outils.
        C'est grâce à lui qu'Ollama peut réutiliser son calcul d'une demande à l'autre (cache de préfixe)."""
        caps = self.capacites()
        outils = registre.disponibles(self.config, confidentiel=confidentiel) if "tools" in caps else []
        return {"role": "system", "content": prompt_systeme(self.config)}, [o.schema() for o in outils] or None

    def prechauffer(self, confidentiel: bool = False) -> None:
        """Charge le modèle et lui fait lire, une fois pour toutes, le début fixe de chaque demande
        (≈ 3 600 jetons de personnalité et d'outils). Ollama garde ce calcul en mémoire : même la
        première vraie question repart de là. Les options sont celles des vraies demandes, sinon
        Ollama rechargerait le modèle."""
        cfg = self.config.cerveau
        systeme, schemas = self.debut_fixe(confidentiel)
        caps = self.capacites()
        for _ in self.llm.discuter(cfg.modele, [systeme, {"role": "user", "content": "Bonjour."}], outils=schemas,
                                   reflexion=False if "thinking" in caps else None,
                                   options={"temperature": cfg.temperature, "num_ctx": cfg.contexte, "num_predict": 1},
                                   garder=cfg.garder_charge):
            pass

    # ------------------------------------------------------------------ boucle principale
    def demander(self, texte: str, *, confirmer: Confirmateur, mode_vocal: bool = False,
                 annuler: "threading.Event | None" = None, confidentiel: bool = False,
                 images: list[str] | None = None) -> Iterator[dict]:
        cfg = self.config.cerveau
        debut = time.perf_counter()
        tour = Tour()
        yield {"type": "debut", "texte": texte}

        souvenirs = []
        if self.memoire:
            try:
                souvenirs = self.memoire.rechercher(texte, k=3)
            except Exception:
                log.exception("Recherche de souvenirs impossible")
        if souvenirs:
            yield {"type": "contexte", "souvenirs": [s.texte for s in souvenirs]}

        message_utilisateur: dict = {
            "role": "user",
            "content": f"{bloc_contexte(self.config, datetime.now(), souvenirs, mode_vocal)}\n\n{texte}",
        }
        if images:
            message_utilisateur["images"] = images
        nouveaux: list[dict] = [message_utilisateur]
        systeme, schemas = self.debut_fixe(confidentiel)
        caps = self.capacites()
        reflexion = (cfg.reflexion and not mode_vocal) if "thinking" in caps else None
        reserve = 500 if mode_vocal else 1500
        options = {"temperature": cfg.temperature, "num_ctx": cfg.contexte, "num_predict": reserve}
        # Le prompt système, les outils et la réponse doivent tenir dans le contexte : l'historique prend le reste.
        taille_fixe = len(systeme["content"]) + len(json.dumps(schemas or [], ensure_ascii=False)) \
            + len(message_utilisateur["content"])
        marge_outils = 1500  # place pour les résultats d'outils de cette demande
        historique = self._historique(self.budget_historique(cfg.contexte, taille_fixe, reserve + marge_outils))

        stats = {"etapes": 0, "jetons": 0, "duree_generation_ns": 0, "jetons_lus": 0,
                 "premier_mot_ms": None, "outils": 0}
        reponse = ""
        interrompu = False
        try:
            for etape in range(cfg.etapes_max + 1):
                derniere_chance = etape == cfg.etapes_max  # dernière étape : plus d'outils, il faut répondre
                contenu, pensee, appels = "", "", []
                for m in self.llm.discuter(cfg.modele, [systeme, *historique, *nouveaux],
                                           outils=None if derniere_chance else schemas,
                                           reflexion=reflexion, options=options, garder=cfg.garder_charge):
                    if annuler is not None and annuler.is_set():
                        interrompu = True
                        break
                    if m.pensee:
                        pensee += m.pensee
                        yield {"type": "pensee", "texte": m.pensee}
                    if m.contenu:
                        if stats["premier_mot_ms"] is None:
                            stats["premier_mot_ms"] = round((time.perf_counter() - debut) * 1000)
                        contenu += m.contenu
                        yield {"type": "jeton", "texte": m.contenu}
                    appels.extend(m.appels)
                    if m.fini and m.stats:
                        stats["jetons"] += m.stats["eval_count"]
                        stats["duree_generation_ns"] += m.stats["eval_duration"]
                        stats["jetons_lus"] += m.stats["prompt_eval_count"]
                stats["etapes"] += 1

                message_assistant: dict = {"role": "assistant", "content": contenu}
                if pensee:
                    message_assistant["thinking"] = pensee
                if appels:
                    message_assistant["tool_calls"] = [a.en_message() for a in appels]
                nouveaux.append(message_assistant)

                if interrompu:
                    reponse = contenu
                    break
                if not appels:
                    reponse = contenu
                    break
                for appel in appels:
                    resultat = yield from self._executer(appel, tour, confirmer)
                    stats["outils"] += 1
                    nouveaux.append({"role": "tool", "tool_name": appel.nom, "content": resultat})
                    if annuler is not None and annuler.is_set():
                        interrompu = True
                        break
                if interrompu:
                    break
        except OllamaErreur as e:
            yield {"type": "erreur", "message": str(e)}
            return
        except Exception as e:  # on ne laisse jamais une erreur tuer Jarvis
            log.exception("Erreur inattendue dans le cerveau")
            yield {"type": "erreur", "message": f"Erreur inattendue : {e}"}
            return

        if interrompu:
            dernier = nouveaux[-1]
            if dernier["role"] == "assistant":
                dernier.pop("tool_calls", None)  # appels jamais exécutés : on ne les garde pas
                dernier["content"] = (dernier.get("content") or "") + " [interrompu]"
            else:
                nouveaux.append({"role": "assistant", "content": "[interrompu]"})
        self._ranger(nouveaux)
        duree = time.perf_counter() - debut
        stats["duree_ms"] = round(duree * 1000)
        stats["jetons_par_s"] = round(stats["jetons"] / (stats["duree_generation_ns"] / 1e9), 1) \
            if stats["duree_generation_ns"] else 0.0
        del stats["duree_generation_ns"]
        yield {"type": "fin", "texte": reponse, "interrompu": interrompu, "stats": stats}

    # ------------------------------------------------------------------ outils
    def _executer(self, appel: "AppelOutil", tour: Tour, confirmer: Confirmateur):
        """Exécute un appel d'outil (générateur : produit des événements, renvoie le résultat texte)."""
        ident = uuid.uuid4().hex[:10]
        outil = registre.obtenir(appel.nom)
        if outil is None:
            noms = ", ".join(o.nom for o in registre.disponibles(self.config))
            resultat = f"Erreur : l'outil « {appel.nom} » n'existe pas. Outils disponibles : {noms}."
            yield {"type": "outil", "phase": "erreur", "id": ident, "nom": appel.nom, "args": appel.arguments,
                   "resultat": resultat, "icone": "?", "libelle": appel.nom}
            return resultat

        base = {"type": "outil", "id": ident, "nom": outil.nom, "icone": outil.icone, "libelle": outil.libelle,
                "risque": outil.risque, "reseau": outil.reseau}
        try:
            args = outil.preparer_arguments(appel.arguments)
        except registre.ArgumentInvalide as e:
            resultat = f"Erreur d'arguments pour {outil.nom} : {e}. Corrige l'appel."
            yield {**base, "phase": "erreur", "args": appel.arguments, "resultat": resultat}
            return resultat

        args_affiches = {k: tronquer(_affichable(v), 400) for k, v in args.items()}
        decision, raison = self.politique.evaluer(outil, args, tour)
        yield {**base, "phase": "appel", "args": args_affiches, "decision": decision.value, "raison": raison}

        accord = None
        if decision is Decision.BLOQUER:
            self.journal.ecrire(evenement="outil", outil=outil.nom, arguments=args, decision="bloque", raison=raison)
            resultat = f"Action bloquée par le pare-feu de Jarvis : {raison} Ne réessaie pas."
            yield {**base, "phase": "bloque", "args": args_affiches, "resultat": resultat}
            return resultat
        if decision is Decision.CONFIRMER:
            accord = bool(confirmer(DemandeConfirmation(ident, outil, args, raison)))
            if not accord:
                self.journal.ecrire(evenement="outil", outil=outil.nom, arguments=args, decision="refuse")
                resultat = ("L'utilisateur a refusé cette action. Ne la réessaie pas : "
                            "propose une alternative ou demande ce qu'il souhaite.")
                yield {**base, "phase": "refuse", "args": args_affiches, "resultat": resultat}
                return resultat

        debut = time.perf_counter()
        ok = True
        try:
            futur = self._executeur.submit(outil.appeler, self.ctx, args)
            brut = futur.result(timeout=120)
            resultat = brut if isinstance(brut, str) else (
                "OK" if brut is None else json.dumps(brut, ensure_ascii=False, default=str))
        except concurrent.futures.TimeoutError:
            ok, resultat = False, f"L'outil {outil.nom} n'a pas répondu à temps."
        except Exception as e:
            ok, resultat = False, f"Erreur pendant {outil.nom} : {e}"
            log.info("Outil %s en erreur : %s", outil.nom, e)
        duree_ms = round((time.perf_counter() - debut) * 1000)
        resultat = tronquer(resultat, LIMITE_RESULTAT)
        if outil.prive and ok:
            tour.marquer(outil.libelle)
        self.journal.ecrire(evenement="outil", outil=outil.nom, arguments=args,
                            decision="accorde" if accord else "auto", ok=ok, duree_ms=duree_ms,
                            resultat=resultat)
        yield {**base, "phase": "resultat", "args": args_affiches, "ok": ok, "duree_ms": duree_ms,
               "resultat": tronquer(_sans_enveloppe(resultat), 700)}
        return resultat


def _affichable(valeur) -> str:
    """10.0 s'affiche « 10 », le reste tel quel."""
    if isinstance(valeur, float) and valeur.is_integer():
        return str(int(valeur))
    return str(valeur)


def _sans_enveloppe(texte: str) -> str:
    """Pour l'affichage : retire le cadre « contenu externe » ajouté pour le modèle."""
    if texte.startswith("<<CONTENU_EXTERNE"):
        lignes = texte.splitlines()
        return "\n".join(lignes[2:-1]) if len(lignes) > 3 else texte
    return texte
