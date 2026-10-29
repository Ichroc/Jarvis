"""Petits outils de texte : préparer une réponse pour la voix, découper en phrases,
écrire une date en français, extraire le texte d'une page HTML."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from html.parser import HTMLParser

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def date_longue(d: datetime) -> str:
    """« mercredi 28 octobre 2026 » sans dépendre de la locale du système."""
    jour = "1er" if d.day == 1 else str(d.day)
    return f"{JOURS[d.weekday()]} {jour} {MOIS[d.month - 1]} {d.year}"


def heure_parlee(d: datetime) -> str:
    return f"{d.hour} h {d.minute:02d}" if d.minute else f"{d.hour} h"


def sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, sans ponctuation : pour comparer des mots."""
    texte = sans_accents(texte.lower())
    return re.sub(r"[^a-z0-9]+", " ", texte).strip()


# ---------------------------------------------------------------------------
# Préparer un texte pour la synthèse vocale
# ---------------------------------------------------------------------------

_BLOC_CODE = re.compile(r"```.*?(?:```|$)", re.S)
_LIEN_MD = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️←-⇿⬀-⯿]"
)


def pour_la_voix(texte: str) -> str:
    """Retire la mise en forme qui n'a pas de sens à l'oral (Markdown, liens, emojis)."""
    t = _BLOC_CODE.sub(" Le code est affiché à l'écran. ", texte)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = _LIEN_MD.sub(r"\1", t)
    t = _URL.sub("le lien affiché à l'écran", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*(?:[-*•+]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"\1", t)
    t = re.sub(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", "", t, flags=re.M)  # séparateurs de tableau
    t = t.replace("|", ", ")
    t = _EMOJI.sub("", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t.strip()


_ABREVIATIONS = {
    "m", "mm", "mme", "mmes", "mlle", "dr", "pr", "me", "st", "ste", "etc", "cf",
    "ex", "p", "vs", "env", "av", "apr", "no", "nº", "tél", "tel", "réf", "ref",
}


class DecoupeurPhrases:
    """Reçoit le texte morceau par morceau (comme il sort du modèle) et rend
    les phrases complètes dès qu'elles sont prêtes. C'est ce qui permet à Jarvis
    de commencer à parler avant d'avoir fini de réfléchir."""

    def __init__(self, min_car: int = 12, max_car: int = 240) -> None:
        self.min_car = min_car
        self.max_car = max_car
        self.tampon = ""

    def ajouter(self, morceau: str) -> list[str]:
        self.tampon += morceau
        phrases: list[str] = []
        while True:
            coupure = self._trouver_coupure()
            if coupure is None:
                break
            phrase, self.tampon = self.tampon[:coupure].strip(), self.tampon[coupure:]
            if phrase:
                phrases.append(phrase)
        return phrases

    def terminer(self) -> list[str]:
        reste, self.tampon = self.tampon.strip(), ""
        return [reste] if reste else []

    def _trouver_coupure(self) -> int | None:
        t = self.tampon
        # Ponctuation suivie d'un espace déjà reçu : on sait que la phrase est finie.
        for m in re.finditer(r"([.!?…]+|[;:])[\"»)]?(?=\s)|\n\s*\n", t):
            fin = m.end()
            candidat = t[:fin].strip()
            if len(candidat) < self.min_car:
                continue
            ponct = m.group(1) or ""
            if ponct == "." and self._abreviation(t[: m.start()]):
                continue
            if ponct in (";", ":") and len(candidat) < 60:
                continue
            return fin
        # Phrase interminable sans ponctuation : on coupe proprement sur une virgule.
        if len(t) > self.max_car:
            i = t.rfind(", ", 0, self.max_car)
            if i < self.min_car:
                i = t.rfind(" ", 0, self.max_car)
            if i > 0:
                return i + 1
        return None

    @staticmethod
    def _abreviation(avant: str) -> bool:
        mot = re.split(r"[\s(\"«]", avant)[-1].lower()
        if not mot:
            return False
        if len(mot) == 1 and mot.isalpha():
            return True  # initiale : « J. Dupont »
        return mot.rstrip(".") in _ABREVIATIONS


# ---------------------------------------------------------------------------
# HTML -> texte (pour les pages web et les documents .html)
# ---------------------------------------------------------------------------

class _Extracteur(HTMLParser):
    IGNORES = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
    BLOCS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.morceaux: list[str] = []
        self.profondeur_ignoree = 0
        self.titre = ""
        self._dans_titre = False

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORES:
            self.profondeur_ignoree += 1
        elif tag == "title":
            self._dans_titre = True
        elif tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORES and self.profondeur_ignoree:
            self.profondeur_ignoree -= 1
        elif tag == "title":
            self._dans_titre = False
        elif tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_data(self, data):
        if self._dans_titre:
            self.titre += data
        elif not self.profondeur_ignoree:
            self.morceaux.append(data)


def html_vers_texte(html: str) -> tuple[str, str]:
    """Renvoie (titre, texte lisible) d'une page HTML."""
    p = _Extracteur()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    texte = "".join(p.morceaux)
    texte = re.sub(r"[ \t\r\f\v]+", " ", texte)
    texte = re.sub(r"\n\s*\n+", "\n\n", texte)
    return p.titre.strip(), texte.strip()


def tronquer(texte: str, limite: int) -> str:
    if len(texte) <= limite:
        return texte
    return texte[: limite - 1].rstrip() + "…"
