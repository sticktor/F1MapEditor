# Eigene 3D-Modelle aus Blender (Kurzfassung)

Ausführliche englische Anleitung: [own_models_blender.md](own_models_blender.md). Hier das Wichtigste.

**Modell in Blender**
- 1 Blender-Einheit = 1 m, +Z oben (der glTF-Export macht daraus +Y oben).
- Ursprung auf den Boden in die Mitte der Grundfläche. Die Vorderseite zeigt nach Blender −Y (Frontansicht, Ziffernblock 1). Stimmt der Pfeil im Editor nicht, hilft *Vorderseite +90°*.
- Größte Ausdehnung zwischen 5 cm und 500 m (sonst Warnung MO-03). Skalierung und Rotation anwenden (Strg+A), Modifikatoren anwenden.
- Material: Principled BSDF mit Grundfarbe, Farbtextur, Normalenkarte (OpenGL, wie Blender), Metall/Rauheit und Verdeckung (Wert oder Textur).
- Transparenz: Blend-Modus *Alpha Blend* für Glas, *Alpha Clip* für Zäune und Blätter (harte Kanten); Alpha in der Farbtextur (PNG) oder in der Grundfarbe. Ohne Rückseiten-Culling (doppelseitig) werden beide Seiten gezeichnet.
- Kollision: Häkchen *Collision* in den Angaben des eigenen Modells setzt einen unsichtbaren Quader um die Grundfläche, an dem Autos abprallen. Standard aus; die KI kennt ihn nicht, Prüfregel UM-11 warnt, wenn er Fahrbahn oder Boxengasse berührt.
- Texturen werden auf Zweierpotenzen und höchstens 1024 px gebracht (Standard, bis 2048 einstellbar).

**LODs:** Objekte `Name`, `Name_LOD1`, `Name_LOD2` in einer Datei (ohne Lücke) oder eigene Dateien über die Kommandozeile. Wechsel bei 10 % und 25 % der Sichtweite (2500 m).

**Grenzen:** über 50.000 Dreiecke in LOD 0 Warnung (MO-02), über 250.000 Fehler (MO-01), alle eigenen Modelle zusammen über 256 MB Warnung (MO-06).

**Export:** glTF 2.0, *glTF Binary (.glb)*, +Y Up, Apply Modifiers an, Materials: Export, Images: Automatic, keine Kompression (kein Draco), keine Animation, keine Skins.

**Im Editor:** Projekt in einem Ordner speichern, Reiter *Environment* (Umgebung) → *Modell importieren …* → Filter *Eigene Modelle* → platzieren, drehen, skalieren → Prüfen und bauen. *Eigenes Modell entfernen* löscht es wieder aus dem Projekt.

**Rechte:** Für Rechte an importierten Modellen und Texturen bist du selbst verantwortlich. Keine Modelle oder Texturen aus dem Spiel herausnehmen und keine Spieldateien weitergeben.
