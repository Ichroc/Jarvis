import docx
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from jarvis.knowledge import BaseDocuments, decouper
from jarvis.llm import Ollama
from jarvis.memory import Memoire, Vectoriseur, score_lexical


@pytest.fixture
def vectoriseur(config):
    return Vectoriseur(Ollama(config.cerveau.hote_ollama), config.cerveau.modele_embeddings)


# ------------------------------------------------------------------ mémoire
def test_memoire_ajout_recherche_et_doublons(config, vectoriseur):
    m = Memoire(config.base, vectoriseur)
    s1, nouveau = m.ajouter("Le stage d'Ichroc commence le 2 février 2027 à Rennes.")
    assert nouveau
    m.ajouter("Ichroc boit son café sans sucre.")
    m.ajouter("Sam est le meilleur ami d'Ichroc.")
    trouves = m.rechercher("Quand commence mon stage à Rennes ?")
    assert trouves and trouves[0].id == s1.id
    _, nouveau = m.ajouter("Le stage d'Ichroc commence le 2 février 2027 à Rennes")
    assert not nouveau and m.compter() == 3
    assert m.rechercher("recette de cuisine italienne") == []


def test_memoire_sans_embeddings(config):
    m = Memoire(config.base, None)
    m.ajouter("Ichroc est allergique aux arachides.")
    m.ajouter("Le Wi-Fi de la maison s'appelle Atelier.")
    trouves = m.rechercher("allergique arachides ?")
    assert len(trouves) == 1 and "arachides" in trouves[0].texte


def test_memoire_complete_les_vecteurs_manquants(config, vectoriseur):
    hors_ligne = Memoire(config.base, None)
    hors_ligne.ajouter("La soutenance du PFE aura lieu en juin.")
    en_ligne = Memoire(config.base, vectoriseur)
    assert en_ligne.rechercher("Quand a lieu la soutenance du PFE ?")
    ligne = en_ligne.db.execute("SELECT modele FROM souvenirs").fetchone()
    assert ligne[0] == "qwen3-embedding:0.6b"


def test_oublier(config, vectoriseur):
    m = Memoire(config.base, vectoriseur)
    s, _ = m.ajouter("Code de la porte du garage : à ne jamais oublier")
    m.ajouter("Ichroc aime la musique classique.")
    assert m.oublier(s.id).texte.startswith("Code de la porte")
    assert m.oublier(s.id) is None
    effaces = m.oublier_requete("musique classique")
    assert len(effaces) == 1 and m.compter() == 0


def test_score_lexical():
    assert score_lexical("café sans sucre", "Il boit son café sans sucre") > 0.5
    assert score_lexical("le la les", "un une des") == 0


# ------------------------------------------------------------------ documents
def _pdf(chemin, lignes):
    c = canvas.Canvas(str(chemin), pagesize=A4)
    y = 800
    for ligne in lignes:
        c.drawString(60, y, ligne)
        y -= 18
    c.save()


def test_decoupage():
    texte = "\n\n".join(f"Paragraphe {i}. " + "mot " * 60 for i in range(10))
    morceaux = decouper(texte, taille=600, chevauchement=100)
    assert len(morceaux) > 3 and all(len(m) <= 700 for m in morceaux)
    assert decouper("court") == []


def test_indexation_et_recherche(config, vectoriseur, tmp_path):
    dossier = tmp_path / "cours"
    (dossier / "reseau").mkdir(parents=True)
    (dossier / ".git").mkdir()
    (dossier / "kubernetes.md").write_text(
        "# Kubernetes\n\nUn Service Kubernetes expose des Pods derrière une adresse stable. "
        "Les types sont ClusterIP, NodePort et LoadBalancer.\n\nLe selector choisit les Pods par labels.",
        encoding="utf-8")
    (dossier / "reseau" / "nat.txt").write_text(
        "La traduction d'adresses NAT avec iptables permet à une passerelle de masquer un réseau privé. "
        "La règle MASQUERADE de la table nat est la plus utilisée.", encoding="utf-8")
    _pdf(dossier / "wazuh.pdf", ["Wazuh est un SIEM et XDR open source.",
                                 "Les agents Wazuh envoient les journaux au manager pour analyse et alertes."])
    d = docx.Document()
    d.add_paragraph("Compte rendu du projet Docker : les conteneurs Apache et MariaDB communiquent par un réseau dédié.")
    d.save(dossier / "docker.docx")
    (dossier / ".git" / "config").write_text("secret ignoré", encoding="utf-8")

    base = BaseDocuments(config.base, vectoriseur)
    stats = base.indexer([dossier], [".md", ".txt", ".pdf", ".docx"])
    assert stats["nouveaux"] == 4 and stats["erreurs"] == 0
    assert base.rechercher("types de Service Kubernetes ClusterIP NodePort")[0].chemin.endswith("kubernetes.md")
    assert base.rechercher("agents Wazuh journaux manager")[0].chemin.endswith("wazuh.pdf")
    assert base.rechercher("conteneurs Apache MariaDB réseau Docker")[0].chemin.endswith("docker.docx")
    assert base.rechercher("règle MASQUERADE iptables NAT")[0].chemin.endswith("nat.txt")

    stats = base.indexer([dossier], [".md", ".txt", ".pdf", ".docx"])
    assert stats["inchanges"] == 4 and stats["nouveaux"] == 0
    (dossier / "reseau" / "nat.txt").unlink()
    stats = base.indexer([dossier], [".md", ".txt", ".pdf", ".docx"])
    assert stats["retires"] == 1 and base.stats()["fichiers"] == 3


def test_recherche_documents_sans_embeddings(config, tmp_path):
    (tmp_path / "a.md").write_text("Le protocole SSH chiffre les échanges et utilise le port 22 par défaut.",
                                   encoding="utf-8")
    base = BaseDocuments(config.base, None)
    base.indexer([tmp_path], [".md"])
    assert base.rechercher("port SSH par défaut")[0].chemin.endswith("a.md")
