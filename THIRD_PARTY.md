# Composants tiers et licences

| Composant | Rôle | Licence | Source |
|---|---|---|---|
| Ollama | moteur d'exécution des modèles | MIT | github.com/ollama/ollama |
| Qwen3.5 9B | cerveau | Apache 2.0 | ollama.com/library/qwen3.5 |
| Qwen3-Embedding 0.6B | mémoire et documents | Apache 2.0 | ollama.com/library/qwen3-embedding |
| Phonon 40M français (Gradium) | voix | **CC BY-NC 4.0** (usage non commercial) | huggingface.co/gradium/phonon-40m-fr |
| Piper (Open Home Foundation) | voix sur processeur (`piper-tts`) | GPL-3.0 | github.com/OHF-Voice/piper1-gpl |
| Voix Piper « siwis » (fr_FR, féminine) | voix sous Linux | CC BY 4.0 (corpus SIWIS, université d'Édimbourg) | huggingface.co/rhasspy/piper-voices |
| Whisper large-v3 turbo (OpenAI) | transcription | MIT | huggingface.co/mlx-community/whisper-large-v3-turbo |
| mlx-whisper (Apple) | exécution de Whisper avec MLX | MIT | github.com/ml-explore/mlx-examples |
| openWakeWord (David Scripka) | mélspectrogramme et embeddings audio (`melspectrogram.onnx`, `embedding_model.onnx`) | Apache 2.0 | github.com/dscripka/openWakeWord |
| Modèle « hey jarvis » d'openWakeWord | mot d'activation (`hey_jarvis_v0.1.onnx`) | **CC BY-NC-SA 4.0** (usage non commercial) | github.com/dscripka/openWakeWord |
| Silero VAD v6 | détection de parole (`silero_vad.onnx`) | MIT | github.com/snakers4/silero-vad |
| Enregistrements de test `tests/data/*.wav` | tests du mot d'activation | Apache 2.0 | github.com/rhasspy/wyoming-openwakeword |

Les modèles ONNX de `jarvis/assets/modeles/` sont redistribués tels quels, avec cette attribution. Les licences « NC » autorisent un usage personnel, éducatif ou de recherche, mais pas un usage commercial : un Jarvis vendu ou intégré à un produit payant devrait changer de voix (par exemple Phonon 31M anglais, sous Apache 2.0) et de mot d'activation.
