from jarvis.config import PACKAGE_DIR, Config


def test_valeurs_par_defaut():
    cfg = Config()
    assert cfg.cerveau.modele == "qwen3.5:9b"
    assert cfg.voix.modele_tts == "phonon:fr"
    assert cfg.voix.voix == "Noémie" and cfg.voix.voix_piper == "fr_FR-siwis-medium"
    assert cfg.interface.port == 7777


def test_fichier_exemple_valide():
    cfg = Config.charger(PACKAGE_DIR / "assets" / "config.example.toml")
    assert cfg.avertissements == []
    assert cfg.general.nom == "Ichroc"
    assert "Kubernetes" in cfg.oreilles.vocabulaire


def test_options_inconnues_et_mauvais_types(tmp_path):
    chemin = tmp_path / "config.toml"
    chemin.write_text('[general]\nville = "Lyon"\ncouleur = "bleu"\n[cerveau]\ncontexte = "beaucoup"\n'
                      'temperature = 1\n[mystere]\nx = 1\n', encoding="utf-8")
    cfg = Config.charger(chemin)
    assert cfg.general.ville == "Lyon"
    assert cfg.cerveau.contexte == 12288          # mauvais type : valeur par défaut gardée
    assert cfg.cerveau.temperature == 1.0          # un entier est accepté pour un réel
    assert len(cfg.avertissements) == 3


def test_toml_illisible(tmp_path):
    chemin = tmp_path / "config.toml"
    chemin.write_text("[general\nnom = ", encoding="utf-8")
    cfg = Config.charger(chemin)
    assert cfg.avertissements and cfg.general.ville == "Paris"


def test_dossier_personnel_protege(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "j"))
    cfg = Config()
    cfg.preparer_dossiers()
    assert cfg.dossier_notes.exists() and cfg.journal_audit.parent.exists()
    assert oct(cfg.dossier.stat().st_mode)[-3:] == "700"
