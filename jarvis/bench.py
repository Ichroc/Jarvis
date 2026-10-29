"""« jarvis bench » : mesure les vraies performances de Jarvis sur ta machine.

Plutôt que de croire des estimations, on chronomètre chaque maillon :
  cerveau     temps de chargement, délai avant le premier mot, jetons par seconde
  outils      délai avant qu'un outil soit appelé
  mémoire     vitesse des embeddings
  voix        délai avant le premier son, vitesse par rapport au temps réel
  oreilles    on fait dire une phrase à Jarvis, on la retranscrit, on compare
"""

from __future__ import annotations

import difflib
import json
import time
from datetime import datetime

import numpy as np

from jarvis import tools as registre
from jarvis.config import IS_APPLE_SILICON, Config
from jarvis.llm import Ollama, OllamaErreur
from jarvis.textutils import normaliser
from jarvis.ui_terminal import banniere, dore, gris, ligne_etat, vert

PHRASE_TEST = "Bonjour Monsieur, la réunion du projet commence à quinze heures trente, salle quarante-deux."


def _chrono_chat(llm: Ollama, modele: str, messages: list[dict], **kwargs) -> dict:
    debut = time.perf_counter()
    premier = None
    stats = {}
    appels = []
    for m in llm.discuter(modele, messages, **kwargs):
        if (m.contenu or m.appels) and premier is None:
            premier = time.perf_counter() - debut
        appels.extend(m.appels)
        if m.fini:
            stats = m.stats or {}
    return {"premier_ms": round((premier or 0) * 1000), "total_ms": round((time.perf_counter() - debut) * 1000),
            "jetons_par_s": stats.get("jetons_par_s", 0), "lecture_par_s": stats.get("lecture_par_s", 0),
            "chargement_ms": round(stats.get("load_duration", 0) / 1e6), "appels": [a.nom for a in appels]}


def _reechantillonner(x: np.ndarray, de: int, vers: int) -> np.ndarray:
    duree = len(x) / de
    t_src = np.linspace(0, duree, num=len(x), endpoint=False)
    t_dst = np.linspace(0, duree, num=int(duree * vers), endpoint=False)
    return np.interp(t_dst, t_src, x).astype(np.float32)


