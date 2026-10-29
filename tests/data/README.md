# Enregistrements de test

`hey_jarvis.wav` et `alexa.wav` (16 kHz, mono, 2 s) proviennent des tests du projet
[wyoming-openwakeword](https://github.com/rhasspy/wyoming-openwakeword) (Apache 2.0).
Ils servent à vérifier que « Hey Jarvis » déclenche le détecteur et qu'« Alexa » ne le déclenche pas.

Pour tester avec ta propre voix, enregistre un fichier au même format :

```bash
ffmpeg -f avfoundation -i ":0" -t 2 -ar 16000 -ac 1 -sample_fmt s16 ma_voix.wav
```
