#!/usr/bin/env bash
# Installation de Jarvis.
# Pensé pour un Mac Apple Silicon (M1 à M5, 16 Go) ; fonctionne aussi sous Linux pour tester.
# Le script annonce chaque étape et demande avant d'installer ou de modifier quoi que ce soit.
# Lancement, depuis le dossier du projet : bash install.sh
set -euo pipefail

if [ -t 1 ]; then
  C='\033[38;2;116;220;255m'; O='\033[38;2;230;191;110m'; R='\033[38;2;255;93;80m'
  V='\033[38;2;127;224;166m'; G='\033[38;2;129;152;174m'; Z='\033[0m'
else
  C=''; O=''; R=''; V=''; G=''; Z=''
fi

etape()        { printf "\n${C}▸ %s${Z}\n" "$1"; }
ok()           { printf "  ${V}✓${Z} %s\n" "$1"; }
info()         { printf "  ${G}%s${Z}\n" "$1"; }
attention()    { printf "  ${O}!${Z} %s\n" "$1"; }
echec()        { printf "  ${R}✗ %s${Z}\n" "$1"; exit 1; }
demander()     { local r=""; read -r -p "  $1 [o/N] " r || true; [[ "$r" =~ ^[oOyY] ]]; }
demander_oui() { local r=""; read -r -p "  $1 [O/n] " r || true; [[ ! "$r" =~ ^[nN] ]]; }
version_ge()   { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }
court()        { case "$1" in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }

# ======================================================================= briques (testées par tests/test_installation.py)

# Carte graphique utilisable par Ollama : « Apple Silicon », « NVIDIA », « AMD » ou « aucune ».
detecter_acceleration() {
  if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then echo "Apple Silicon"; return; fi
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then echo "NVIDIA"; return; fi
  if command -v rocminfo >/dev/null 2>&1 && rocminfo 2>/dev/null | grep 'gfx' >/dev/null; then echo "AMD"; return; fi
  echo "aucune"
}

# Paquets Linux nécessaires au micro (PortAudio) et à la voix de secours (espeak-ng).
paquets_audio_manquants() {
  local manquants=""
  if ! { ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null || true; } | grep 'libportaudio\.so' >/dev/null; then
    manquants="libportaudio2"
  fi
  if ! command -v espeak-ng >/dev/null 2>&1; then
    manquants="${manquants:+$manquants }espeak-ng"
  fi
  printf '%s' "$manquants"
}

# Remplace le modèle par défaut par le modèle léger, sans jamais écraser un choix personnel.
appliquer_modele_leger() {
  local fichier="$1"
  [ -f "$fichier" ] || return 1
  grep -E '^modele = "qwen3\.5:9b"' "$fichier" >/dev/null || return 1
  sed -i.bak -E 's|^modele = "qwen3\.5:9b".*$|modele = "qwen3.5:4b"                       # 3,3 Go · modèle léger, pour une machine sans GPU|' "$fichier"
  rm -f "$fichier.bak"
}

# Fichier de démarrage du shell de l'utilisateur (là où l'on peut compléter le PATH).
fichier_rc() {
  case "${SHELL:-}" in
    */zsh)  printf '%s\n' "$HOME/.zshrc" ;;
    */bash) if [ "$(uname -s)" = "Darwin" ]; then printf '%s\n' "$HOME/.bash_profile"; else printf '%s\n' "$HOME/.bashrc"; fi ;;
    *)      printf '%s\n' "$HOME/.profile" ;;
  esac
}

# Vrai si ~/.local/bin figure dans le PATH donné.
dans_path() {
  case ":$1:" in
    *":$HOME/.local/bin:"* | *":$HOME/.local/bin/:"*) return 0 ;;
    *) return 1 ;;
  esac
}

# Ajoute ~/.local/bin au PATH, à la fin du fichier de démarrage du shell.
ajouter_au_path() {
  cat >> "$1" <<'FIN'

# Ajouté par l'installateur de Jarvis : rend la commande « jarvis » accessible
export PATH="$HOME/.local/bin:$PATH"
FIN
}

# Met une valeur entre apostrophes pour un script sh (une apostrophe devient '\'').
entre_apostrophes() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# Crée la commande « jarvis » : un petit script qui lance le projet avec uv, d'où que tu sois.
creer_lanceur() {
  local destination="$1" uv_bin="$2" projet="$3"
  mkdir -p "$(dirname "$destination")"
  {
    printf '#!/bin/sh\n'
    printf '%s\n' "# Commande créée par l'installateur de Jarvis. Projet : $projet"
    printf 'exec %s run --project %s jarvis "$@"\n' "$(entre_apostrophes "$uv_bin")" "$(entre_apostrophes "$projet")"
  } > "$destination"
  chmod +x "$destination"
}

# ======================================================================= installation