def executer(config: Config) -> int:
    banniere("Banc d'essai · mesures réelles sur cette machine")
    llm = Ollama(config.cerveau.hote_ollama)
    modele = config.cerveau.modele
    r: dict = {"date": datetime.now().isoformat(timespec="seconds"), "modele": modele}
    try:
        llm.version()
    except OllamaErreur as e:
        print(ligne_etat("ko", "Ollama", str(e)))
        return 1
    caps = llm.capacites(modele)
    reflexion = False if "thinking" in caps else None
    options = {"num_ctx": config.cerveau.contexte, "temperature": 0.6}

    print("  " + gris("CERVEAU"))
    froid = _chrono_chat(llm, modele, [{"role": "user", "content": "Réponds juste : prêt."}],
                         reflexion=reflexion, options=options, garder="10m")
    print(ligne_etat("ok", "Chargement du modèle", f"{froid['chargement_ms']} ms (premier lancement)"))
    essais = [_chrono_chat(llm, modele, [{"role": "user", "content":
              "Explique en cinq phrases ce qu'est un SOC en cybersécurité."}],
              reflexion=reflexion, options=options) for _ in range(3)]
    r["cerveau"] = {
        "premier_mot_ms": int(np.median([e["premier_ms"] for e in essais])),
        "jetons_par_s": round(float(np.median([e["jetons_par_s"] for e in essais])), 1),
        "lecture_par_s": round(float(np.median([e["lecture_par_s"] for e in essais])), 1),
    }
    print(ligne_etat("ok", "Premier mot (médiane)", f"{r['cerveau']['premier_mot_ms']} ms"))
    print(ligne_etat("ok", "Vitesse d'écriture", f"{r['cerveau']['jetons_par_s']} jetons/s"))
    print(ligne_etat("ok", "Vitesse de lecture", f"{r['cerveau']['lecture_par_s']} jetons/s"))

    if "tools" in caps:
        registre.charger_tous()
        schemas = [o.schema() for o in registre.disponibles(config)]
        outil = _chrono_chat(llm, modele, [{"role": "user", "content": "Quelle heure est-il ?"}],
                             outils=schemas, reflexion=reflexion, options=options)
        r["outils"] = {"delai_ms": outil["premier_ms"], "appels": outil["appels"], "nb_outils": len(schemas)}
        ok = "heure_et_date" in outil["appels"]
        print(ligne_etat("ok" if ok else "attention", "Choix d'outil",
                         f"{outil['premier_ms']} ms · {', '.join(outil['appels']) or 'aucun outil appelé'}"))
    print()

    print("  " + gris("MÉMOIRE"))
    try:
        textes = [f"Paragraphe de test numéro {i} sur l'administration système et la sécurité réseau. " * 6
                  for i in range(32)]
        llm.vecteurs(config.cerveau.modele_embeddings, textes[:2])
        debut = time.perf_counter()
        v = llm.vecteurs(config.cerveau.modele_embeddings, textes)
        ms = (time.perf_counter() - debut) * 1000
        r["memoire"] = {"ms_par_texte": round(ms / len(textes), 1), "dimensions": len(v[0]) if v else 0}
        print(ligne_etat("ok", "Embeddings", f"{r['memoire']['ms_par_texte']} ms par paragraphe · "
                                             f"{r['memoire']['dimensions']} dimensions"))
    except OllamaErreur as e:
        print(ligne_etat("attention", "Embeddings", str(e)))
    print()

    print("  " + gris("VOIX ET OREILLES"))
    audio_test = None
    if IS_APPLE_SILICON:
        try:
            debut = time.perf_counter()
            premier, morceaux = None, []
            for bloc in llm.parler(config.voix.modele_tts, PHRASE_TEST, config.voix.voix):
                if premier is None:
                    premier = time.perf_counter() - debut
                morceaux.append(bloc)
            total = time.perf_counter() - debut
            brut = b"".join(morceaux)
            pcm = np.frombuffer(brut[: len(brut) // 2 * 2], dtype="<i2")
            duree_audio = len(pcm) / 24000
            r["voix"] = {"premier_son_ms": round(premier * 1000), "temps_reel_x": round(duree_audio / total, 1)}
            print(ligne_etat("ok", "Premier son", f"{r['voix']['premier_son_ms']} ms"))
            print(ligne_etat("ok", "Vitesse de synthèse", f"{r['voix']['temps_reel_x']}× plus rapide que le temps réel"))
            audio_test = _reechantillonner(pcm.astype(np.float32) / 32768.0, 24000, 16000)
        except OllamaErreur as e:
            print(ligne_etat("attention", "Voix Phonon", str(e)))
    if audio_test is not None:
        try:
            from jarvis.voice.stt import creer_transcripteur
            t = creer_transcripteur(config)
            t.prechauffer()
            debut = time.perf_counter()
            texte = t.transcrire(audio_test)
            ms = (time.perf_counter() - debut) * 1000
            fidelite = difflib.SequenceMatcher(None, normaliser(PHRASE_TEST), normaliser(texte)).ratio()
            r["oreilles"] = {"transcription_ms": round(ms), "fidelite": round(fidelite, 3), "texte": texte}
            print(ligne_etat("ok", "Transcription", f"{round(ms)} ms pour {len(audio_test) / 16000:.1f} s d'audio"))
            print(ligne_etat("ok" if fidelite > 0.85 else "attention", "Fidélité (voix → texte)",
                             f"{fidelite:.0%} · « {texte} »"))
        except Exception as e:
            print(ligne_etat("attention", "Transcription", str(e)))
    print()

    if "oreilles" in r and "voix" in r:
        estimation = (config.oreilles.silence_fin_ms + r["oreilles"]["transcription_ms"]
                      + r["cerveau"]["premier_mot_ms"] + 400 + r["voix"]["premier_son_ms"])
        r["latence_voix_estimee_ms"] = estimation
        print("  " + vert(f"Latence estimée entre la fin de ta phrase et la voix de Jarvis : ≈ {estimation / 1000:.1f} s"))
        print("  " + gris("(silence de fin de phrase + transcription + premier mot + première phrase + premier son)"))
    chemin = config.dossier / "banc_essai.json"
    config.preparer_dossiers()
    chemin.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  " + dore(f"Résultats enregistrés : {chemin}"))
    print()
    return 0
