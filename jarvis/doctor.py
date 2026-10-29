"""« jarvis doctor » : vérifie tout ce dont Jarvis a besoin et dit comment réparer."""

from __future__ import annotations

import importlib
import platform
import shutil
import sys
from dataclasses import dataclass

from jarvis.config import IS_APPLE_SILICON, IS_LINUX, IS_MAC, Config
from jarvis.llm import Ollama, OllamaErreur
from jarvis.ui_terminal import banniere, dore, gris, ligne_etat, rouge, vert

VERSION_OLLAMA_VOIX = (0, 40, 11)
VERSION_OLLAMA_QWEN35 = (0, 17, 5)


@dataclass
class Verif:
    statut: str  # ok | attention | ko
    titre: str
    detail: str = ""
    conseil: str = ""


def version_tuple(v: str) -> tuple[int, ...]:
    morceaux = []
    for p in v.split("-")[0].split("."):
        chiffres = "".join(c for c in p if c.isdigit())
        morceaux.append(int(chiffres) if chiffres else 0)
    return tuple(morceaux)


def verifier(config: Config) -> list[tuple[str, list[Verif]]]:
    sections: list[tuple[str, list[Verif]]] = []

    # ------------------------------------------------------------- machine
    v: list[Verif] = []
    if IS_MAC:
        mac = platform.mac_ver()[0] or "?"
        if IS_APPLE_SILICON:
            v.append(Verif("ok", "Mac Apple Silicon", f"macOS {mac} · {platform.machine()}"))
        else:
            v.append(Verif("attention", "Mac Intel", f"macOS {mac}",
                           "MLX et la voix Phonon demandent une puce Apple (M1 à M5) : Jarvis fonctionnera en mode réduit."))
    elif IS_LINUX:
        v.append(Verif("attention", "Linux", platform.platform(),
                       "Parfait pour tester. Sur Linux, la voix passe par ptts ou espeak-ng et la transcription par le processeur."))
    else:
        v.append(Verif("attention", "Système non testé", platform.platform()))
    if not IS_APPLE_SILICON:
        leger = any(t in config.cerveau.modele for t in (":0.8b", ":2b", ":4b", ":e2b", ":e4b"))
        if shutil.which("nvidia-smi") or shutil.which("rocminfo"):
            v.append(Verif("ok", "Accélération", "carte graphique détectée"))
        else:
            v.append(Verif("ok" if leger else "attention", "Accélération",
                           "aucune : calcul sur le processeur" + (" · modèle léger adapté" if leger else ""),
                           "" if leger else "Sur processeur, le modèle 9B est très lent : ollama pull qwen3.5:4b, "
                           "puis modele = \"qwen3.5:4b\" dans ~/.jarvis/config.toml."))
    try:
        import psutil
        ram = psutil.virtual_memory().total / 1e9
        v.append(Verif("ok" if ram >= 15 else "attention", "Mémoire vive", f"{ram:.1f} Go".replace(".", ","),
                       "" if ram >= 15 else "Avec moins de 16 Go, préfère qwen3.5:4b (modèle « éco »)."))
        libre = psutil.disk_usage(str(config.dossier.anchor or "/")).free / 1e9
        v.append(Verif("ok" if libre >= 15 else "attention", "Espace disque libre", f"{libre:.0f} Go",
                       "" if libre >= 15 else "Les modèles occupent environ 10 Go : libère de la place."))
    except ImportError:
        v.append(Verif("ko", "psutil", "absent", "Lance : uv sync"))
    v.append(Verif("ok" if sys.version_info >= (3, 11) else "ko", "Python", platform.python_version(),
                   "" if sys.version_info >= (3, 11) else "Python 3.11 ou plus est nécessaire (uv s'en charge)."))
    sections.append(("Machine", v))

    # ------------------------------------------------------------- modules
    v = []
    for module, role in [("numpy", "calcul"), ("requests", "dialogue avec Ollama"), ("onnxruntime", "écoute"),
                         ("starlette", "HUD"), ("uvicorn", "HUD"), ("pypdf", "lecture des PDF"),
                         ("docx", "lecture des .docx")]:
        v.append(_module(module, role))
    ws = any(_importable(m) for m in ("websockets", "wsproto"))
    v.append(Verif("ok" if ws else "ko", "websockets", "WebSocket du HUD" if ws else "absent",
                   "" if ws else "Lance : uv sync (installe uvicorn[standard])"))
    try:
        import sounddevice  # noqa: F401
        v.append(Verif("ok", "sounddevice", "micro et haut-parleur"))
    except OSError:
        v.append(Verif("ko", "sounddevice", "PortAudio introuvable",
                       "Linux : sudo apt install libportaudio2 · Mac : uv sync --reinstall-package sounddevice"))
    except ImportError:
        v.append(Verif("ko", "sounddevice", "absent", "Lance : uv sync"))
    stt = "mlx_whisper" if IS_APPLE_SILICON else "faster_whisper"
    v.append(_module(stt, "transcription"))
    v.append(_module("ddgs", "recherche web", facultatif=True))
    sections.append(("Modules Python", v))

    # ------------------------------------------------------------- Ollama
    v = []
    llm = Ollama(config.cerveau.hote_ollama)
    try:
        version = llm.version()
        vt = version_tuple(version)
        if vt >= VERSION_OLLAMA_VOIX:
            v.append(Verif("ok", "Ollama", f"version {version}"))
        elif vt >= VERSION_OLLAMA_QWEN35:
            v.append(Verif("attention", "Ollama", f"version {version}",
                           "Mets Ollama à jour (≥ 0.40.11) pour la voix Phonon : brew upgrade ollama ou ollama.com/download"))
        else:
            v.append(Verif("ko", "Ollama", f"version {version}", "Version trop ancienne : mets Ollama à jour."))
        installes = llm.noms_modeles()
        attendus = [(config.cerveau.modele, "cerveau"), (config.cerveau.modele_embeddings, "mémoire")]
        if IS_APPLE_SILICON and config.voix.moteur in ("auto", "ollama"):
            attendus.append((config.voix.modele_tts, "voix"))
        for modele, role in attendus:
            if modele in installes:
                v.append(Verif("ok", f"Modèle {role}", modele))
            else:
                v.append(Verif("ko" if role == "cerveau" else "attention", f"Modèle {role}", f"{modele} absent",
                               f"Lance : ollama pull {modele}"))
        if config.cerveau.modele in installes:
            caps = llm.capacites(config.cerveau.modele)
            manque = [c for c in ("tools",) if c not in caps]
            v.append(Verif("ok" if not manque else "ko", "Capacités du cerveau", " · ".join(sorted(caps)),
                           "" if not manque else "Ce modèle ne sait pas utiliser d'outils : choisis-en un autre."))
    except OllamaErreur as e:
        v.append(Verif("ko", "Ollama", "ne répond pas",
                       f"{e} Installation : https://ollama.com/download"))
    sections.append(("Cerveau (Ollama)", v))

    # ------------------------------------------------------------- audio
    v = []
    try:
        from jarvis.voice.audio import peripheriques
        p = peripheriques()
        v.append(Verif("ok" if p["entree"] else "ko", "Micro", p["entree"] or "aucun",
                       "" if p["entree"] else "Branche un micro ou autorise le terminal dans Réglages Système > Micro."))
        v.append(Verif("ok" if p["sortie"] else "ko", "Haut-parleur", p["sortie"] or "aucun"))
        if p["entree"]:
            import sounddevice as sd
            essai = sd.rec(4800, samplerate=16000, channels=1, dtype="int16", blocking=True)
            if not essai.any():
                v.append(Verif("attention", "Signal du micro", "silence absolu",
                               "Permission probablement refusée : Réglages Système > Confidentialité et "
                               "sécurité > Micro, autorise ton terminal."))
            else:
                v.append(Verif("ok", "Signal du micro", "le micro capte bien le son"))
    except Exception as e:
        v.append(Verif("ko", "Audio", str(e)))
    try:
        from jarvis.voice.vad import SileroVAD
        from jarvis.voice.wakeword import DetecteurMotActivation
        SileroVAD()
        DetecteurMotActivation()
        v.append(Verif("ok", "Détection de voix et « Hey Jarvis »", "modèles ONNX embarqués"))
    except Exception as e:
        v.append(Verif("ko", "Modèles d'écoute", str(e)))
    if IS_MAC:
        v.append(Verif("ok" if shutil.which("say") else "attention", "Voix de secours macOS", "say"))
    elif not (shutil.which("espeak-ng") or _importable("ptts")):
        v.append(Verif("attention", "Voix sur Linux", "aucune", "sudo apt install espeak-ng (ou uv sync --extra ptts)"))
    sections.append(("Voix et écoute", v))

    # ------------------------------------------------------------- données
    v = []
    v.append(Verif("ok" if config.chemin and config.chemin.exists() else "attention", "Configuration",
                   str(config.chemin), "" if config.chemin and config.chemin.exists()
                   else "Crée-la avec : jarvis config --creer"))
    for avert in config.avertissements:
        v.append(Verif("attention", "config.toml", avert))
    try:
        config.preparer_dossiers()
        from jarvis.knowledge import BaseDocuments
        from jarvis.memory import Memoire
        from jarvis.scheduler import Planificateur
        m = Memoire(config.base).compter()
        d = BaseDocuments(config.base).stats()
        r = len(Planificateur(config.base).lister())
        v.append(Verif("ok", "Données personnelles", f"{m} souvenirs · {d['fichiers']} documents · {r} rappels actifs"))
    except Exception as e:
        v.append(Verif("ko", "Données personnelles", str(e)))
    sections.append(("Données", v))
    return sections


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def _module(module: str, role: str, facultatif: bool = False) -> Verif:
    if _importable(module):
        return Verif("ok", module, role)
    return Verif("attention" if facultatif else "ko", module, f"absent ({role})", "Lance : uv sync")


