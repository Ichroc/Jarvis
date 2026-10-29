# Deux machines, un seul Jarvis

Jarvis tourne avec **le même code** sur un vieux ThinkPad sous Linux et sur un MacBook récent. Ce qui change, c'est ce que la machine sait faire : l'installateur (`bash install.sh`) détecte le matériel et choisit les bons modèles. Cette page explique ces choix, chiffres à l'appui.

| | **Profil « Banc d'essai »** | **Profil « Production »** |
|---|---|---|
| Machine | Lenovo ThinkPad T460 (2016) | Apple MacBook Air M5 (2026) |
| Système | Linux (Ubuntu) | macOS |
| Rôle | Tester Jarvis, développer, apprendre | Utiliser Jarvis au quotidien, à la voix |
| Cerveau | `qwen3.5:4b` (3,3 Go) | `qwen3.5:9b` (6,6 Go) |
| Voix | espeak-ng (robotique) | Phonon « Augustin » (naturelle) |
| Oreilles | Whisper small, sur le processeur | Whisper large-v3 turbo, sur le GPU (MLX) |

---

## Les fiches techniques

| Caractéristique | ThinkPad T460 | MacBook Air M5 |
|---|---|---|
| Processeur | Intel Core de 6ᵉ génération (Skylake, 14 nm), i3, i5 ou i7, 15 W | Apple M5 (3 nm) |
| Cœurs | **2 cœurs, 4 fils** (ex. i5-6200U : 2,3 GHz, 2,8 GHz en turbo) | **10 cœurs** : 4 « super cœurs » + 6 cœurs basse consommation |
| Carte graphique | Intel HD Graphics 520, **inutilisable par Ollama** | GPU 8 ou 10 cœurs, avec un **Neural Accelerator dans chaque cœur** |
| Moteur neuronal | aucun | Neural Engine 16 cœurs |
| Mémoire | DDR3L-1600, jusqu'à 32 Go | 16 Go unifiés (LPDDR5X-9600), 24 ou 32 Go en option |
| **Bande passante mémoire** | **≈ 12,8 Go/s par canal** | **153 Go/s** |
| Architecture mémoire | séparée : le processeur fait tout | **unifiée** : processeur, GPU et Neural Engine partagent la même mémoire, sans copie |

Sources : fiche PSREF Lenovo du ThinkPad T460, fiche technique Apple du MacBook Air 13 pouces M5.

> **Vérifie ta propre machine.** Les ThinkPad T460 existent en plusieurs versions.
> Sous Linux : `lscpu | grep "Model name"` (processeur), `free -h` (mémoire),
> `sudo dmidecode -t memory | grep -E "Size|Speed"` (barrettes et vitesse).
> Sur Mac : `system_profiler SPHardwareDataType`.

---

## Pourquoi ces différences comptent pour une IA locale

Un modèle de langage fait deux choses très différentes, limitées par deux choses très différentes.

### 1. Écrire la réponse : limité par la bande passante mémoire

Pour produire **un seul** jeton (un morceau de mot), la machine doit lire **tous** les poids du modèle en mémoire. La vitesse maximale se calcule donc simplement :

```text
vitesse maximale ≈ bande passante mémoire ÷ taille du modèle

ThinkPad, qwen3.5:9b   12,8 Go/s ÷ 6,6 Go  ≈  2 jetons/s    (inutilisable à la voix)
ThinkPad, qwen3.5:4b   12,8 Go/s ÷ 3,3 Go  ≈  4 jetons/s    (lent, mais testable)
Mac M5,   qwen3.5:9b    153 Go/s ÷ 6,6 Go  ≈ 23 jetons/s    (plus vite qu'on ne parle)
```

La bande passante du M5 est **environ 12 fois** celle du T460. C'est la raison principale pour laquelle le ThinkPad prend le modèle léger : diviser la taille du modèle par deux double sa vitesse d'écriture.

### 2. Lire la question : limité par la puissance de calcul

Avant d'écrire, le modèle doit lire tout ce qu'on lui envoie : sa personnalité, la description des 26 outils (≈ 3 600 jetons au total) et ta question. Cette lecture se fait en parallèle, et dépend de la puissance de calcul brute.

Le T460 n'a que 2 cœurs pour ce travail ; le M5 a un GPU dont chaque cœur embarque un accélérateur dédié aux calculs d'IA. Lire les 3 600 jetons de départ prend **quelques minutes** sur le ThinkPad et **quelques secondes** sur le Mac. C'est pour ça que Jarvis se **préchauffe** au lancement : il fait cette lecture une seule fois, et Ollama garde le résultat en mémoire pour toutes les questions suivantes.