main() {
  cd "$(dirname "$0")"
  local PROJET; PROJET="$(pwd)"
  local PATH_ORIGINE="$PATH"
  local CONFIG="${JARVIS_HOME:-$HOME/.jarvis}/config.toml"
  local MODELE_LEGER=0 VOIX_PHONON=0 REDEMARRER=0 LANCER="jarvis"
  local OS ARCH RAM_GO RAM_AFFICHEE LIBRE_GO ACCELERATION VERSION RC

  printf "\n${C}  J . A . R . V . I . S .${Z}\n${G}  Just A Rather Very Intelligent System · installation${Z}\n"

  # ------------------------------------------------------------------ machine
  etape "Vérification de la machine"
  OS="$(uname -s)"; ARCH="$(uname -m)"
  if [ "$OS" = "Darwin" ]; then
    if [ "$ARCH" = "arm64" ]; then ok "Mac Apple Silicon · macOS $(sw_vers -productVersion)"
    else attention "Mac Intel : MLX et la voix Phonon ne seront pas disponibles (mode réduit)."; fi
    RAM_GO=$(( $(sysctl -n hw.memsize) / 1073741824 ))
    RAM_AFFICHEE="$RAM_GO"
  elif [ "$OS" = "Linux" ]; then
    local DISTRIBUTION="Linux"
    if [ -r /etc/os-release ]; then DISTRIBUTION="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-Linux}")"; fi
    ok "$DISTRIBUTION · idéal pour tester avant le Mac"
    RAM_GO=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1048576 ))
    RAM_AFFICHEE="$(awk '/MemTotal/ {printf "%.1f", $2 / 1048576}' /proc/meminfo | tr '.' ',')"
  else
    echec "Système non pris en charge : $OS"
  fi
  ok "Mémoire vive utilisable : ${RAM_AFFICHEE} Go"
  LIBRE_GO=$(df -Pk "$HOME" | awk 'NR==2 {print int($4/1048576)}')
  if [ "$LIBRE_GO" -lt 12 ]; then attention "Seulement ${LIBRE_GO} Go libres : il en faut environ 10."; else ok "Espace libre : ${LIBRE_GO} Go"; fi

  ACCELERATION="$(detecter_acceleration)"
  if [ "$ACCELERATION" = "aucune" ]; then
    attention "Pas de puce Apple ni de carte graphique utilisable : le modèle tournera sur le processeur."
    info "Sur processeur, qwen3.5:9b n'écrit que quelques mots par seconde. Le modèle léger qwen3.5:4b"
    info "(3,3 Go) va environ deux fois plus vite : idéal pour tester avant le Mac."
  else
    ok "Accélération : $ACCELERATION"
  fi
  if [ "$ACCELERATION" = "aucune" ] || [ "$RAM_GO" -lt 15 ]; then
    if demander_oui "Utiliser le modèle léger qwen3.5:4b sur cette machine ?"; then MODELE_LEGER=1; fi
  fi

  if [ "$OS" = "Linux" ]; then
    local MANQUANTS; MANQUANTS="$(paquets_audio_manquants)"
    if [ -z "$MANQUANTS" ]; then
      ok "Audio : PortAudio et espeak-ng présents"
    else
      attention "Paquets audio manquants : $MANQUANTS (micro et voix de Jarvis)"
      if command -v apt-get >/dev/null 2>&1 && demander_oui "Les installer avec apt ? (ton mot de passe sera demandé)"; then
        # shellcheck disable=SC2086
        if sudo apt-get install -y $MANQUANTS; then ok "Paquets audio installés"
        else attention "Installation impossible. Plus tard : sudo apt update && sudo apt install $MANQUANTS"; fi
      else
        info "Tu pourras les installer plus tard : sudo apt install $MANQUANTS"
      fi
    fi
  fi

  # ------------------------------------------------------------------ uv
  etape "uv (gestionnaire Python)"
  if ! command -v uv >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1 && demander_oui "Installer uv avec Homebrew ?"; then
      brew install uv
    elif demander_oui "Installer uv avec le script officiel d'Astral (https://astral.sh/uv/install.sh) ?"; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$PATH"
    else
      echec "uv est nécessaire : https://docs.astral.sh/uv/"
    fi
  fi
  command -v uv >/dev/null 2>&1 || echec "uv reste introuvable."
  ok "uv $(uv --version | awk '{print $2}')"

  # ------------------------------------------------------------------ Ollama
  etape "Ollama (le moteur qui fait tourner les modèles)"
  if ! command -v ollama >/dev/null 2>&1; then
    if [ "$OS" = "Darwin" ]; then
      if command -v brew >/dev/null 2>&1 && demander_oui "Installer Ollama avec Homebrew (brew install ollama) ?"; then
        brew install ollama
      else
        info "Télécharge l'application sur https://ollama.com/download, installe-la et ouvre-la."
        read -r -p "  Appuie sur Entrée une fois Ollama installé et ouvert… " _ || true
      fi
    elif demander_oui "Installer Ollama avec le script officiel (https://ollama.com/install.sh, mot de passe demandé) ?"; then
      curl -fsSL https://ollama.com/install.sh | sh
    else
      echec "Ollama est nécessaire."
    fi
  fi
  command -v ollama >/dev/null 2>&1 || echec "Ollama reste introuvable."
  if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null; then
    info "Ollama ne répond pas encore : démarrage…"
    if [ "$OS" = "Darwin" ] && [ -d "/Applications/Ollama.app" ]; then open -a Ollama
    else (ollama serve >/dev/null 2>&1 &); fi
    for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
  fi
  VERSION="$(curl -sf http://127.0.0.1:11434/api/version | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')" \
    || echec "Ollama ne répond toujours pas sur 127.0.0.1:11434."
  ok "Ollama $VERSION"
  if [ "$ACCELERATION" = "Apple Silicon" ]; then
    if version_ge "$VERSION" "0.40.11"; then VOIX_PHONON=1
    else attention "La voix Phonon demande Ollama 0.40.11 ou plus : mets Ollama à jour (sinon, voix de macOS)."; fi
  fi

  # ------------------------------------------------------------------ Python
  etape "Environnement Python et dépendances"
  uv sync
  ok "Dépendances installées dans .venv"

  # ------------------------------------------------------------------ configuration
  etape "Configuration personnelle"
  uv run jarvis config --creer >/dev/null
  [ -f "$CONFIG" ] || echec "Impossible de créer $(court "$CONFIG")"
  ok "Configuration : $(court "$CONFIG") (tes réglages, commentés ligne par ligne)"
  if [ "$MODELE_LEGER" = "1" ]; then
    if appliquer_modele_leger "$CONFIG"; then ok "Modèle léger retenu : qwen3.5:4b"
    else info "Le modèle choisi dans ta configuration est déjà personnalisé : je n'y touche pas."; fi
  fi

  # ------------------------------------------------------------------ modèles
  etape "Modèles d'IA (le premier téléchargement prend quelques minutes)"
  local CERVEAU="" EMBEDDINGS="" MODELE_VOIX=""
  { IFS= read -r CERVEAU; IFS= read -r EMBEDDINGS; IFS= read -r MODELE_VOIX; } < <(
    uv run python -c "from jarvis.config import Config; c = Config.charger(); print(c.cerveau.modele); print(c.cerveau.modele_embeddings); print(c.voix.modele_tts if c.voix.moteur in ('auto', 'ollama') else '')"
  ) || true
  if [ -z "$CERVEAU" ] || [ -z "$EMBEDDINGS" ]; then
    echec "Configuration illisible : « uv run jarvis config » affiche le problème."
  fi
  local MODELES=("$CERVEAU" "$EMBEDDINGS")
  if [ "$VOIX_PHONON" = "1" ] && [ -n "$MODELE_VOIX" ]; then MODELES+=("$MODELE_VOIX"); fi
  for m in "${MODELES[@]}"; do
    printf "  ${G}↓ %s${Z}\n" "$m"
    ollama pull "$m"
  done
  ok "Modèles prêts : ${MODELES[*]}"

  etape "Oreilles (modèle de transcription, téléchargé une seule fois)"
  local NOM_STT=""
  if NOM_STT="$(uv run python -c "
