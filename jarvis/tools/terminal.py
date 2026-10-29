"""Exécuter une commande dans le terminal : toujours avec ton accord explicite.

La politique de sécurité bloque d'office les commandes destructrices (sudo,
formatage, suppression récursive de dossiers racines…). Les autres s'affichent
en entier dans une fenêtre de confirmation avant de s'exécuter.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from jarvis.security import envelopper_externe
from jarvis.textutils import tronquer
from jarvis.tools import outil


@outil(
    description=("Exécute une commande shell (zsh sur Mac) et renvoie sa sortie. Utile pour des infos "
                 "système ou réseau (ex. adresse IP, espace disque, version d'un outil). "
                 "L'utilisateur devra confirmer avant l'exécution."),
    params={"commande": "Commande complète, sur une ligne."},
    risque="sensible", categorie="terminal", prive=True, externe=True, icone="❯", libelle="terminal",
)
def executer_commande(commande: str) -> str:
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        r = subprocess.run([shell, "-c", commande], cwd=str(Path.home()), capture_output=True,
                           text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "La commande a dépassé 30 secondes et a été arrêtée."
    sortie = (r.stdout or "") + (("\n[erreurs]\n" + r.stderr) if r.stderr.strip() else "")
    return envelopper_externe(
        "terminal", f"Code de retour : {r.returncode}\n{tronquer(sortie.strip() or '(aucune sortie)', 5000)}"
    )
