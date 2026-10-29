"""Mot d'activation « Hey Jarvis », 100 % local.

Réimplémentation compacte du pipeline openWakeWord (Apache-2.0), avec ses
modèles ONNX pré-entraînés :

  audio 80 ms ─► melspectrogramme ─► embedding (96 valeurs) ─► classifieur « hey jarvis »
                 (melspectrogram.onnx)  (embedding_model.onnx)   (16 derniers embeddings)

Chaque bloc de 80 ms coûte environ 1 ms de calcul : on peut écouter en
permanence sans que le processeur ne le remarque.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from jarvis.config import MODELES_DIR

BLOC = 1280            # 80 ms à 16 kHz
RECOUVREMENT = 480     # 3 trames de 10 ms de contexte pour le melspectrogramme
FENETRE_MEL = 76       # trames mel par embedding (~775 ms)


class DetecteurMotActivation:
    def __init__(self, modele: Path = MODELES_DIR / "hey_jarvis_v0.1.onnx", seuil: float = 0.5,
                 dossier: Path = MODELES_DIR) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.log_severity_level = 3
        cpu = ["CPUExecutionProvider"]
        self.mel = ort.InferenceSession(str(dossier / "melspectrogram.onnx"), sess_options=options, providers=cpu)
        self.emb = ort.InferenceSession(str(dossier / "embedding_model.onnx"), sess_options=options, providers=cpu)
        self.mot = ort.InferenceSession(str(modele), sess_options=options, providers=cpu)
        self.entree_mot = self.mot.get_inputs()[0].name
        self.nb_embeddings = int(self.mot.get_inputs()[0].shape[1])
        self.seuil = seuil
        self.reinitialiser()

    def reinitialiser(self) -> None:
        self.audio = np.zeros(0, dtype=np.int16)
        self.mels = np.ones((FENETRE_MEL, 32), dtype=np.float32)
        # Historique initial neutre (le modèle de référence utilise du bruit aléatoire).
        bruit = np.random.default_rng(0).integers(-1000, 1000, 16000 * 4).astype(np.int16)
        self.embeddings = self._embeddings_hors_ligne(bruit)
        self.nb_predictions = 0

    def _melspectrogramme(self, x: np.ndarray) -> np.ndarray:
        sortie = self.mel.run(None, {"input": x.astype(np.float32)[None, :]})[0]
        return np.squeeze(sortie) / 10 + 2  # même transformation que le modèle d'origine

    def _embeddings_hors_ligne(self, x: np.ndarray) -> np.ndarray:
        mels = self._melspectrogramme(x)
        fenetres = [mels[i:i + FENETRE_MEL] for i in range(0, mels.shape[0], 8)
                    if mels[i:i + FENETRE_MEL].shape[0] == FENETRE_MEL]
        lot = np.expand_dims(np.array(fenetres), axis=-1).astype(np.float32)
        return self.emb.run(None, {"input_1": lot})[0].squeeze().astype(np.float32)

    def score(self, bloc: np.ndarray) -> float:
        """Traite exactement 80 ms d'audio (int16) et renvoie la probabilité du mot d'activation."""
        if bloc.dtype != np.int16:
            bloc = (np.clip(bloc, -1, 1) * 32767).astype(np.int16)
        if len(bloc) != BLOC:
            raise ValueError(f"le détecteur attend des blocs de {BLOC} échantillons")
        self.audio = np.concatenate([self.audio, bloc])[-(BLOC + RECOUVREMENT):]
        self.mels = np.vstack([self.mels, self._melspectrogramme(self.audio)])[-10 * 97:]
        fenetre = self.mels[-FENETRE_MEL:].astype(np.float32)[None, :, :, None]
        embedding = self.emb.run(None, {"input_1": fenetre})[0].squeeze().astype(np.float32)
        self.embeddings = np.vstack([self.embeddings, embedding])[-120:]
        entree = self.embeddings[-self.nb_embeddings:][None].astype(np.float32)
        p = float(np.squeeze(self.mot.run(None, {self.entree_mot: entree})[0]))
        self.nb_predictions += 1
        return 0.0 if self.nb_predictions <= 5 else p  # les premières prédictions ne sont pas fiables

    def detecte(self, bloc: np.ndarray) -> bool:
        if self.score(bloc) >= self.seuil:
            self.reinitialiser()  # évite de déclencher plusieurs fois sur le même « Hey Jarvis »
            return True
        return False