def executer(config: Config) -> int:
    banniere("Diagnostic des systèmes")
    sections = verifier(config)
    nb_ko = nb_att = 0
    conseils = []
    for titre, verifs in sections:
        print("  " + gris(titre.upper()))
        for v in verifs:
            print(ligne_etat(v.statut, v.titre, v.detail))
            if v.statut == "ko":
                nb_ko += 1
            elif v.statut == "attention":
                nb_att += 1
            if v.conseil and v.statut != "ok":
                conseils.append((v.statut, v.titre, v.conseil))
        print()
    if conseils:
        print("  " + gris("À FAIRE"))
        for statut, titre, conseil in conseils:
            puce = rouge("✗") if statut == "ko" else dore("!")
            print(f"  {puce} {titre} : {conseil}")
        print()
    if nb_ko == 0:
        print("  " + vert("Tous les systèmes essentiels sont opérationnels.")
              + (gris(f" ({nb_att} point(s) d'attention)") if nb_att else ""))
    else:
        print("  " + rouge(f"{nb_ko} problème(s) à corriger avant de lancer Jarvis."))
    print()
    if IS_MAC:
        print(gris("  Permissions macOS : au premier lancement, autorise ton terminal pour le Micro,\n"
                   "  et pour l'Enregistrement de l'écran si tu utilises « regarde mon écran ».\n"))
    return 0 if nb_ko == 0 else 1
