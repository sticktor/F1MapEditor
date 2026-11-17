# Track surface and tyre marks

The build dialog can replace the carrier asphalt and the dark tyre-mark strip with a circuit from
`ego-werkzeuge/strecke/daten/fahrbahn.json`. At build time the textures are copied from the F1 25
game folder (lossless, same path as the desert biome). Default is the Portimão carrier (unchanged).

Circuits whose textures are identical (same MD5 in the game data) form one entry, e.g. “Baku / Brazil (+10)”.
Bahrain, Imola, Madrid, Melbourne and Suzuka use another shader and supply only the base textures (untested in the
game); they have no tyre-mark strip of their own.

Rebuild the catalog after a game update (run in `ego-werkzeuge`). The file is exactly the output of this command;
missing previews (`strecke/daten/vorschau/`) are created from the game textures, `--ohne-vorschau` skips them:

```text
python -m strecke.asphalt_vorlagen analysieren <game folder>
```

Both pictures below are the same straight. The first uses the Abu Dhabi surface and Abu Dhabi tyre
marks, the second the Portimão carrier.

![Abu Dhabi surface, Abu Dhabi tyre marks](abu-dhabi.jpg)

![Portimão surface, Portimão tyre marks](portimao.jpg)
