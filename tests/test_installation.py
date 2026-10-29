"""Tests des briques de install.sh. Le script est chargé avec « source » : rien n'est installé,
on appelle ses fonctions une à une, avec des commandes factices quand il faut simuler une machine."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.config import Config

RACINE = Path(__file__).resolve().parents[1]
SCRIPT = RACINE / "install.sh"
EXEMPLE = RACINE / "jarvis" / "assets" / "config.example.toml"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash absent")


def bash(code, home, *, path=None, shell="/bin/zsh", entree=""):
    env = {"HOME": str(home), "PATH": path or os.environ.get("PATH", "/usr/bin:/bin"),
           "SHELL": shell, "LANG": "C.UTF-8"}
    return subprocess.run([BASH, "-c", f'source "{SCRIPT}"; {code}'], env=env, input=entree,
                          capture_output=True, text=True, timeout=30)


def faux_bin(dossier: Path, **scripts) -> str:
    """Dossier de commandes factices (nom → corps du script sh), avec les vrais grep, sed et cat."""
    dossier.mkdir()
    for outil in ("grep", "sed", "cat"):
        (dossier / outil).symlink_to(shutil.which(outil))
    for nom, corps in scripts.items():
        f = dossier / nom.replace("_", "-")
        f.write_text("#!/bin/sh\n" + corps + "\n", encoding="utf-8")
        f.chmod(0o755)
    return str(dossier)


LINUX = 'case "$1" in -s) echo Linux ;; -m) echo x86_64 ;; esac'


def test_syntaxe():
    assert subprocess.run([BASH, "-n", str(SCRIPT)]).returncode == 0


def test_charger_le_script_n_installe_rien(tmp_path):
    r = bash("echo chargé", tmp_path)
    assert r.returncode == 0 and r.stdout == "chargé\n"


@pytest.mark.parametrize("fonction,entree,attendu", [
    ("demander_oui", "\n", "oui"), ("demander_oui", "o\n", "oui"), ("demander_oui", "n\n", "non"),
    ("demander", "\n", "non"), ("demander", "oui\n", "oui"), ("demander", "non\n", "non"),
])
def test_questions_et_reponses_par_defaut(tmp_path, fonction, entree, attendu):
    r = bash(f'if {fonction} "Question ?"; then echo oui; else echo non; fi', tmp_path, entree=entree)
    assert r.stdout.strip() == attendu


def test_detection_du_materiel(tmp_path):
    assert bash("detecter_acceleration", tmp_path, path=faux_bin(tmp_path / "a", uname=LINUX)).stdout == "aucune\n"
    avec_gpu = faux_bin(tmp_path / "b", uname=LINUX, nvidia_smi="exit 0")
    assert bash("detecter_acceleration", tmp_path, path=avec_gpu).stdout == "NVIDIA\n"
    mac = faux_bin(tmp_path / "c", uname='case "$1" in -s) echo Darwin ;; -m) echo arm64 ;; esac')
    assert bash("detecter_acceleration", tmp_path, path=mac).stdout == "Apple Silicon\n"


def test_paquets_audio(tmp_path):
    present = faux_bin(tmp_path / "a", ldconfig='echo "libportaudio.so.2 (libc6,x86-64) => /usr/lib/libportaudio.so.2"')
    assert bash("paquets_audio_manquants", tmp_path, path=present).stdout == ""
    absent = faux_bin(tmp_path / "b", ldconfig='echo "libc.so.6 (libc6,x86-64) => /lib/libc.so.6"')
    assert bash("paquets_audio_manquants", tmp_path, path=absent).stdout == "libportaudio2"


def test_modele_leger(tmp_path):
    cfg = tmp_path / "config.toml"
    shutil.copy(EXEMPLE, cfg)
    r = bash(f'appliquer_modele_leger "{cfg}"', tmp_path)
    assert r.returncode == 0, r.stderr
    c = Config.charger(cfg)
    assert c.avertissements == [] and c.cerveau.modele == "qwen3.5:4b"
    assert c.cerveau.modele_embeddings == "qwen3-embedding:0.6b"
    assert not (tmp_path / "config.toml.bak").exists()
    # Une seconde fois, ou sur un choix personnel : rien n'est écrasé.
    assert bash(f'appliquer_modele_leger "{cfg}"', tmp_path).returncode == 1
    assert bash(f'appliquer_modele_leger "{tmp_path}/absent.toml"', tmp_path).returncode == 1


@pytest.mark.parametrize("shell,attendu", [("/bin/zsh", ".zshrc"), ("/usr/bin/fish", ".profile")])
def test_fichier_de_demarrage_du_shell(tmp_path, shell, attendu):
    assert bash("fichier_rc", tmp_path, shell=shell).stdout.strip() == f"{tmp_path}/{attendu}"


def test_fichier_de_demarrage_bash_sous_linux(tmp_path):
    chemin = faux_bin(tmp_path / "bin", uname=LINUX)
    assert bash("fichier_rc", tmp_path, path=chemin, shell="/bin/bash").stdout.strip() == f"{tmp_path}/.bashrc"


def test_path(tmp_path):
    assert bash(f'dans_path "/usr/bin:{tmp_path}/.local/bin:/bin"', tmp_path).returncode == 0
    assert bash(f'dans_path "{tmp_path}/.local/bin/"', tmp_path).returncode == 0
    assert bash('dans_path "/usr/bin:/bin"', tmp_path).returncode == 1

    rc = tmp_path / ".zshrc"
    rc.write_text("# mon zshrc\nalias ll='ls -l'\n", encoding="utf-8")
    assert bash(f'ajouter_au_path "{rc}"', tmp_path).returncode == 0
    contenu = rc.read_text(encoding="utf-8")
    assert contenu.startswith("# mon zshrc\nalias ll='ls -l'\n")
    assert contenu.rstrip().endswith('export PATH="$HOME/.local/bin:$PATH"')
    # Un nouveau terminal qui lit ce fichier trouve bien ~/.local/bin en premier.
    r = subprocess.run([BASH, "-c", f'. "{rc}"; echo "$PATH"'],
                       env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert r.stdout.strip().split(":")[0] == f"{tmp_path}/.local/bin"


def test_lanceur_avec_espaces_accents_et_apostrophe(tmp_path):
    faux_uv = tmp_path / "outils" / "uv"
    faux_uv.parent.mkdir()
    faux_uv.write_text('#!/bin/sh\nfor a in "$@"; do printf "[%s]" "$a"; done\n', encoding="utf-8")
    faux_uv.chmod(0o755)
    projet = tmp_path / "Téléchargements" / "l'été de jarvis"
    projet.mkdir(parents=True)
    lanceur = tmp_path / ".local" / "bin" / "jarvis"
    r = bash(f'creer_lanceur "{lanceur}" "{faux_uv}" "{projet}"', tmp_path)
    assert r.returncode == 0, r.stderr
    assert os.access(lanceur, os.X_OK)
    sortie = subprocess.run([str(lanceur), "chat", "--voix", "deux mots"], capture_output=True, text=True)
    assert sortie.stdout == f"[run][--project][{projet}][jarvis][chat][--voix][deux mots]", sortie.stderr
