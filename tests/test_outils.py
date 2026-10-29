from datetime import datetime, timedelta

import pytest

from jarvis import tools as registre
from jarvis.llm import Ollama
from jarvis.scheduler import Planificateur
from jarvis.tools import ArgumentInvalide, Contexte
from jarvis.tools.essentiels import calcul_sur, calculer, heure_et_date

registre.charger_tous()


@pytest.fixture
def ctx(config):
    return Contexte(config=config, llm=Ollama(config.cerveau.hote_ollama),
                    planificateur=Planificateur(config.base))


def appeler(ctx, nom, **args):
    return registre.obtenir(nom).appeler(ctx, args)


# ------------------------------------------------------------------ registre et schémas
def test_registre_complet():
    noms = {o.nom for o in registre.tous()}
    assert {"heure_et_date", "calculer", "meteo", "minuteur", "memoriser", "executer_commande",
            "regarder_ecran", "chercher_documents", "lire_page_web"} <= noms
    assert len(noms) == 26


def test_schemas_generes_depuis_la_signature():
    minuteur = registre.obtenir("minuteur").parametres
    assert minuteur["properties"]["minutes"]["type"] == "number"
    assert minuteur["required"] == ["minutes"]
    musique = registre.obtenir("controle_musique").parametres["properties"]["action"]
    assert musique["enum"] == ["lecture", "pause", "suivant", "precedent", "info"]
    assert "ctx" not in registre.obtenir("meteo").parametres["properties"]


def test_conversion_des_arguments():
    rappel = registre.obtenir("creer_rappel")
    args = rappel.preparer_arguments({"texte": "pâtes", "dans_minutes": "0,5", "inconnu": 1})
    assert args == {"texte": "pâtes", "dans_minutes": 0.5}
    assert registre.obtenir("regler_volume").preparer_arguments({"niveau": "40"}) == {"niveau": 40}
    assert registre.obtenir("controle_musique").preparer_arguments({"action": "PAUSE"}) == {"action": "pause"}
    with pytest.raises(ArgumentInvalide):
        rappel.preparer_arguments({"dans_minutes": 3})
    with pytest.raises(ArgumentInvalide):
        registre.obtenir("controle_musique").preparer_arguments({"action": "danser"})
    with pytest.raises(ArgumentInvalide):
        registre.obtenir("regler_volume").preparer_arguments({"niveau": "fort"})


def test_outils_reseau_masques_en_mode_confidentiel(config):
    tous = {o.nom for o in registre.disponibles(config)}
    prives = {o.nom for o in registre.disponibles(config, confidentiel=True)}
    assert "meteo" in tous and "meteo" not in prives and "heure_et_date" in prives
    config.securite.shell = False
    assert "executer_commande" not in {o.nom for o in registre.disponibles(config)}


# ------------------------------------------------------------------ outils essentiels
def test_calculatrice_exacte_et_sure():
    assert calcul_sur("2 + 2 * 3") == 8
    assert calcul_sur("sqrt(16) + 2^3") == 12
    assert calcul_sur("12,5".replace(",", ".")) == 12.5
    assert "= 4" in calculer("(10-2)/2")
    assert "division par zéro" in calculer("1/0")
    assert calculer("__import__('os').system('ls')").startswith("Erreur")
    assert calculer("2**100000").startswith("Erreur")
    assert calculer("open('/etc/passwd')").startswith("Erreur")


def test_heure_et_date():
    texte = heure_et_date()
    assert str(datetime.now().year) in texte and "semaine" in texte


def test_etat_systeme(ctx):
    texte = appeler(ctx, "etat_systeme")
    assert "Processeur" in texte and "Mémoire" in texte and "qwen3.5:9b" in texte


# ------------------------------------------------------------------ notes
def test_notes_ecrire_lister_chercher(ctx):
    assert "créée" in appeler(ctx, "ecrire_note", titre="Idées PFE", contenu="SOC avec Wazuh et Suricata")
    assert "complétée" in appeler(ctx, "ecrire_note", titre="Idées PFE", contenu="Tableau de bord Grafana")
    assert "Idées PFE" in appeler(ctx, "lire_notes")
    trouve = appeler(ctx, "lire_notes", recherche="suricata")
    assert "Wazuh" in trouve and "Grafana" in trouve
    assert "Aucune note" in appeler(ctx, "lire_notes", recherche="licorne")


