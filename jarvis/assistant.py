"""Le chef d'orchestre : relie le cerveau, la voix, les oreilles, la mémoire et les rappels.

Une seule demande est traitée à la fois (file d'attente), dans un fil dédié.
Tout ce qui se passe est publié sur le bus d'événements : le HUD et le
terminal n'ont qu'à écouter.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from jarvis import __version__
from jarvis import tools as registre
from jarvis.brain import Cerveau, DemandeConfirmation, _affichable
from jarvis.config import Config
from jarvis.events import Bus
from jarvis.knowledge import BaseDocuments
from jarvis.llm import ModeleIntrouvable, Ollama, OllamaErreur
from jarvis.memory import Memoire, Vectoriseur
from jarvis.scheduler import Planificateur, Rappel
from jarvis.security import Journal, Politique
from jarvis.textutils import DecoupeurPhrases, tronquer
from jarvis.tools import Contexte

log = logging.getLogger("jarvis")


class Assistant:
    def __init__(self, config: Config | None = None, *, avec_voix: bool = True, avec_oreilles: bool = True,
                 bus: Bus | None = None, llm: Ollama | None = None,
                 confirmateur: Callable[[DemandeConfirmation], bool] | None = None) -> None:
        self.config = config or Config.charger()
        self.config.preparer_dossiers()
        self.bus = bus or Bus()
        self.llm = llm or Ollama(self.config.cerveau.hote_ollama)
        vectoriseur = Vectoriseur(self.llm, self.config.cerveau.modele_embeddings)
        self.memoire = Memoire(self.config.base, vectoriseur)
        self.documents = BaseDocuments(self.config.base, vectoriseur)
        self.planificateur = Planificateur(self.config.base, quand_echu=self._rappel_echu)
        self.ctx = Contexte(config=self.config, llm=self.llm, memoire=self.memoire, documents=self.documents,
                            planificateur=self.planificateur, bus=self.bus)
        registre.charger_tous()
        self.journal = Journal(self.config.journal_audit, actif=self.config.securite.journal)
        self.politique = Politique(mode_prudent=self.config.securite.mode_prudent)
        self.cerveau = Cerveau(self.config, self.llm, self.ctx, self.politique, self.journal, self.memoire)

        self.confidentiel = not self.config.securite.internet
        self.voix_active = avec_voix and self.config.voix.active
        self.avec_oreilles = avec_oreilles and self.config.oreilles.active
        self.confirmateur_externe = confirmateur
        self.orateur = None
        self.oreilles = None
        self.etat = "demarrage"
        self.occupe = False
        self.composants: dict[str, dict] = {}
        self.dernieres_stats: dict = {}

        self._file: queue.Queue = queue.Queue()
        self._annuler = threading.Event()
        self._confirmations: dict[str, tuple[threading.Event, dict]] = {}
        self._confirmation_vocale: str | None = None
        self._relances_vocales = 0
        self._actif = False
        self.prechauffage_fini = threading.Event()
        self._detail_cerveau = ""

    # ================================================================== cycle de vie
    def demarrer(self) -> None:
        self._actif = True
        self.planificateur.demarrer()
        threading.Thread(target=self._boucle_travail, name="jarvis-cerveau", daemon=True).start()
        threading.Thread(target=self._boucle_telemetrie, name="jarvis-telemetrie", daemon=True).start()
        self._verifier_cerveau()
        chauffer = bool(self.composants.get("cerveau", {}).get("ok")) and self.config.cerveau.prechauffage
        if chauffer:
            cerveau = self.composants["cerveau"]
            self._detail_cerveau = cerveau.get("detail", "")
            cerveau.update(ok=None, nom="préchauffage…")
        else:
            self.prechauffage_fini.set()
        self.composants["memoire"] = {"ok": True, "nom": f"{self.memoire.compter()} souvenirs"}
        stats = self.documents.stats()
        self.composants["documents"] = {"ok": True, "nom": f"{stats['fichiers']} fichiers indexés"}
        self.composants["pare_feu"] = {"ok": True, "nom": "mode prudent" if self.politique.mode_prudent else "actif"}
        if self.voix_active:
            self._preparer_voix()
        else:
            self.composants["voix"] = {"ok": None, "desactive": True, "nom": "désactivée"}
        if self.avec_oreilles:
            self.composants["oreilles"] = {"ok": None, "nom": "chargement…"}
            threading.Thread(target=self._preparer_oreilles, name="jarvis-chargement", daemon=True).start()
        else:
            self.composants["oreilles"] = {"ok": None, "desactive": True, "nom": "désactivées"}
        self.changer_etat("inactif")
        self.bus.publier("pret", resume=self.resume())
        if chauffer:
            threading.Thread(target=self._prechauffer, name="jarvis-prechauffage", daemon=True).start()

    def _prechauffer(self) -> None:
        """Charge les modèles et prépare le début fixe des demandes, pendant que tu lis l'écran d'accueil.
        Une question posée entre-temps attend simplement la fin du préchauffage, puis en profite."""
        cerveau = self.composants["cerveau"]
        debut = time.perf_counter()
        try:
            self.cerveau.prechauffer(confidentiel=self.confidentiel)
            if self.memoire.vectoriseur is not None:
                self.memoire.vectoriseur(["prêt"])  # charge aussi le modèle de la mémoire
            voix = getattr(self.orateur, "voix", None)
            if voix is not None and not voix.joue_seule:
                try:
                    for _ in voix.synthese("Prêt."):  # et la voix, sans rien jouer
                        pass
                except Exception as e:
                    log.info("Préchauffage de la voix impossible : %s", e)
            duree = _duree(time.perf_counter() - debut)
            cerveau.update(ok=True, nom=self.config.cerveau.modele,
                           detail=" · ".join(x for x in (self._detail_cerveau, f"préchauffé en {duree}") if x))
            self.bus.publier("info", texte=f"Cerveau prêt : préchauffé en {duree}.")
        except Exception as e:
            log.warning("Préchauffage impossible : %s", e)
            cerveau.update(ok=False, nom=self.config.cerveau.modele, detail=str(e))
            self.bus.publier("avertissement", texte=f"Préchauffage du cerveau impossible : {e}")
        finally:
            self.prechauffage_fini.set()
            self.bus.publier("composants", composants=self.composants,
                             mot_activation=bool(self.oreilles and self.oreilles.detecteur))

    def arreter(self) -> None:
        self._actif = False
        self.interrompre()
        if self.oreilles:
            self.oreilles.arreter()
        if self.orateur:
            self.orateur.fermer()
        self.planificateur.arreter()
        self._file.put(None)

    def resume(self) -> dict:
        g = self.config.general
        return {
            "version": __version__, "nom": g.nom, "appellation": g.appellation, "ville": g.ville,
            "modele": self.config.cerveau.modele, "etat": self.etat, "confidentiel": self.confidentiel,
            "voix_active": self.voix_active, "composants": self.composants,
            "mot_activation": bool(self.oreilles and self.oreilles.detecteur),
            "outils": [{"nom": o.nom, "libelle": o.libelle, "icone": o.icone, "risque": o.risque,
                        "reseau": o.reseau} for o in registre.disponibles(self.config, confidentiel=self.confidentiel)],
            "stats": self.dernieres_stats,
        }

    def _verifier_cerveau(self) -> None:
        modele = self.config.cerveau.modele
        try:
            version = self.llm.version()
            installes = self.llm.noms_modeles()
            if modele not in installes:
                raise ModeleIntrouvable(modele)
            caps = self.llm.capacites(modele)
            detail = " · ".join(c for c in ("tools", "vision", "thinking") if c in caps)
            self.composants["cerveau"] = {"ok": True, "nom": modele, "detail": f"Ollama {version} · {detail}"}
            if self.config.cerveau.modele_embeddings not in installes:
                self.bus.publier("avertissement", texte=(
                    f"Modèle d'embeddings absent : mémoire en mode mots-clés. "
                    f"Lance : ollama pull {self.config.cerveau.modele_embeddings}"))
        except OllamaErreur as e:
            self.composants["cerveau"] = {"ok": False, "nom": modele, "detail": str(e)}
            self.bus.publier("avertissement", texte=str(e))

    def _preparer_voix(self) -> None:
        from jarvis.voice.tts import Orateur, creer_voix
        try:
            voix = creer_voix(self.config, self.llm)
            self.orateur = Orateur(
                voix,
                sur_debut=lambda: self.changer_etat("parole"),
                sur_niveau=lambda n: self.bus.publier("niveau", source="voix", valeur=round(n, 3)),
            )
            self.composants["voix"] = {"ok": voix.nom != "aucune", "nom": voix.nom}
        except Exception as e:
            self.composants["voix"] = {"ok": False, "nom": "indisponible", "detail": str(e)}
            self.bus.publier("avertissement", texte=f"Voix indisponible : {e}")

    def _preparer_oreilles(self) -> None:
        try:
            from jarvis.voice.audio import Micro
            from jarvis.voice.oreilles import Oreilles
            from jarvis.voice.stt import creer_transcripteur
            from jarvis.voice.vad import Enregistreur, SileroVAD
            from jarvis.voice.wakeword import DetecteurMotActivation

            o = self.config.oreilles
            transcripteur = creer_transcripteur(self.config)
            transcripteur.prechauffer()
            detecteur = DetecteurMotActivation(seuil=o.seuil_activation) if o.mot_activation else None
            enregistreur = Enregistreur(SileroVAD(), seuil=o.seuil_voix, silence_fin_ms=o.silence_fin_ms)
            self.oreilles = Oreilles(self, Micro(o.micro), detecteur, enregistreur, transcripteur)
            self.oreilles.demarrer()
            self.composants["oreilles"] = {"ok": True, "nom": transcripteur.nom,
                                           "detail": "« Hey Jarvis » actif" if detecteur else "bouton micro"}
            if self.etat == "inactif":
                self.changer_etat("veille")
        except Exception as e:
            log.warning("Oreilles indisponibles : %s", e)
            self.oreilles = None
            self.composants["oreilles"] = {"ok": False, "nom": "indisponibles", "detail": str(e)}
            self.bus.publier("avertissement", texte=f"Écoute vocale indisponible : {e}")
        self.bus.publier("composants", composants=self.composants, mot_activation=bool(
            self.oreilles and self.oreilles.detecteur))

    # ================================================================== entrées
    def demander(self, texte: str, source: str = "texte", ms_transcription: int | None = None) -> None:
        texte = texte.strip()
        if texte:
            self._file.put((texte[:4000], source, ms_transcription))

    def ecouter(self) -> bool:
        """Demande d'écoute immédiate (bouton micro)."""
        if not self.oreilles:
            detail = (self.composants.get("oreilles") or {}).get("detail") or "écoute désactivée"
            self.bus.publier("avertissement", texte=f"Le micro n'est pas disponible ({detail}).")
            return False
        if self.orateur and self.orateur.parle:
            self.orateur.stop()
        self.oreilles.declencher()
        return True

    def interrompre(self) -> None:
        self._annuler.set()
        if self.orateur:
            self.orateur.stop()
        for ev, boite in list(self._confirmations.values()):
            boite["accord"] = False
            ev.set()
        self.bus.publier("interruption")

    def repondre_confirmation(self, ident: str, accord: bool) -> bool:
        entree = self._confirmations.get(ident)
        if not entree:
            return False
        ev, boite = entree
        boite["accord"] = bool(accord)
        ev.set()
        return True

    def confirmation_en_attente(self) -> bool:
        return self._confirmation_vocale is not None

    def reponse_vocale_confirmation(self, texte: str) -> None:
        from jarvis.voice.oreilles import interpreter_accord
        ident = self._confirmation_vocale
        accord = interpreter_accord(texte)
        if ident is None:
            return
        if accord is None and self._relances_vocales < 1 and self.orateur and self.oreilles:
            self._relances_vocales += 1
            self.orateur.dire("Je n'ai pas compris. Répondez simplement oui ou non.")
            self.orateur.attendre(15)
            self.oreilles.ecouter_suite(6.0)
            return
        self.repondre_confirmation(ident, bool(accord))

    def fin_ecoute_sans_parole(self) -> None:
        if self._confirmation_vocale:
            return  # la confirmation reste ouverte à l'écran
        if not self.occupe:
            self.changer_etat("veille" if self.oreilles else "inactif")

    def regler(self, *, confidentiel: bool | None = None, voix_active: bool | None = None) -> None:
        if confidentiel is not None:
            self.confidentiel = bool(confidentiel)
        if voix_active is not None:
            self.voix_active = bool(voix_active) and self.orateur is not None
            if not self.voix_active and self.orateur:
                self.orateur.stop()
        self.bus.publier("reglages", confidentiel=self.confidentiel, voix_active=self.voix_active,
                         outils=self.resume()["outils"])

    def oublier_conversation(self) -> None:
        self.cerveau.oublier_conversation()
        self.bus.historique.clear()
        self.bus.publier("conversation_effacee")

    # ================================================================== traitement
    def changer_etat(self, etat: str) -> None:
        if etat != self.etat:
            self.etat = etat
            self.bus.publier("etat", etat=etat)

    def _boucle_travail(self) -> None:
        while self._actif:
            element = self._file.get()
            if element is None:
                return
            try:
                self.traiter(*element)
            except Exception:
                log.exception("Erreur pendant le traitement d'une demande")
                self.occupe = False
                self._retour_au_repos()

    def traiter(self, texte: str, source: str = "texte", ms_transcription: int | None = None) -> str:
        """Traite une demande de bout en bout (bloquant). Renvoie la réponse."""
        self.occupe = True
        self._annuler.clear()
        parler = bool(self.orateur and self.voix_active and
                      (source == "voix" or self.config.voix.lire_reponses_ecrites))
        self.bus.publier("utilisateur", texte=texte, source=source)
        self.changer_etat("reflexion")
        decoupeur = DecoupeurPhrases()
        debut = time.perf_counter()
        premiere_phrase_ms = None
        reponse = ""
        for evt in self.cerveau.demander(texte, confirmer=self._confirmer, mode_vocal=source == "voix",
                                         annuler=self._annuler, confidentiel=self.confidentiel):
            genre = evt["type"]
            donnees = {k: v for k, v in evt.items() if k != "type"}
            if genre == "jeton":
                self.bus.publier("jeton", **donnees)
                if parler:
                    for phrase in decoupeur.ajouter(evt["texte"]):
                        if premiere_phrase_ms is None:
                            premiere_phrase_ms = round((time.perf_counter() - debut) * 1000)
                        self.orateur.dire(phrase)
            elif genre in ("pensee", "outil", "contexte"):
                self.bus.publier(genre, **donnees)
            elif genre == "erreur":
                self.bus.publier("erreur", texte=evt["message"])
                if parler:
                    self.orateur.dire("Je rencontre un problème technique. Les détails sont à l'écran.")
            elif genre == "fin":
                reponse = evt["texte"]
                if parler:
                    for phrase in decoupeur.terminer():
                        if premiere_phrase_ms is None:
                            premiere_phrase_ms = round((time.perf_counter() - debut) * 1000)
                        self.orateur.dire(phrase)
                stats = dict(evt["stats"])
                if ms_transcription is not None:
                    stats["transcription_ms"] = ms_transcription
                if premiere_phrase_ms is not None:
                    stats["premiere_phrase_ms"] = premiere_phrase_ms
                self.dernieres_stats = stats
                self.bus.publier("reponse", texte=reponse, interrompu=evt["interrompu"], stats=stats, source=source)
        self.occupe = False
        if parler and not self._annuler.is_set():
            self.orateur.attendre()
            if (self.oreilles and source == "voix" and self.config.oreilles.conversation_continue
                    and not self._annuler.is_set()):
                self.oreilles.ecouter_suite()
                return reponse
        self._retour_au_repos()
        return reponse

    def _retour_au_repos(self) -> None:
        # Si « Hey Jarvis » a interrompu la réponse, les oreilles écoutent déjà : on ne les écrase pas.
        if self.etat not in ("ecoute", "transcription"):
            self.changer_etat("veille" if self.oreilles else "inactif")

    def _confirmer(self, demande: DemandeConfirmation) -> bool:
        if self.confirmateur_externe:
            return bool(self.confirmateur_externe(demande))
        ev, boite = threading.Event(), {"accord": False}
        self._confirmations[demande.ident] = (ev, boite)
        delai = self.config.securite.delai_confirmation_s
        self.bus.publier("confirmation", id=demande.ident, outil=demande.outil.nom, libelle=demande.outil.libelle,
                         icone=demande.outil.icone,
                         args={k: tronquer(_affichable(v), 600) for k, v in demande.arguments.items()},
                         raison=demande.raison, delai=delai)
        etat_avant = self.etat
        self.changer_etat("confirmation")
        if self.orateur and self.oreilles and self.voix_active:
            self._confirmation_vocale = demande.ident
            self._relances_vocales = 0
            self.orateur.dire(f"J'ai besoin de votre accord pour : {_resume_action(demande)}. Je confirme ?")
            self.orateur.attendre(30)
            self.changer_etat("confirmation")
            if not ev.is_set():
                self.oreilles.ecouter_suite(8.0)
        accord = ev.wait(delai) and boite["accord"]
        self._confirmations.pop(demande.ident, None)
        self._confirmation_vocale = None
        self.bus.publier("confirmation_fin", id=demande.ident, accord=accord, outil=demande.outil.nom)
        self.changer_etat(etat_avant if etat_avant != "confirmation" else "reflexion")
        return accord

    # ================================================================== rappels et télémétrie
    def _rappel_echu(self, rappel: Rappel, en_retard: bool) -> None:
        self.bus.publier("rappel", id=rappel.id, texte=rappel.texte, genre=rappel.type, en_retard=en_retard)
        try:
            from jarvis.tools.systeme import envoyer_notification
            titre = "Minuteur terminé" if rappel.type == "minuteur" else "Rappel"
            envoyer_notification(f"Jarvis · {titre}", rappel.texte)
        except Exception:
            pass
        if self.orateur and self.voix_active:
            appel = self.config.general.appellation
            debut = f"{appel}, " if appel else ""
            phrase = (f"{debut}votre minuteur « {rappel.texte} » est terminé." if rappel.type == "minuteur"
                      else f"{debut}petit rappel : {rappel.texte}.")
            if en_retard:
                phrase += " Ce rappel était prévu pendant mon absence."
            self.orateur.bip()
            self.orateur.dire(phrase)

    def _boucle_telemetrie(self) -> None:
        import psutil
        psutil.cpu_percent(interval=None)
        tour = 0
        modeles: list = []
        while self._actif:
            time.sleep(2.0)
            tour += 1
            try:
                if tour % 3 == 1:
                    try:
                        modeles = [{"nom": m.get("name"), "go": round((m.get("size_vram") or m.get("size") or 0) / 1e9, 2)}
                                   for m in self.llm.charges()]
                    except OllamaErreur:
                        modeles = []
                ram = psutil.virtual_memory()
                batterie = psutil.sensors_battery()
                self.bus.publier(
                    "telemetrie",
                    cpu=round(psutil.cpu_percent(interval=None)),
                    ram_utilisee=round(ram.used / 1e9, 1), ram_totale=round(ram.total / 1e9, 1),
                    batterie=None if batterie is None else round(batterie.percent),
                    en_charge=None if batterie is None else bool(batterie.power_plugged),
                    modeles=modeles, requetes_internet=self.ctx.requetes_internet,
                    souvenirs=self.memoire.compter() if tour % 15 == 1 else None,
                )
            except Exception:
                log.debug("Télémétrie indisponible", exc_info=True)


def _duree(secondes: float) -> str:
    """4,2 s · 2 min 05 s"""
    if secondes < 60:
        return f"{secondes:.1f} s".replace(".", ",")
    minutes, reste = divmod(round(secondes), 60)
    return f"{minutes} min {reste:02d} s"


def _resume_action(demande: DemandeConfirmation) -> str:
    if demande.outil.categorie == "terminal":
        return "exécuter la commande affichée à l'écran"
    if demande.outil.nom == "oublier":
        return f"effacer de ma mémoire ce qui concerne « {demande.arguments.get('recherche', '')} »"
    valeurs = [str(v) for v in demande.arguments.values()]
    apercu = tronquer(", ".join(valeurs), 80)
    return f"{demande.outil.libelle} ({apercu})" if apercu else demande.outil.libelle
