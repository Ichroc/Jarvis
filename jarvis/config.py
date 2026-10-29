"""Configuration de Jarvis.

Les valeurs par défaut sont définies ici. Ton fichier personnel
~/.jarvis/config.toml ne contient que ce que tu veux changer.

Les données personnelles (mémoire, notes, journal) vivent dans ~/.jarvis,
en dehors du code : elles ne risquent pas de partir sur GitHub avec le projet.
"""

from __future__ import annotations

import os
import platform
import sys
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_APPLE_SILICON = IS_MAC and platform.machine() == "arm64"
IS_LINUX = sys.platform.startswith("linux")

PACKAGE_DIR = Path(__file__).resolve().parent
MODELES_DIR = PACKAGE_DIR / "assets" / "modeles"


def dossier_donnees() -> Path:
    """Dossier des données personnelles (modifiable avec la variable JARVIS_HOME)."""
    return Path(os.environ.get("JARVIS_HOME", "~/.jarvis")).expanduser()


@dataclass
class General:
    nom: str = ""
    appellation: str = "Monsieur"
    tutoiement: bool = False
    ville: str = "Paris"


@dataclass
class Cerveau:
    modele: str = "qwen3.5:9b"
    modele_embeddings: str = "qwen3-embedding:0.6b"
    hote_ollama: str = "http://127.0.0.1:11434"
    reflexion: bool = False
    temperature: float = 0.6
    contexte: int = 12288
    garder_charge: str = "30m"
    prechauffage: bool = True
    etapes_max: int = 6


@dataclass
class Voix:
    active: bool = True
    moteur: str = "auto"
    modele_tts: str = "phonon:fr"
    voix: str = "Noémie"
    voix_piper: str = "fr_FR-siwis-medium"
    debit: float = 1.0
    voix_say: str = "Amélie"
    vitesse_say: int = 185
    lire_reponses_ecrites: bool = False


@dataclass
class Oreilles:
    active: bool = True
    moteur: str = "auto"
    modele_whisper: str = "mlx-community/whisper-large-v3-turbo"
    modele_parakeet: str = "mlx-community/parakeet-tdt-0.6b-v3"
    modele_faster_whisper: str = "small"
    vocabulaire: list[str] = field(default_factory=list)
    mot_activation: bool = True
    seuil_activation: float = 0.5
    seuil_voix: float = 0.5
    silence_fin_ms: int = 700
    conversation_continue: bool = True
    micro: str = ""


@dataclass
class Securite:
    mode_prudent: bool = False
    internet: bool = True
    shell: bool = True
    journal: bool = True
    delai_confirmation_s: int = 45


@dataclass
class Interface:
    port: int = 7777
    ouvrir_navigateur: bool = True
    lecteur_musique: str = "Music"


@dataclass
class Documents:
    dossiers: list[str] = field(default_factory=lambda: ["~/Documents"])
    extensions: list[str] = field(
        default_factory=lambda: [
            ".md", ".txt", ".pdf", ".docx", ".py", ".c", ".h", ".sh",
            ".csv", ".json", ".yaml", ".yml", ".html", ".tex",
        ]
    )


SECTIONS: dict[str, type] = {
    "general": General,
    "cerveau": Cerveau,
    "voix": Voix,
    "oreilles": Oreilles,
    "securite": Securite,
    "interface": Interface,
    "documents": Documents,
}


@dataclass
class Config:
    general: General = field(default_factory=General)
    cerveau: Cerveau = field(default_factory=Cerveau)
    voix: Voix = field(default_factory=Voix)
    oreilles: Oreilles = field(default_factory=Oreilles)
    securite: Securite = field(default_factory=Securite)
    interface: Interface = field(default_factory=Interface)
    documents: Documents = field(default_factory=Documents)
    chemin: Path | None = None
    avertissements: list[str] = field(default_factory=list)

    # --- chemins -----------------------------------------------------------
    @property
    def dossier(self) -> Path:
        return dossier_donnees()

    @property
    def base(self) -> Path:
        return self.dossier / "jarvis.db"

    @property
    def dossier_notes(self) -> Path:
        return self.dossier / "notes"

    @property
    def journal_audit(self) -> Path:
        return self.dossier / "journal" / "audit.jsonl"

    # --- chargement --------------------------------------------------------
    @classmethod
    def charger(cls, chemin: Path | None = None) -> "Config":
        chemin = Path(chemin) if chemin else dossier_donnees() / "config.toml"
        cfg = cls(chemin=chemin)
        if not chemin.exists():
            return cfg
        try:
            with open(chemin, "rb") as f:
                donnees = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            cfg.avertissements.append(f"config.toml illisible ({e}) : valeurs par défaut utilisées.")
            return cfg

        for nom_section, valeurs in donnees.items():
            classe = SECTIONS.get(nom_section)
            if classe is None or not isinstance(valeurs, dict):
                cfg.avertissements.append(f"Section inconnue ignorée : [{nom_section}]")
                continue
            section = getattr(cfg, nom_section)
            connues = {f.name for f in fields(classe)}
            for cle, valeur in valeurs.items():
                if cle not in connues:
                    cfg.avertissements.append(f"Option inconnue ignorée : {nom_section}.{cle}")
                    continue
                actuelle = getattr(section, cle)
                convertie = _convertir(valeur, actuelle)
                if convertie is None:
                    cfg.avertissements.append(
                        f"{nom_section}.{cle} : type attendu {type(actuelle).__name__}, "
                        f"reçu {type(valeur).__name__}. Valeur par défaut conservée."
                    )
                    continue
                setattr(section, cle, convertie)
        return cfg

    def preparer_dossiers(self) -> None:
        for d in (self.dossier, self.dossier_notes, self.journal_audit.parent):
            d.mkdir(parents=True, exist_ok=True)
        # Les données personnelles ne sont lisibles que par toi.
        try:
            os.chmod(self.dossier, 0o700)
        except OSError:
            pass

    def en_dict(self) -> dict:
        return {nom: asdict(getattr(self, nom)) for nom in SECTIONS}


def _convertir(valeur, reference):
    """Vérifie qu'une valeur du TOML a le bon type (TOML est typé, on reste strict)."""
    if isinstance(reference, bool):
        return valeur if isinstance(valeur, bool) else None
    if isinstance(reference, int):
        return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else None
    if isinstance(reference, float):
        if isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
            return float(valeur)
        return None
    if isinstance(reference, str):
        return valeur if isinstance(valeur, str) else None
    if isinstance(reference, list):
        if isinstance(valeur, list) and all(isinstance(v, str) for v in valeur):
            return list(valeur)
        return None
    return valeur
