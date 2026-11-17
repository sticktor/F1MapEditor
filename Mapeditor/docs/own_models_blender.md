# Own 3D models from Blender

Mapeditor can place your own 3D models (a sign, a tent, a statue, a stand …) as scenery next to your track. You make the
model in Blender, export it as glTF, import it in the editor, place it like any other environment object and build.
Everything the model needs (geometry, materials, textures) is created by the build and stored in your track package, you
do not need any game files for it.

German short version: [own_models_blender.de.md](own_models_blender.de.md)

> **Status:** models with base colour, colour texture, normal map, metal/roughness, transparency (glass), cut-outs
> (fences, leaves), double-sided faces, levels of detail and an optional box collision work and are tested in the game,
> see [What is converted and what is not](#what-is-converted-and-what-is-not).

## 1. Prepare the model in Blender

| Topic | Rule |
|---|---|
| Unit and scale | **1 Blender unit = 1 metre.** Model at real size (a person is about 1.8 m, a grandstand step about 0.4 m). You can still scale each placed object in the editor |
| Up | **+Z is up** in Blender (the glTF exporter turns it into +Y up) |
| Origin | Put the object origin on the **ground at the centre of the footprint**. The editor rotates and places the model around its origin. A model whose lowest point is above the origin is lowered by the build (ground mode *Automatic*) and a hint tells you. A warning appears if the origin is far beside the model |
| Front | The **front faces Blender −Y** (the side you see in Blender's front view, Numpad 1). That is the side that turns towards the track when you use *Face the track* (T). If the arrow in the editor points the wrong way, use *Front +90°* for all objects of that model |
| Size | Largest extent between **5 cm and 500 m**; otherwise a check (MO-03) warns, because it is almost always a unit mistake |
| Geometry | Apply scale and rotation (Ctrl+A → All Transforms) and modifiers before export. Triangles are made on export. Remove loose, hidden and degenerate parts |
| UV | One UV map (the first one is used). Textures can tile (UV outside 0 … 1) |
| Normals | Smooth shading is kept. Missing normals are calculated from the faces (a note tells you) |

Not used: animation, skinning, armatures, shape keys, cameras, lights, vertex colours, a second UV map.

## 2. Materials and textures

Use the **Principled BSDF** with image textures; the glTF exporter understands exactly this setup.

| Blender | Used in the game |
|---|---|
| Base Color (colour) | tint of the material |
| Base Color (image texture) | colour texture |
| Normal Map (OpenGL / Blender convention, tangent space) | normal map |
| Metallic, Roughness (value or texture) | gloss and metal of the surface (glTF metallic/roughness texture: B metal, G roughness) |
| Ambient Occlusion texture (glTF occlusion) | darkens the surface (crevices, bottom edges) |
| Alpha with Blend mode *Alpha Blend* | transparent material (glass, plastic sheets); opacity = alpha of the colour texture × alpha of Base Color |
| Alpha with Blend mode *Alpha Clip* | cut-out material with hard edges (fences, leaves, grids); the clip threshold is kept |
| Backface culling off (glTF *double sided*) | both sides are drawn; the build adds the back faces, so these faces count twice |
| Emission, other maps | not used |

Textures:

- Use PNG or JPEG. The build makes them a power of two and **at most 1024 px on the longer side** (project option
  `textur_max`, up to 2048). Bigger images are halved, so 4K textures bring no gain.
- Prefer one material with one atlas texture over many materials. Equal images in several materials are stored once.
- Normal maps in the Blender/OpenGL convention (green up) are right. If you baked DirectX normal maps, set the model's
  `normalen` to `dx` in `modelle/modelle.json` (see [Project files](#project-files)).
- All textures of own models are kept in memory all the time. Check MO-06 warns above 256 MB for all own models together.

- For transparency, put the alpha in the colour texture (PNG with alpha) or in the alpha of Base Color. Several
  transparent faces behind each other are sorted by the game; avoid large overlapping glass areas.
- Transparency needs a matching material in the carrier package. If it has none, a note tells you and the model is
  drawn opaque. If this page and the editor disagree, the notes the editor shows under the preview of the model are right.

## 3. Level of detail (LOD) and limits

Objects far from the camera need fewer triangles. Two ways, you can use either:

1. **Objects in one file:** name the objects `Sign`, `Sign_LOD1`, `Sign_LOD2` (suffix `_LOD1` … `_LOD9`; an object without
   suffix is LOD 0, the most detailed). The numbers must not skip a level.
2. **Separate files:** export each level as its own file (`sign.glb`, `sign_lod1.glb`, …) and add them with the command
   line tool (`strecke_tool.py modell hinzufuegen … --lod …`); *Import model …* in the editor takes one file.

By default the model switches to LOD 1 at 10 % and to LOD 2 at 25 % of the view distance (2,500 m) and disappears at the
view distance. Each lower level should have clearly fewer triangles (about half or fewer).

| Limit | Value | Reaction |
|---|---|---|
| Triangles in LOD 0 | up to 50,000 comfortable | MO-02 warns above 50,000 |
| Triangles in LOD 0 | **250,000 at most** | MO-01 error above this, the model is not used |
| Largest extent | 0.05 … 500 m | MO-03 warns outside |
| Texture edge | 64 … 2,048 px, default 1,024 | larger images are reduced |
| All own models together | 256 MB | MO-06 warns above |
| File types | `.glb` (best), `.gltf` without side files, `.obj` | compressed glTF (Draco, meshopt) is not read |

Many instances of a heavy model cost frame rate. Use a low-poly version for objects that are placed in hundreds
(trees, signs, fence parts).

## 4. Export from Blender

*File → Export → glTF 2.0 (.glb/.gltf)* with these settings:

| Panel | Setting |
|---|---|
| Format | **glTF Binary (.glb)** |
| Include | Selected Objects if you only want the model; cameras and punctual lights off |
| Transform | **+Y Up** on |
| Geometry | **Apply Modifiers** on; UVs on; Normals on; Tangents off; Vertex Colour off; Materials: **Export** |
| Geometry → Compression | **Off** (no Draco) |
| Geometry → Images | **Automatic** (PNG/JPEG) |
| Animation | everything off (Animation, Shape Keys, Skinning) |

Save the file anywhere; the editor copies it into the project.

## 5. Use it in Mapeditor

1. Save your project in a folder (own models are stored in its subfolder `modelle`).
2. Open the **Environment** tab and press **Import model …**. Choose the `.glb` (or `.gltf`, `.obj`).
3. The model appears in the catalogue under the filter **Own models** with a preview, size, triangle count and notes. The
   notes tell you what the conversion changes or leaves out (for example a missing normal map or a reduced texture).
4. Place it like any game object: pick it, click in the map, then move, rotate (round handle, R, Ctrl+wheel) and scale it.
   Check the front arrow and use *Face the track* (T) if the model should look at the track.
5. Run **Check and build**. The checks MO-01 … MO-07 report problems with own models (unusable file, many triangles,
   implausible size, origin beside the model, notes on the conversion, too much memory, model not placed). Check
   UM-11 warns if a collision box touches the road or the pit lane. The build
   creates the game resources of the model and puts them in the package, which then contains everything of your model.

**Collision:** tick *Collision* in the details of an own model to make the build put an invisible box around its
footprint (rotated and scaled like each placed object). Cars bounce off it like off a wall. It is off by default, the
AI does not know the box, and check UM-11 warns if a box touches the road or the pit lane. Keep collision objects
beside the track (grass, gravel), not on it.

*Remove own model* deletes the model and its files from the project; objects already placed stay and are reported as
unknown model until you delete them. If you change the `.glb` in Blender, import it again with the same name to replace it.

The converted result is cached in `%LOCALAPPDATA%\ego-werkzeuge\modelle`, so rebuilding is fast. You can delete the cache
at any time.

### Project files

```
<project>/modelle/modelle.json     table of own models
<project>/modelle/<name>.glb       LOD 0, further LODs as <name>_lod1.glb …
```

Per model, `modelle.json` has these optional fields (defaults in brackets): `kategorie` (`sonstiges`, the catalogue
category), `vorne_grad` (270, the front), `sichtweite_m` (2500, view distance), `lod_abstaende` (automatic, own switching
distances), `textur_max` (1024), `textur_format` (`bc7`, or `bc1` for half the size), `normalen` (`gl`, or `dx`),
`kollision` (`keine`, or `quader` for a box collision). A placed object in `umgebung.json` can override `kollision`. Edit them
with the editor closed, or use the command line tool. Do not rename the files by hand.

## What is converted and what is not

| Feature | Now |
|---|---|
| Opaque triangles, UV, normals | yes |
| Several materials, base colour, colour texture, normal map | yes |
| LOD levels | yes |
| Textures larger than the limit | reduced |
| Transparency (glass), cut-outs (fences, leaves) | yes |
| Double-sided faces | yes (back faces are added) |
| Metal, roughness, ambient occlusion | yes (values or texture) |
| Collision | optional box around the footprint (tick *Collision* in the details of the model); off by default |
| Exact collision shape, AI avoiding the model | no (the box is a simple wall; the AI does not know it, keep it off the racing line) |
| Animation, skinning, vertex colours, second UV | no |

## Rights

You are responsible for the rights to the models and textures you import and publish. Use only your own work or material
you are allowed to use and share. Do not take models or textures out of the game and do not share game files with a
track you publish. Mapeditor and this guide contain no game data. Packages made from original game tracks are for your own
use only. The editor is an unofficial fan project and not affiliated with EA, Codemasters, Formula One Group or the FIA.