### 3. Tenir en mémoire : 16 Go des deux côtés, mais pas utilisés pareil

Sur le Mac, la mémoire unifiée sert à la fois au processeur et au GPU : le modèle de 6,6 Go y est directement utilisable par le GPU. Sur le ThinkPad, tout passe par le processeur ; les 16 Go suffiraient pour le 9B, mais c'est la vitesse (section 1) qui l'exclut, pas la place.

---

## Ce que l'installateur choisit, et pourquoi

| Brique | ThinkPad T460 | MacBook Air M5 | Raison |
|---|---|---|---|
| Cerveau | `qwen3.5:4b` | `qwen3.5:9b` | bande passante mémoire (voir plus haut) |
| Mémoire et documents | `qwen3-embedding:0.6b` | `qwen3-embedding:0.6b` | petit modèle, rapide partout |
| Transcription | faster-whisper `small`, int8, processeur | mlx-whisper `large-v3-turbo`, GPU | MLX n'existe que sur puce Apple |
| Voix | espeak-ng | Phonon 40M « Augustin » via Ollama | Ollama ne sert les voix Phonon que sur Mac Apple Silicon |
| « Hey Jarvis » | openWakeWord (ONNX) | openWakeWord (ONNX) | ≈ 1 ms par bloc de son : tourne partout |
| Détection de fin de phrase | Silero VAD (ONNX) | Silero VAD (ONNX) | idem |
| Paquets système | `libportaudio2`, `espeak-ng` (proposés par l'installateur) | rien à ajouter | le micro passe par PortAudio sous Linux |

Le choix se fait dans `install.sh` (fonction `detecter_acceleration`) : sans puce Apple ni carte graphique NVIDIA ou AMD, il propose le modèle léger, puis l'écrit dans `~/.jarvis/config.toml`. Rien n'est figé : tout se change dans ce fichier.

---

## Ce qu'il faut attendre de chaque machine

Estimations, à remplacer par tes vraies mesures avec `jarvis bench`.

| | ThinkPad T460 | MacBook Air M5 |
|---|---|---|
| Préchauffage au lancement | quelques minutes | quelques secondes |
| Vitesse d'écriture | ≈ 2 à 4 jetons/s | ≈ 20 à 25 jetons/s |
| Transcription d'une phrase | 1 à 3 s | moins d'une seconde |
| Entre la fin de ta phrase et la réponse | 15 s à 1 min | ≈ 2 à 3 s |
| Mémoire vive utilisée en conversation | ≈ 6 Go | ≈ 9,8 Go |
| Disque utilisé par les modèles | ≈ 4,5 Go | ≈ 9 Go |

| Fonction | ThinkPad T460 | MacBook Air M5 |
|---|---|---|
| Conversation écrite (`jarvis chat`) | ✓ | ✓ |
| HUD et « Hey Jarvis » | ✓ | ✓ |
| Voix naturelle | voix robotique (espeak-ng) | ✓ Phonon |
| Outils (rappels, notes, météo, terminal…) | ✓ | ✓ |
| Recherche dans tes documents | ✓ (indexation plus lente) | ✓ |
| Regarder l'écran (vision) | ✓ mais très lent | ✓ |
| Pilotage des applications (musique, volume…) | via `playerctl`, `pactl` | via AppleScript |

---

## Passer d'une machine à l'autre

Le code est le même : sur la nouvelle machine, clone le dépôt et lance `bash install.sh`. Pour garder tes souvenirs, notes et rappels, copie aussi le dossier `~/.jarvis` ; pense alors à remettre `modele = "qwen3.5:9b"` dans `~/.jarvis/config.toml` sur le Mac (le fichier copié depuis le ThinkPad contient le modèle léger).

| Pour changer de profil | Commande |
|---|---|
| Plus léger (machine lente, sur batterie) | `ollama pull qwen3.5:4b`, puis `modele = "qwen3.5:4b"` |
| Encore plus léger, moins fiable | `ollama pull qwen3.5:2b`, puis `modele = "qwen3.5:2b"` |
| Équilibré (Mac 16 Go) | `ollama pull qwen3.5:9b`, puis `modele = "qwen3.5:9b"` |
| Plus fin (Mac, applications lourdes fermées) | `ollama pull gemma4:12b`, puis `modele = "gemma4:12b"` |
