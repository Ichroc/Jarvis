"""Registre des outils de Jarvis.

Un outil est une fonction Python ordinaire décorée avec @outil. Le décorateur
lit sa signature (noms, types, valeurs par défaut) et en fabrique le schéma JSON
que le modèle reçoit. Le modèle choisit l'outil et ses arguments ; c'est Jarvis
qui l'exécute, après avoir consulté la politique de sécurité.

Niveaux de risque :
  lecture   lit une information, sans effet de bord        -> exécuté directement
  action    effet local et réversible (note, rappel, son)  -> exécuté et journalisé
  sensible  effet difficile à annuler (commande, oubli)    -> demande ton accord
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
import types
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    from jarvis.config import Config
    from jarvis.events import Bus
    from jarvis.knowledge import BaseDocuments
    from jarvis.llm import Ollama
    from jarvis.memory import Memoire
    from jarvis.scheduler import Planificateur


class ArgumentInvalide(ValueError):
    pass


@dataclass
class Contexte:
    """Ce dont les outils ont besoin pour travailler."""
    config: "Config"
    llm: "Ollama | None" = None
    memoire: "Memoire | None" = None
    documents: "BaseDocuments | None" = None
    planificateur: "Planificateur | None" = None
    bus: "Bus | None" = None
    requetes_internet: int = 0  # compteur affiché dans le HUD (transparence)


@dataclass
class Outil:
    nom: str
    description: str
    fonction: Callable[..., Any]
    parametres: dict
    risque: str = "lecture"
    categorie: str = "essentiel"
    prive: bool = False      # lit des données personnelles (presse-papiers, notes, écran…)
    reseau: bool = False     # envoie une requête sur internet
    externe: bool = False    # renvoie du contenu non fiable (web, documents)
    icone: str = "◆"
    systemes: tuple[str, ...] = ("darwin", "linux")
    veut_contexte: bool = False
    libelle: str = ""

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.nom, "description": self.description, "parameters": self.parametres},
        }

    def preparer_arguments(self, bruts: dict) -> dict:
        props = self.parametres.get("properties", {})
        requis = set(self.parametres.get("required", []))
        args: dict = {}
        for nom, spec in props.items():
            valeur = bruts.get(nom)
            if valeur is None or valeur == "":
                if nom in requis:
                    raise ArgumentInvalide(f"paramètre obligatoire manquant : « {nom} »")
                continue
            args[nom] = _convertir(valeur, spec, nom)
        return args  # les arguments inventés par le modèle sont ignorés

    def appeler(self, ctx: Contexte, bruts: dict) -> Any:
        args = self.preparer_arguments(bruts)
        return self.fonction(ctx, **args) if self.veut_contexte else self.fonction(**args)


_REGISTRE: dict[str, Outil] = {}

MODULES = ("essentiels", "notes", "rappels", "souvenirs", "documents", "systeme", "web", "terminal")


def outil(*, description: str, risque: str = "lecture", categorie: str = "essentiel",
          prive: bool = False, reseau: bool = False, externe: bool = False, icone: str = "◆",
          params: dict[str, str] | None = None, systemes: tuple[str, ...] = ("darwin", "linux"),
          libelle: str = "", nom: str | None = None):
    """Déclare une fonction comme outil utilisable par le modèle."""
    assert risque in ("lecture", "action", "sensible"), risque

    def decorer(fonction: Callable) -> Callable:
        signature = inspect.signature(fonction)
        proprietes: dict = {}
        requis: list[str] = []
        veut_ctx = False
        for p in signature.parameters.values():
            if p.name == "ctx":
                veut_ctx = True
                continue
            spec = _schema_type(_resoudre_annotation(fonction, p.annotation))
            if params and p.name in params:
                spec["description"] = params[p.name]
            if p.default is inspect.Parameter.empty:
                requis.append(p.name)
            proprietes[p.name] = spec
        o = Outil(
            nom=nom or fonction.__name__, description=description, fonction=fonction,
            parametres={"type": "object", "properties": proprietes, "required": requis},
            risque=risque, categorie=categorie, prive=prive, reseau=reseau, externe=externe,
            icone=icone, systemes=systemes, veut_contexte=veut_ctx,
            libelle=libelle or (nom or fonction.__name__).replace("_", " "),
        )
        _REGISTRE[o.nom] = o
        return fonction

    return decorer


def charger_tous() -> dict[str, Outil]:
    for m in MODULES:
        importlib.import_module(f"jarvis.tools.{m}")
    return _REGISTRE


def obtenir(nom: str) -> Outil | None:
    return _REGISTRE.get(nom)


def tous() -> list[Outil]:
    return list(_REGISTRE.values())


def disponibles(config: "Config", *, confidentiel: bool = False) -> list[Outil]:
    """Outils proposés au modèle selon le système et tes réglages."""
    resultat = []
    for o in _REGISTRE.values():
        if sys.platform not in o.systemes:
            continue
        if o.reseau and (confidentiel or not config.securite.internet):
            continue
        if o.categorie == "terminal" and not config.securite.shell:
            continue
        resultat.append(o)
    return resultat


# ---------------------------------------------------------------------------
# Schémas JSON et conversion des arguments
# ---------------------------------------------------------------------------

def _resoudre_annotation(fonction: Callable, annotation: Any) -> Any:
    """Avec « from __future__ import annotations », les types sont des chaînes :
    on les évalue dans le module de la fonction."""
    if annotation is inspect.Parameter.empty:
        return str
    if isinstance(annotation, str):
        try:
            return eval(annotation, vars(sys.modules[fonction.__module__]), {"Literal": Literal})  # noqa: S307
        except Exception:
            return str
    return annotation


def _schema_type(t: Any) -> dict:
    origine = typing.get_origin(t)
    args = typing.get_args(t)
    if origine is Literal:
        return {"type": "string", "enum": [str(a) for a in args]}
    if origine in (typing.Union, types.UnionType):
        non_nuls = [a for a in args if a is not type(None)]
        return _schema_type(non_nuls[0]) if non_nuls else {"type": "string"}
    if origine in (list, typing.List):
        return {"type": "array", "items": _schema_type(args[0]) if args else {"type": "string"}}
    return {
        str: {"type": "string"}, int: {"type": "integer"},
        float: {"type": "number"}, bool: {"type": "boolean"},
    }.get(t, {"type": "string"})


_VRAI = {"true", "vrai", "oui", "yes", "1", "on"}
_FAUX = {"false", "faux", "non", "no", "0", "off"}


def _convertir(valeur: Any, spec: dict, nom: str) -> Any:
    """Les petits modèles se trompent parfois de type (« 10 » au lieu de 10) :
    on corrige ce qui est sans ambiguïté et on refuse le reste avec un message clair."""
    t = spec.get("type", "string")
    try:
        if t == "integer":
            if isinstance(valeur, bool):
                raise ValueError
            if isinstance(valeur, (int, float)):
                if float(valeur).is_integer():
                    return int(valeur)
                raise ValueError
            return int(float(str(valeur).strip().replace(",", ".")))
        if t == "number":
            if isinstance(valeur, bool):
                raise ValueError
            return float(str(valeur).strip().replace(",", ".")) if isinstance(valeur, str) else float(valeur)
        if t == "boolean":
            if isinstance(valeur, bool):
                return valeur
            s = str(valeur).strip().lower()
            if s in _VRAI:
                return True
            if s in _FAUX:
                return False
            raise ValueError
        if t == "array":
            if isinstance(valeur, str):
                try:
                    valeur = json.loads(valeur)
                except json.JSONDecodeError:
                    valeur = [valeur]
            if not isinstance(valeur, list):
                valeur = [valeur]
            items = spec.get("items", {"type": "string"})
            return [_convertir(v, items, nom) for v in valeur]
        # chaîne
        s = valeur if isinstance(valeur, str) else json.dumps(valeur, ensure_ascii=False) \
            if isinstance(valeur, (dict, list)) else str(valeur)
        if "enum" in spec:
            for choix in spec["enum"]:
                if s.strip().lower() == choix.lower():
                    return choix
            raise ArgumentInvalide(f"« {nom} » doit valoir : {', '.join(spec['enum'])} (reçu « {s} »)")
        return s
    except ArgumentInvalide:
        raise
    except (ValueError, TypeError):
        raise ArgumentInvalide(f"« {nom} » devrait être de type {t} (reçu {valeur!r})") from None
