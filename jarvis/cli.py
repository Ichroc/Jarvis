"""Point d'entrée « jarvis ».

  jarvis                 HUD + voix (l'expérience complète)
  jarvis chat            discussion écrite dans le terminal
  jarvis vocal           voix dans le terminal, sans HUD
  jarvis doctor          diagnostic complet de l'installation
  jarvis bench           mesures de performance réelles
  jarvis index [dossier] indexe tes documents pour la recherche
  jarvis memoire         ce que Jarvis a retenu (et l'oublier)
  jarvis voix            liste et essai des voix
  jarvis dire "texte"    fait parler Jarvis
  jarvis config          emplacement et création de la configuration
  jarvis journal         dernières actions du journal d'audit
"""

from __future__ import annotations

import argparse
import logging
import shutil
import socket
import sys
import time

from jarvis import __version__
from jarvis.config import PACKAGE_DIR, Config
from jarvis.ui_terminal import (AffichageTerminal, arc, banniere, blanc, confirmer_terminal, dore, gris,
                                ligne_etat, pale, rouge, vert)


def _config() -> Config:
    cfg = Config.charger()
    for avert in cfg.avertissements:
        print(ligne_etat("attention", "config.toml", avert))
    return cfg


def _afficher_composants(assistant) -> None:
    noms = {"cerveau": "Cerveau", "voix": "Voix", "oreilles": "Oreilles", "memoire": "Mémoire",
            "documents": "Documents", "pare_feu": "Pare-feu éthique"}
    for cle, libelle in noms.items():
        c = assistant.composants.get(cle)
        if not c:
            continue
        statut = "ok" if c.get("ok") else "info" if c.get("ok") is None else "ko"
        print(ligne_etat(statut, libelle, " · ".join(x for x in (c.get("nom"), c.get("detail")) if x)))
    print()


