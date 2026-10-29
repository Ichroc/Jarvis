from datetime import datetime

from jarvis.textutils import DecoupeurPhrases, date_longue, html_vers_texte, normaliser, pour_la_voix


def test_pour_la_voix_retire_la_mise_en_forme():
    texte = ("## Résumé\n**Important** : voici `ls -la` et [la doc](https://exemple.fr).\n"
             "- premier point\n- second point 🚀\n```python\nprint('x')\n```\nVoir https://site.fr/page")
    voix = pour_la_voix(texte)
    assert "#" not in voix and "**" not in voix and "`" not in voix and "🚀" not in voix
    assert "Important : voici ls -la et la doc." in voix
    assert "premier point" in voix and "- premier" not in voix
    assert "Le code est affiché à l'écran." in voix and "print" not in voix
    assert "le lien affiché à l'écran" in voix


def test_pour_la_voix_garde_les_noms_avec_soulignes():
    assert pour_la_voix("La variable nom_de_fichier vaut 3.") == "La variable nom_de_fichier vaut 3."


def test_decoupeur_phrases_en_flux():
    d = DecoupeurPhrases()
    morceaux = ["Bonjour Monsieur. Il fait ", "14 °C à Angers", " aujourd'hui. Une averse ", "est possible."]
    phrases = []
    for m in morceaux:
        phrases += d.ajouter(m)
    assert phrases == ["Bonjour Monsieur.", "Il fait 14 °C à Angers aujourd'hui."]
    assert d.terminer() == ["Une averse est possible."]


def test_decoupeur_respecte_abreviations_et_decimales():
    d = DecoupeurPhrases()
    phrases = d.ajouter("Rendez-vous avec M. Dupont à 3.5 km du centre, etc. et puis voilà. Suite ")
    assert phrases == ["Rendez-vous avec M. Dupont à 3.5 km du centre, etc. et puis voilà."]


def test_decoupeur_coupe_une_phrase_trop_longue():
    d = DecoupeurPhrases(max_car=80)
    phrases = d.ajouter("mot, " * 40)
    assert phrases and all(len(p) <= 81 for p in phrases)


def test_date_longue_en_francais():
    assert date_longue(datetime(2026, 10, 28)) == "mercredi 28 octobre 2026"
    assert date_longue(datetime(2026, 11, 1)) == "dimanche 1er novembre 2026"


def test_normaliser():
    assert normaliser("Été, déjà ÇA !") == "ete deja ca"


def test_html_vers_texte():
    titre, texte = html_vers_texte("<html><head><title>Doc</title><script>alert(1)</script></head>"
                                   "<body><nav>menu</nav><p>Premier</p><p>Second &amp; dernier</p></body></html>")
    assert titre == "Doc"
    assert "alert" not in texte and "menu" not in texte
    assert "Premier" in texte and "Second & dernier" in texte
