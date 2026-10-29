"""Outils de base : l'heure, le calcul exact, l'état de la machine."""

from __future__ import annotations

import ast
import math
import operator
import time
from datetime import datetime

from jarvis.textutils import date_longue
from jarvis.tools import Contexte, outil


@outil(
    description="Donne la date, l'heure et le jour de la semaine actuels.",
    icone="◷", libelle="heure",
)
def heure_et_date() -> str:
    maintenant = datetime.now().astimezone()
    decalage = maintenant.strftime("%z")
    decalage = f"UTC{decalage[:3]}:{decalage[3:]}" if decalage else "heure locale"
    semaine = maintenant.isocalendar().week
    return (f"{date_longue(maintenant).capitalize()}, {maintenant:%H:%M:%S} ({decalage}), "
            f"semaine {semaine}.")


# --- calculatrice sûre ------------------------------------------------------
# On n'utilise jamais eval() : on lit l'expression comme un arbre syntaxique et
# on n'autorise que les nombres, les opérateurs et quelques fonctions mathématiques.

_OPERATEURS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FONCTIONS = {
    "sqrt": math.sqrt, "racine": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "min": min, "max": max, "pow": pow, "hypot": math.hypot, "degrees": math.degrees,
    "radians": math.radians, "factorial": lambda n: math.factorial(int(n)) if 0 <= n <= 170 else math.inf,
}
_CONSTANTES = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _evaluer(noeud):
    if isinstance(noeud, ast.Expression):
        return _evaluer(noeud.body)
    if isinstance(noeud, ast.Constant) and isinstance(noeud.value, (int, float)) and not isinstance(noeud.value, bool):
        return noeud.value
    if isinstance(noeud, ast.BinOp) and type(noeud.op) in _OPERATEURS:
        gauche, droite = _evaluer(noeud.left), _evaluer(noeud.right)
        if isinstance(noeud.op, ast.Pow) and abs(droite) > 1000:
            raise ValueError("exposant trop grand")
        return _OPERATEURS[type(noeud.op)](gauche, droite)
    if isinstance(noeud, ast.UnaryOp) and type(noeud.op) in _OPERATEURS:
        return _OPERATEURS[type(noeud.op)](_evaluer(noeud.operand))
    if isinstance(noeud, ast.Name) and noeud.id in _CONSTANTES:
        return _CONSTANTES[noeud.id]
    if isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name) and noeud.func.id in _FONCTIONS \
            and not noeud.keywords:
        return _FONCTIONS[noeud.func.id](*[_evaluer(a) for a in noeud.args])
    raise ValueError("expression non autorisée")


def calcul_sur(expression: str) -> float | int:
    expr = (expression.replace("×", "*").replace("÷", "/").replace("^", "**")
            .replace("−", "-").replace("√", "sqrt").strip())
    if len(expr) > 300:
        raise ValueError("expression trop longue")
    return _evaluer(ast.parse(expr, mode="eval"))


def formater_nombre(x: float | int) -> str:
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return str(x)
        if x.is_integer() and abs(x) < 1e15:
            return str(int(x))
        return f"{x:.10g}"
    return str(x)


@outil(
    description=("Calcule exactement une expression mathématique (+ - * / // % **, sqrt, sin, cos, log, "
                 "exp, pi, e…). À utiliser pour TOUT calcul, même simple, plutôt que de calculer de tête."),
    params={"expression": "Expression en syntaxe Python, ex. « (12.5*3)/4 » ou « sqrt(2)*100 »."},
    icone="∑", libelle="calcul",
)
def calculer(expression: str) -> str:
    try:
        return f"{expression} = {formater_nombre(calcul_sur(expression))}"
    except ZeroDivisionError:
        return "Erreur : division par zéro."
    except (ValueError, SyntaxError, TypeError, OverflowError) as e:
        return f"Erreur : impossible de calculer « {expression} » ({e})."


@outil(
    description="Donne l'état de l'ordinateur : processeur, mémoire, batterie, disque, modèles IA chargés.",
    icone="◎", libelle="état système",
)
def etat_systeme(ctx: Contexte) -> str:
    import psutil

    lignes = []
    cpu = psutil.cpu_percent(interval=0.3)
    ram = psutil.virtual_memory()
    lignes.append(f"Processeur : {cpu:.0f} % ({psutil.cpu_count()} cœurs).")
    lignes.append(f"Mémoire : {ram.used / 1e9:.1f} Go utilisés sur {ram.total / 1e9:.1f} Go ({ram.percent:.0f} %).")
    swap = psutil.swap_memory()
    if swap.used > 0.5e9:
        lignes.append(f"Mémoire d'échange (swap) : {swap.used / 1e9:.1f} Go, signe que la RAM est saturée.")
    batterie = psutil.sensors_battery()
    if batterie is not None:
        etat = "en charge" if batterie.power_plugged else "sur batterie"
        lignes.append(f"Batterie : {batterie.percent:.0f} % ({etat}).")
    disque = psutil.disk_usage(str(ctx.config.dossier.anchor or "/"))
    lignes.append(f"Disque : {disque.free / 1e9:.0f} Go libres sur {disque.total / 1e9:.0f} Go.")
    duree = time.time() - psutil.boot_time()
    lignes.append(f"Allumé depuis {int(duree // 86400)} j {int(duree % 86400 // 3600)} h.")
    if ctx.llm:
        try:
            charges = ctx.llm.charges()
            if charges:
                details = ", ".join(f"{m.get('name')} ({(m.get('size_vram') or m.get('size') or 0) / 1e9:.1f} Go)"
                                    for m in charges)
                lignes.append(f"Modèles chargés en mémoire : {details}.")
        except Exception:
            lignes.append("Ollama ne répond pas.")
    return "\n".join(lignes)
