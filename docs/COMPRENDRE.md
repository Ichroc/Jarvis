# Comprendre Jarvis

Ce guide suit une phrase, du micro jusqu'au haut-parleur, et explique chaque brique : ce qu'elle fait, pourquoi elle est conçue ainsi, et où la trouver dans le code. Les durées indiquées sont des ordres de grandeur sur un Mac M5 de 16 Go.

```text
 0 ms      tu dis « Hey Jarvis »                       voice/wakeword.py
 +80 ms    le mot d'activation est reconnu, petit carillon
           tu poses ta question
 +700 ms   de silence : la phrase est terminée          voice/vad.py
 +≈ 800 ms Whisper a transcrit le texte                 voice/stt.py
 +≈ 400 ms le modèle choisit un outil ou écrit           brain.py
 +≈ 300 ms la première phrase est complète              textutils.py
 +≈ 150 ms premier son de la voix de Noémie              voice/tts.py
```

---

## 1. Le micro

Le micro est lu par blocs de **80 ms** (1 280 échantillons à 16 kHz). Le système audio appelle une fonction à chaque bloc, dans un fil temps réel : cette fonction ne fait que copier le bloc dans une file d'attente, puis rend la main. Si elle prenait du temps, l'audio se mettrait à grésiller. Tout le travail se fait dans un autre fil, qui vide la file à son rythme (`voice/audio.py`, `voice/oreilles.py`).

## 2. « Hey Jarvis »

Reconnaître un mot d'activation en permanence doit coûter presque rien. Jarvis réutilise les modèles pré-entraînés d'**openWakeWord**, avec une réimplémentation de 100 lignes (`voice/wakeword.py`) :