# ------------------------------------------------------------------ rappels
def test_rappels_et_minuteurs(ctx):
    assert "programmé" in appeler(ctx, "creer_rappel", texte="appeler Sam", dans_minutes=30)
    demain = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0).isoformat(timespec="minutes")
    assert "demain à 09:00" in appeler(ctx, "creer_rappel", texte="réunion", date_heure=demain)
    assert "déjà passé" in appeler(ctx, "creer_rappel", texte="trop tard", date_heure="2020-01-01T10:00")
    assert "illisible" in appeler(ctx, "creer_rappel", texte="?", date_heure="mardi prochain")
    assert "Erreur" in appeler(ctx, "creer_rappel", texte="?")
    assert "30 s" in appeler(ctx, "minuteur", minutes=0.5, libelle="thé")
    liste = appeler(ctx, "lister_rappels")
    assert "appeler Sam" in liste and "thé" in liste and "réunion" in liste
    assert "annulé" in appeler(ctx, "annuler_rappel", numero=1)
    assert "Aucun rappel actif" in appeler(ctx, "annuler_rappel", numero=1)


def test_planificateur_declenche_a_l_heure(config):
    maintenant = [datetime(2026, 10, 28, 9, 0)]
    sonnes = []
    p = Planificateur(config.base, quand_echu=lambda r, retard: sonnes.append((r.texte, retard)),
                      horloge=lambda: maintenant[0])
    p.ajouter("café", datetime(2026, 10, 28, 9, 5))
    p.ajouter("vieux", datetime(2026, 10, 27, 8, 0))  # manqué depuis plus de 12 h : pas annoncé
    assert p.verifier() == [] and sonnes == []          # « vieux » est marqué manqué, rien ne sonne
    maintenant[0] = datetime(2026, 10, 28, 9, 5, 30)
    assert [r.texte for r in p.verifier()] == ["café"]
    assert sonnes == [("café", False)]
    assert p.lister() == []


# ------------------------------------------------------------------ web (sans réseau : réponses simulées)
class _Reponse:
    def __init__(self, donnees):
        self._d = donnees

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def test_meteo_avec_reponses_simulees(ctx, monkeypatch):
    from jarvis.tools import web

    def faux_get(url, **kwargs):
        if "geocoding" in url:
            return _Reponse({"results": [{"name": "Angers", "admin1": "Pays de la Loire", "country": "France",
                                          "latitude": 47.47, "longitude": -0.55}]})
        return _Reponse({
            "current": {"time": "2026-10-28T14:00", "temperature_2m": 14.2, "apparent_temperature": 13.0,
                        "relative_humidity_2m": 81, "weather_code": 2, "wind_speed_10m": 16.0},
            "hourly": {"time": [f"2026-10-28T{h:02d}:00" for h in range(24)],
                       "temperature_2m": [10 + h / 4 for h in range(24)],
                       "precipitation_probability": [60 if h == 18 else 0 for h in range(24)],
                       "weather_code": [80 if h == 18 else 2 for h in range(24)]},
            "daily": {"time": ["2026-10-28"], "temperature_2m_min": [9.0], "temperature_2m_max": [16.4],
                      "weather_code": [80], "precipitation_probability_max": [60]},
        })

    monkeypatch.setattr(web._session, "get", faux_get)
    texte = appeler(ctx, "meteo", ville="Angers", jours=1)
    assert "Angers, Pays de la Loire, France" in texte
    assert "14,2 °C (ressenti 13 °C)" in texte and "partiellement nuageux" in texte
    assert "17 h-20 h : 14,2 °C, averses faibles, pluie 60 %" in texte  # l'averse de 18 h n'est pas ratée
    assert "14 h-17 h : 13,5 °C, partiellement nuageux ·" in texte
    assert "9 à 16,4 °C" in texte
    assert ctx.requetes_internet == 2


def test_protection_ssrf():
    from jarvis.tools.web import adresse_publique
    assert not adresse_publique("http://127.0.0.1:11434/api/tags")
    assert not adresse_publique("http://192.168.1.1/admin")
    assert not adresse_publique("http://10.0.0.5")
    assert not adresse_publique("http://[::1]/")
    assert not adresse_publique("http://169.254.169.254/latest/meta-data")
    assert adresse_publique("http://93.184.215.14/")


def test_lire_page_refuse_les_adresses_locales(ctx):
    assert "refusée" in appeler(ctx, "lire_page_web", url="http://127.0.0.1:11434/api/tags")
    assert "http(s)" in appeler(ctx, "lire_page_web", url="file:///etc/passwd")


def test_commande_terminal():
    from jarvis.tools.terminal import executer_commande
    sortie = executer_commande("echo bonjour")
    assert "Code de retour : 0" in sortie and "bonjour" in sortie and "CONTENU_EXTERNE" in sortie