import sys
from jarvis.config import Config
from jarvis.voice.stt import creer_transcripteur
try:
    t = creer_transcripteur(Config.charger())
    t.prechauffer()
except Exception as e:
    sys.exit(f'  {type(e).__name__} : {e}')
print(t.nom)")"; then
    ok "Transcription prête : $NOM_STT"
  else
    attention "Transcription non préchargée : elle se fera au premier lancement."
  fi

  # ------------------------------------------------------------------ commande « jarvis »
  etape "Commande « jarvis »"
  creer_lanceur "$HOME/.local/bin/jarvis" "$(command -v uv)" "$PROJET"
  ok "Commande installée : ~/.local/bin/jarvis"
  RC="$(fichier_rc)"
  if dans_path "$PATH_ORIGINE"; then
    ok "~/.local/bin est déjà dans ton PATH"
  elif [ -f "$RC" ] && grep '\.local/bin' "$RC" >/dev/null 2>&1; then
    ok "~/$(basename "$RC") ajoute déjà ~/.local/bin au PATH"
    REDEMARRER=1
  elif demander_oui "Ajouter ~/.local/bin au PATH (dans ~/$(basename "$RC")) pour que ton terminal trouve « jarvis » ?"; then
    ajouter_au_path "$RC"
    ok "PATH complété dans ~/$(basename "$RC")"
    REDEMARRER=1
  else
    LANCER="~/.local/bin/jarvis"
    info "Sans cela, lance Jarvis avec : $LANCER"
  fi

  # ------------------------------------------------------------------ fin
  etape "Diagnostic final"
  uv run jarvis doctor || true
  if [ "$REDEMARRER" = "1" ]; then
    printf "\n${O}  Installation terminée. Ouvre un nouveau terminal (ou tape : source ~/%s), puis lance « jarvis ».${Z}\n\n" "$(basename "$RC")"
  else
    printf "\n${O}  Installation terminée. Lance « %s », puis dis « Hey Jarvis ».${Z}\n\n" "$LANCER"
  fi
}

# Exécuté directement : on installe. Chargé par les tests (source) : on ne fait rien.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
