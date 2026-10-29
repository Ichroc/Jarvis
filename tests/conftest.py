from __future__ import annotations

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from faux_ollama import FauxOllama  # noqa: E402

from jarvis.config import Config  # noqa: E402

DONNEES = Path(__file__).resolve().parent / "data"


@pytest.fixture
def faux_ollama():
    serveur = FauxOllama().demarrer()
    yield serveur
    serveur.arreter()


@pytest.fixture
def config(tmp_path, monkeypatch, faux_ollama) -> Config:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis"))
    cfg = Config()
    cfg.general.nom = "Ichroc"
    cfg.cerveau.hote_ollama = faux_ollama.url
    cfg.cerveau.prechauffage = False  # testé à part : il ajouterait une requête au début de chaque test
    cfg.preparer_dossiers()
    return cfg