def _attendre_prechauffage(assistant) -> None:
    """Sur une machine sans carte graphique, le préchauffage peut durer quelques minutes :
    on montre qu'il avance plutôt que de laisser croire que Jarvis est bloqué."""
    if assistant.prechauffage_fini.wait(0.3):
        return
    print(gris("  Le cerveau lit sa personnalité et ses outils, une seule fois par session."))
    print(gris("  Sur un Mac : quelques secondes. Sans carte graphique : quelques minutes. Ctrl+C pour ne pas attendre."))
    debut = time.monotonic()
    try:
        while not assistant.prechauffage_fini.wait(0.5):
            ecoule = int(time.monotonic() - debut)
            print(f"\r  {arc('◌')} {gris('Préchauffage du cerveau…')} {ecoule // 60} min {ecoule % 60:02d} s ",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    print("\r" + " " * 60 + "\r", end="")
    c = assistant.composants.get("cerveau") or {}
    statut = "ok" if c.get("ok") else "info" if c.get("ok") is None else "ko"
    print(ligne_etat(statut, "Cerveau", " · ".join(x for x in (c.get("nom"), c.get("detail")) if x)) + "\n")


# --------------------------------------------------------------------- HUD
def cmd_hud(args) -> int:
    cfg = _config()
    port = args.port or cfg.interface.port
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(rouge(f"  Le port {port} est déjà utilisé : Jarvis tourne peut-être déjà. "
                        f"Sinon, essaie : jarvis --port {port + 1}"))
            return 1
    from jarvis.assistant import Assistant
    from jarvis.hud.server import lancer

    banniere("Démarrage des systèmes…")
    assistant = Assistant(cfg, avec_voix=not args.sans_voix, avec_oreilles=not args.sans_micro)
    assistant.demarrer()
    _afficher_composants(assistant)
    if assistant.avec_oreilles or not assistant.prechauffage_fini.is_set():
        print(gris("  Le cerveau et les oreilles se préparent en arrière-plan : le HUD affiche leur progression."))
    try:
        lancer(assistant, port=port, ouvrir=cfg.interface.ouvrir_navigateur and not args.sans_navigateur)
    except KeyboardInterrupt:
        pass
    finally:
        assistant.arreter()
        print(gris("\n  Jarvis s'est arrêté. À bientôt.\n"))
    return 0


# --------------------------------------------------------------------- chat
AIDE_CHAT = """  /oublier       efface la conversation en cours (la mémoire à long terme reste)
  /memoire       affiche ce que Jarvis a retenu
  /rappels       rappels et minuteurs en cours
  /confidentiel  coupe ou rétablit les outils internet
  /stats         performances de la dernière réponse
  /quitter       ferme Jarvis (ou Ctrl+D)"""


def cmd_chat(args) -> int:
    cfg = _config()
    if args.voix:
        cfg.voix.lire_reponses_ecrites = True
    from jarvis.assistant import Assistant

    banniere("Mode écrit · tape /aide pour les commandes")
    assistant = Assistant(cfg, avec_voix=args.voix, avec_oreilles=False, confirmateur=confirmer_terminal)
    AffichageTerminal(assistant.bus)
    assistant.demarrer()
    _afficher_composants(assistant)
    _attendre_prechauffage(assistant)
    while True:
        try:
            texte = input("  " + blanc("Vous › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not texte:
            continue
        if texte.startswith("/"):
            if not _commande_chat(texte, assistant):
                break
            continue
        try:
            assistant.traiter(texte, source="texte")
        except KeyboardInterrupt:
            assistant.interrompre()
            assistant.occupe = False
            print(gris("\n  (interrompu)\n"))
    assistant.arreter()
    print(gris("  À bientôt.\n"))
    return 0


def _commande_chat(texte: str, assistant) -> bool:
    commande = texte.split()[0].lower()
    if commande in ("/quitter", "/q", "/exit", "/quit"):
        return False
    if commande == "/aide":
        print(gris(AIDE_CHAT) + "\n")
    elif commande == "/oublier":
        assistant.oublier_conversation()
        print(gris("  Conversation effacée.\n"))
    elif commande == "/memoire":
        souvenirs = assistant.memoire.tout()
        for s in souvenirs:
            print(f"  {pale(f'n°{s.id}')} {s.texte}")
        print(gris(f"  {len(souvenirs)} souvenir(s).\n"))
    elif commande == "/rappels":
        rappels = assistant.planificateur.lister()
        for r in rappels:
            print(f"  {pale(f'n°{r.id}')} {r.echeance:%d/%m %H:%M} · {r.type} · {r.texte}")
        print(gris(f"  {len(rappels)} en cours.\n"))
    elif commande == "/confidentiel":
        assistant.regler(confidentiel=not assistant.confidentiel)
        print(gris(f"  Mode confidentiel {'activé : aucun outil internet' if assistant.confidentiel else 'désactivé'}.\n"))
    elif commande == "/stats":
        print(gris("  " + (", ".join(f"{k} : {v}" for k, v in assistant.dernieres_stats.items()) or "aucune réponse")) + "\n")
    else:
        print(gris("  Commande inconnue. Tape /aide.\n"))
    return True


# --------------------------------------------------------------------- vocal
def cmd_vocal(args) -> int:
    cfg = _config()
    from jarvis.assistant import Assistant

    banniere("Mode vocal · dites « Hey Jarvis » · Ctrl+C pour quitter")
    assistant = Assistant(cfg, avec_voix=True, avec_oreilles=True)
    AffichageTerminal(assistant.bus, vocal=True)
    assistant.demarrer()
    _afficher_composants(assistant)
    _attendre_prechauffage(assistant)
    print(gris("  Vous pouvez aussi taper une question puis Entrée.\n"))
    try:
        while True:
            texte = input().strip()
            if not texte:
                continue
            if assistant._confirmations and texte.lower() in ("o", "oui", "n", "non"):
                ident = list(assistant._confirmations)[-1]
                assistant.repondre_confirmation(ident, texte.lower() in ("o", "oui"))
                continue
            assistant.demander(texte)
    except (EOFError, KeyboardInterrupt):
        pass
    assistant.arreter()
    print(gris("\n  À bientôt.\n"))
    return 0


# --------------------------------------------------------------------- outils
def cmd_doctor(args) -> int:
    from jarvis.doctor import executer
    return executer(_config())


def cmd_bench(args) -> int:
    from jarvis.bench import executer
    return executer(_config())


def cmd_index(args) -> int:
    cfg = _config()
    cfg.preparer_dossiers()
    from jarvis.knowledge import BaseDocuments
    from jarvis.llm import Ollama
    from jarvis.memory import Vectoriseur

    vectoriseur = Vectoriseur(Ollama(cfg.cerveau.hote_ollama), cfg.cerveau.modele_embeddings)
    banniere("Indexation des documents")
    if vectoriseur(["test"]) is None:
        print(ligne_etat("attention", "Embeddings indisponibles",
                         f"recherche par mots-clés seulement (ollama pull {cfg.cerveau.modele_embeddings})"))
    dossiers = args.dossiers or cfg.documents.dossiers
    print(gris(f"  Dossiers : {', '.join(dossiers)}\n"))
    largeur = shutil.get_terminal_size((80, 20)).columns

    def rapport(chemin: str, i: int, n: int) -> None:
        ligne = f"  [{i}/{n}] {chemin}"
        print("\r" + ligne[: largeur - 1].ljust(largeur - 1), end="", flush=True)

    debut = time.perf_counter()
    stats = BaseDocuments(cfg.base, vectoriseur).indexer(dossiers, cfg.documents.extensions, rapport)
    print("\r" + " " * (largeur - 1) + "\r", end="")
    print(ligne_etat("ok", "Indexation terminée", f"{time.perf_counter() - debut:.1f} s"))
    print(ligne_etat("info", "Fichiers vus", str(stats["vus"])))
    print(ligne_etat("info", "Nouveaux / mis à jour", f"{stats['nouveaux']} / {stats['mis_a_jour']}"))
    print(ligne_etat("info", "Inchangés / retirés", f"{stats['inchanges']} / {stats['retires']}"))
    print(ligne_etat("info", "Morceaux créés", str(stats["morceaux"])))
    if stats["erreurs"]:
        print(ligne_etat("attention", "Fichiers illisibles", str(stats["erreurs"])))
    print()
    return 0


def cmd_memoire(args) -> int:
    cfg = _config()
    cfg.preparer_dossiers()
    from jarvis.llm import Ollama
    from jarvis.memory import Memoire, Vectoriseur

    memoire = Memoire(cfg.base, Vectoriseur(Ollama(cfg.cerveau.hote_ollama), cfg.cerveau.modele_embeddings))
    if args.ajouter:
        s, nouveau = memoire.ajouter(args.ajouter)
        print(ligne_etat("ok", "Mémorisé" if nouveau else "Mis à jour", f"n°{s.id} · {s.texte}"))
    if args.oublier is not None:
        s = memoire.oublier(args.oublier)
        print(ligne_etat("ok" if s else "attention", "Oublié" if s else "Introuvable",
                         s.texte if s else f"n°{args.oublier}"))
    print()
    souvenirs = memoire.tout()
    for s in souvenirs:
        print(f"  {pale(f'n°{s.id:<4}')} {gris(s.cree_le[:10])}  {s.texte}")
    print(gris(f"\n  {len(souvenirs)} souvenir(s). Oublier : jarvis memoire --oublier N\n"))
    return 0


def cmd_voix(args) -> int:
    """Liste les voix disponibles sur cette machine et en fait essayer une."""
    cfg = _config()
    from jarvis.llm import Ollama, OllamaErreur
    from jarvis.voice.tts import VOIX_PIPER_FR, Orateur, VoixOllama, VoixPiper, moteurs_possibles

    llm = Ollama(cfg.cerveau.hote_ollama)
    moteurs = moteurs_possibles(cfg, llm)
    print(gris(f"\n  Moteurs de voix disponibles ici : {', '.join(moteurs) or 'aucun'}\n"))
    if "ollama" in moteurs:
        try:
            print("  " + blanc(f"Phonon ({cfg.voix.modele_tts})") + gris(f" · actuelle : {cfg.voix.voix}"))
            for n in llm.voix(cfg.voix.modele_tts):
                print("    " + (arc("● ") if n == cfg.voix.voix else "  ") + n)
            print()
        except OllamaErreur as e:
            print(rouge(f"  {e}"))
    if "piper" in moteurs:
        print("  " + blanc("Piper") + gris(f" · actuelle : {cfg.voix.voix_piper}"))
        for n, description in VOIX_PIPER_FR.items():
            print("    " + (arc("● ") if n == cfg.voix.voix_piper else "  ") + n.ljust(22) + gris(description))
        print()
    if args.essayer:
        nom = args.essayer
        try:
            if nom.startswith("fr_") or "ollama" not in moteurs:
                voix = VoixPiper(nom, cfg.dossier / "voix", cfg.voix.debit)
                reglage = f'[voix] voix_piper = "{nom}"'
            else:
                voix = VoixOllama(llm, cfg.voix.modele_tts, nom)
                reglage = f'[voix] voix = "{nom}"'
        except Exception as e:
            print(rouge(f"  Voix « {nom} » impossible à charger : {e}"))
            return 1
        print(gris(f"  Écoute : {voix.nom}…"))
        orateur = Orateur(voix)
        orateur.dire("Bonjour, je suis Jarvis. Voici ma voix. Il fait quatorze degrés à Angers, "
                     "et votre prochain rappel est à dix-huit heures.")
        time.sleep(0.2)
        orateur.attendre()
        print(gris(f"  Pour la garder, mets dans ~/.jarvis/config.toml : {reglage}\n"))
    else:
        exemple = "fr_FR-tom-medium" if "piper" in moteurs else "Coralie"
        print(gris(f"  Essayer une voix : jarvis voix --essayer {exemple}\n"))
    return 0


def cmd_dire(args) -> int:
    cfg = _config()
    from jarvis.llm import Ollama
    from jarvis.voice.tts import Orateur, creer_voix

    voix = creer_voix(cfg, Ollama(cfg.cerveau.hote_ollama))
    print(gris(f"  Voix : {voix.nom}"))
    orateur = Orateur(voix)
    orateur.dire(" ".join(args.texte))
    time.sleep(0.2)
    orateur.attendre()
    return 0


def cmd_config(args) -> int:
    cfg = Config.charger()
    exemple = PACKAGE_DIR / "assets" / "config.example.toml"
    if args.creer:
        cfg.preparer_dossiers()
        if cfg.chemin.exists():
            print(ligne_etat("attention", "Déjà présente", str(cfg.chemin)))
        else:
            shutil.copy(exemple, cfg.chemin)
            print(ligne_etat("ok", "Configuration créée", str(cfg.chemin)))
    print(ligne_etat("info", "Fichier", f"{cfg.chemin} ({'présent' if cfg.chemin.exists() else 'absent'})"))
    print(ligne_etat("info", "Données", str(cfg.dossier)))
    print(ligne_etat("info", "Modèle", cfg.cerveau.modele))
    print(ligne_etat("info", "Voix", f"{cfg.voix.moteur} · {cfg.voix.voix}"))
    for avert in cfg.avertissements:
        print(ligne_etat("attention", "Avertissement", avert))
    return 0


def cmd_journal(args) -> int:
    cfg = Config.charger()
    from jarvis.security import Journal

    entrees = Journal(cfg.journal_audit).derniers(args.nombre)
    if not entrees:
        print(gris("  Le journal d'audit est vide."))
    for e in entrees:
        decision = e.get("decision", "")
        couleur = rouge if decision in ("refuse", "bloque") else dore if decision == "accorde" else vert
        args_txt = ", ".join(f"{k}={v}" for k, v in (e.get("arguments") or {}).items())
        print(f"  {gris(e.get('horodatage', ''))} {couleur(decision.ljust(8))} {e.get('outil', '')} {pale(args_txt[:80])}")
    print(gris(f"\n  Fichier complet : {cfg.journal_audit}\n"))
    return 0


# --------------------------------------------------------------------- analyse des arguments
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. · assistant personnel 100 % local")
    p.add_argument("--version", action="version", version=f"Jarvis {__version__}")
    p.add_argument("-v", "--verbeux", action="count", default=0, help="affiche les journaux techniques (-vv : tout)")
    p.add_argument("--port", type=int, help="port du HUD (7777 par défaut)")
    p.add_argument("--sans-voix", action="store_true", help="Jarvis répond seulement par écrit")
    p.add_argument("--sans-micro", action="store_true", help="pas d'écoute vocale")
    p.add_argument("--sans-navigateur", action="store_true", help="n'ouvre pas le navigateur automatiquement")
    sous = p.add_subparsers(dest="commande")

    sous.add_parser("hud", help="HUD + voix (par défaut)")
    c = sous.add_parser("chat", help="discussion écrite dans le terminal")
    c.add_argument("--voix", action="store_true", help="lit aussi les réponses à voix haute")
    sous.add_parser("vocal", help="voix dans le terminal, sans HUD")
    sous.add_parser("doctor", aliases=["diagnostic"], help="vérifie l'installation")
    sous.add_parser("bench", help="mesure les performances")
    i = sous.add_parser("index", help="indexe des documents")
    i.add_argument("dossiers", nargs="*", help="dossiers (par défaut : ceux de la configuration)")
    m = sous.add_parser("memoire", help="souvenirs de Jarvis")
    m.add_argument("--ajouter", metavar="TEXTE")
    m.add_argument("--oublier", type=int, metavar="N")
    v = sous.add_parser("voix", help="liste et essai des voix")
    v.add_argument("--essayer", metavar="NOM")
    d = sous.add_parser("dire", help="fait parler Jarvis")
    d.add_argument("texte", nargs="+")
    cf = sous.add_parser("config", help="configuration")
    cf.add_argument("--creer", action="store_true", help="crée ~/.jarvis/config.toml à partir de l'exemple")
    j = sous.add_parser("journal", help="journal d'audit")
    j.add_argument("-n", "--nombre", type=int, default=25)

    args = p.parse_args(argv)
    niveau = logging.WARNING if args.verbeux == 0 else logging.INFO if args.verbeux == 1 else logging.DEBUG
    logging.basicConfig(level=niveau, format="  [%(name)s] %(message)s")
    actions = {
        None: cmd_hud, "hud": cmd_hud, "chat": cmd_chat, "vocal": cmd_vocal, "doctor": cmd_doctor,
        "diagnostic": cmd_doctor, "bench": cmd_bench, "index": cmd_index, "memoire": cmd_memoire,
        "voix": cmd_voix, "dire": cmd_dire, "config": cmd_config, "journal": cmd_journal,
    }
    try:
        return actions[args.commande](args)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