1. Chaque bloc de 80 ms devient un **mélspectrogramme** : une image du son en 32 bandes de fréquences, à l'échelle de l'oreille humaine.
2. Une fenêtre de 76 trames (environ 0,8 s) passe dans un réseau qui la résume en **96 nombres** (un embedding audio, entraîné par Google sur des milliers d'heures de parole).
3. Un petit classifieur regarde les **16 derniers embeddings** (environ 1,3 s) et donne une probabilité que tu viennes de dire « hey jarvis ».

Le tout coûte environ 1 ms par bloc. Au-delà du seuil (0,5 par défaut), Jarvis se réveille, puis réinitialise ses tampons pour ne pas se déclencher deux fois sur la même phrase. Les tests vérifient sur un vrai enregistrement que les scores sont **identiques** à ceux de la bibliothèque d'origine (0,997 sur « Hey Jarvis », 0,0005 sur « Alexa »).

## 3. La fin de ta phrase

Savoir quand tu as fini de parler est plus subtil qu'il n'y paraît. Jarvis utilise **Silero VAD** (2,3 Mo), qui donne pour chaque trame de 32 ms la probabilité qu'elle contienne de la voix. L'`Enregistreur` (`voice/vad.py`) ajoute trois idées :

- **Pré-enregistrement de 320 ms** : on garde en permanence le tout dernier tiers de seconde. Quand la voix est détectée, on a déjà le début du premier mot, qui serait sinon coupé.
- **Hystérésis** : il faut plus de certitude pour décider « ça parle » que pour décider « ça ne parle plus ». Une hésitation (« euh… ») ne coupe pas la phrase.
- **700 ms de silence** marquent la fin. Plus court, Jarvis te coupe la parole ; plus long, il paraît lent. C'est le réglage `silence_fin_ms`.

Un bruit de moins de 250 ms (porte, toux) est ignoré.

## 4. La transcription

**Whisper large-v3 turbo** (809 millions de paramètres) est la version accélérée de Whisper : son décodeur passe de 32 à 4 couches, pour une précision presque identique. Sur Mac, `mlx-whisper` l'exécute sur le GPU grâce à **MLX**, la bibliothèque d'Apple qui profite de la mémoire unifiée : le processeur et le GPU partagent la même RAM, aucune copie n'est nécessaire.

Trois détails de `voice/stt.py` font la différence :

- La langue est **forcée en français**. Sur une phrase de trois mots, la détection automatique se trompe parfois.
- Le **vocabulaire** de la configuration est donné à Whisper comme contexte : il écrit alors correctement « Wazuh » ou « Kubernetes ».
- Whisper a été entraîné sur des vidéos sous-titrées, alors dans le silence il lui arrive d'« entendre » « Sous-titres réalisés par la communauté d'Amara.org ». Ces hallucinations connues sont filtrées.

## 5. Le cerveau

### Ce qu'est le modèle

Qwen3.5 9B est un modèle de langage : il prédit le **jeton** suivant (un morceau de mot, environ 3 caractères en français), encore et encore. Ses 9 milliards de paramètres pèseraient 18 Go en 16 bits. La version installée est **quantifiée en 4 bits** (Q4_K_M) : chaque paramètre est arrondi sur 16 niveaux par petits groupes, ce qui ramène le modèle à 6,6 Go pour une perte de qualité faible.

### Pourquoi la vitesse dépend de la mémoire

Pour produire **un** jeton, le processeur graphique doit lire **tous** les poids du modèle. La vitesse est donc limitée par la bande passante mémoire : avec 153 Go/s sur une puce M5 et environ 6 Go de poids, le plafond théorique est de 25 jetons par seconde. C'est pour cette raison qu'un modèle plus petit est plus rapide, et que la quantification accélère autant qu'elle allège.

### Le contexte et le cache de préfixe

Chaque demande envoie au modèle : le prompt système (la personnalité), la description des 26 outils, l'historique récent et ta question. Le tout doit tenir dans le **contexte** (12 288 jetons) en laissant la place à la réponse ; `brain.py` calcule ce budget et oublie les échanges les plus anciens, tour par tour, pour ne jamais séparer un appel d'outil de son résultat.

Ollama garde en mémoire le calcul déjà fait sur le début de la conversation (**cache de préfixe**). Jarvis en tire parti : le prompt système et les outils ne changent jamais, et tout ce qui varie (l'heure, les souvenirs, le mode vocal) est placé dans ton message, à la fin. Résultat : seuls les nouveaux jetons sont lus, et le premier mot arrive bien plus vite. C'est pourquoi l'heure n'est pas dans le prompt système (`persona.py`).

Reste la toute première question : le cache est vide, et le modèle doit lire les ≈ 3 600 jetons de personnalité et d'outils. Sur un Mac, c'est quelques secondes ; sur un processeur sans carte graphique, plusieurs minutes. Jarvis fait donc ce travail **au lancement** (`Cerveau.prechauffer`) : il envoie une demande factice limitée à un seul jeton de réponse, avec exactement le même début et les mêmes réglages que les vraies demandes. Le détail compte : si la taille du contexte (`num_ctx`) différait, Ollama rechargerait le modèle et le calcul serait perdu. Un test vérifie que les deux débuts sont identiques, octet pour octet.

### Réfléchir ou répondre

Qwen3.5 sait « réfléchir » avant de répondre (mode thinking). C'est utile pour un problème difficile, mais en vocal cela ajoute plusieurs secondes de silence : Jarvis le désactive par défaut (`reflexion = false`).

## 6. La boucle agent et les outils

Un outil est une simple fonction Python :

```python
@outil(
    description="Lance un minuteur (compte à rebours) qui sonnera à la fin.",
    params={"minutes": "Durée en minutes.", "libelle": "Nom du minuteur."},
    risque="action", categorie="rappels", icone="⧗",
)
def minuteur(ctx: Contexte, minutes: float, libelle: str = "Minuteur") -> str:
    ...
```

Le décorateur lit la signature et fabrique le **schéma JSON** que le modèle reçoit (`minutes` est un nombre, obligatoire ; `libelle` est une chaîne facultative). La boucle (`brain.py`) fonctionne ainsi :

1. Le modèle reçoit la conversation et la liste des outils.
2. Il répond soit par du texte (fin), soit par un **appel d'outil** : `{"name": "minuteur", "arguments": {"minutes": 12, "libelle": "pâtes"}}`.
3. Jarvis valide et convertit les arguments (« 12 » devient 12), consulte le **pare-feu**, demande ton accord si nécessaire, exécute l'outil.
4. Le résultat repart vers le modèle dans un message de rôle `tool`, et on recommence à l'étape 2 (six étapes au maximum).

Pourquoi un outil `calculer` ? Parce qu'un modèle de langage ne calcule pas : il devine des chiffres plausibles. L'outil lit l'expression comme un arbre syntaxique (jamais avec `eval`) et n'autorise que les nombres, les opérateurs et quelques fonctions mathématiques.

## 7. La mémoire et tes documents

### Les embeddings

Un modèle d'**embeddings** (Qwen3-Embedding 0.6B) transforme un texte en un vecteur de 1 024 nombres, de façon que deux textes de sens proche donnent des vecteurs proches. On mesure cette proximité par la **similarité cosinus** : le cosinus de l'angle entre deux vecteurs, qui vaut 1 s'ils pointent dans la même direction. Comme les vecteurs sont normalisés, c'est un simple produit scalaire.

À chaque question, Jarvis calcule son vecteur, le compare à tous les souvenirs, et glisse dans le contexte ceux qui dépassent 0,45 sans être à plus de 0,15 du meilleur (`memory.py`). Qwen3-Embedding est plus précis quand la question est précédée d'une consigne (« retrouve des faits personnels qui aident à répondre ») ; les souvenirs, eux, sont stockés bruts.

### Le RAG (recherche augmentée)

`jarvis index` découpe tes documents en morceaux d'environ 900 caractères qui respectent les paragraphes, avec 150 caractères de **chevauchement** : une idée à cheval sur deux morceaux reste trouvable. À la question, les 5 morceaux les plus proches (2 au plus par fichier, pour varier les sources) sont donnés au modèle, qui répond en citant les fichiers (`knowledge.py`).

Pas besoin de base de données vectorielle : 10 000 morceaux de 1 024 nombres pèsent 40 Mo, et les comparer tous est une multiplication de matrices qui prend quelques millisecondes.

## 8. La voix

**Phonon** (Gradium, 40 millions de paramètres) génère l'audio par trames de 80 ms et l'envoie **en flux** : le son arrive avant que la phrase entière soit calculée. Il s'appuie sur Pocket TTS et le codec audio Mimi de Kyutai, deux travaux français. Ollama l'exécute avec MLX, jusqu'à plus de 100 fois plus vite que le temps réel sur les puces récentes.

Pour que Jarvis parle pendant qu'il réfléchit encore, le `DecoupeurPhrases` (`textutils.py`) reçoit le texte jeton par jeton et libère chaque phrase dès qu'un point est suivi d'un espace, sans se laisser piéger par « M. Dupont », « 3.5 » ou « etc. ». Chaque phrase est nettoyée (pas de Markdown, de lien ni d'emoji), puis jouée par tranches de 100 ms : c'est ce qui permet à **Échap** ou à un nouveau « Hey Jarvis » de couper la parole instantanément.

## 9. Le HUD

Chaque partie de Jarvis publie ce qu'elle fait sur un **bus d'événements** (`events.py`) : « état : écoute », « outil : météo », « jeton : Bonjour »… Le serveur du HUD (`hud/server.py`) s'abonne au bus et relaie chaque événement vers le navigateur par **WebSocket**. Comme le bus est appelé depuis plusieurs fils et le serveur tourne sur une boucle `asyncio`, le passage se fait par `call_soon_threadsafe`. Le réacteur est dessiné à 60 images par seconde dans un `canvas` ; son spectre réagit au niveau du micro quand tu parles, et à celui de la voix quand Jarvis répond.

---

## Exercices pour aller plus loin

**★ Ton premier outil.** Ajoute dans `jarvis/tools/essentiels.py` un outil qui donne une citation au hasard :

```python
import random

@outil(description="Donne une citation inspirante.", icone="❝", libelle="citation")
def citation() -> str:
    return random.choice(["« La simplicité est la sophistication suprême. » Léonard de Vinci",
                          "« Fais de ta vie un rêve, et d'un rêve, une réalité. » Saint-Exupéry"])
```

Relance Jarvis et demande « Une citation pour bien commencer la journée ? ». Puis écris le test correspondant dans `tests/test_outils.py`.

**★★ Une autre personnalité.** Modifie `persona.py` pour que Jarvis te tutoie et devienne coach sportif le matin. Observe comment une seule phrase du prompt change tout le comportement.

**★★ Mesurer.** Lance `jarvis bench` avec `qwen3.5:9b`, puis avec `qwen3.5:4b`. Compare le débit avec le calcul de la section 5 (bande passante divisée par la taille du modèle).

**★★★ Ton propre mot d'activation.** openWakeWord fournit un notebook d'entraînement à partir de voix synthétiques. Entraîne « OK Ichroc », exporte-le en ONNX, place-le dans `jarvis/assets/modeles/` et passe son chemin à `DetecteurMotActivation`.

**★★★ Jarvis et ton SOC.** Écris un outil `alertes_wazuh` qui interroge l'API de ton Wazuh (lecture seule) et résume les alertes de niveau 10 et plus des dernières 24 heures. Marque-le `prive=True` : le garde-fou anti-fuite s'appliquera automatiquement.

**★★★ Démarrer avec le Mac.** Crée un LaunchAgent (`~/Library/LaunchAgents/…plist`) qui lance `~/.local/bin/jarvis --sans-navigateur` à l'ouverture de session. Indice : launchd démarre avec un `PATH` minimal, pense à `EnvironmentVariables`, et vérifie la permission du micro.
