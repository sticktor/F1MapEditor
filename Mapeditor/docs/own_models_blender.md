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

Not used: skinning, armatures, shape keys, cameras, lights, vertex colours, a second UV map. Moving parts (object animation) are possible, see [6. Animation](#6-animation-moving-parts).

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
| Animation | **off** for static models. For moving parts (section 6): **Animation on**, *Always Sample Animations* on, Shape Keys and Skinning off |

Save the file anywhere; the editor copies it into the project.

## 5. Use it in Mapeditor

1. Save your project in a folder (own models are stored in its subfolder `modelle`).
2. Open the **Environment** tab and press **Import model …**. Choose the `.glb` (or `.gltf`, `.obj`).
3. The model appears in the catalogue under the filter **Own models** with a preview, size, triangle count and notes. The
   notes tell you what the conversion changes or leaves out (for example a missing normal map or a reduced texture).
4. Place it like any game object: pick it, click in the map, then move, rotate (round handle, R, Ctrl+wheel) and scale it.
   Check the front arrow and use *Face the track* (T) if the model should look at the track.
5. Run **Check and build**. The checks MO-01 … MO-08 report problems with own models (unusable file, many triangles,
   implausible size, origin beside the model, notes on the conversion, too much memory, model not placed, moving model
   with a collision box). Check
   UM-11 warns if a collision box touches the road or the pit lane. The build
   creates the game resources of the model and puts them in the package, which then contains everything of your model.

**Collision:** tick *Collision* in the details of an own model to make the build put an invisible box around its
footprint (rotated and scaled like each placed object). Cars bounce off it like off a wall. It is off by default, the
AI does not know the box, and check UM-11 warns if a box touches the road or the pit lane. Keep collision objects
beside the track (grass, gravel), not on it.

**Animation:** if the file contains an animation (section 6), the details show *Animation name: length, moving parts*
and the box *Play animation* (on by default). Off, the model stands still in its rest pose.

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
`kollision` (`keine`, or `quader` for a box collision), `animation` (`an`, or `aus` for the rest pose). A placed object in `umgebung.json` can override `kollision`. Edit them
with the editor closed, or use the command line tool. Do not rename the files by hand.

## 6. Animation (moving parts)

A model can move in the game: a wind turbine, a ferris wheel, a flag pole that turns, a floating stone that bobs. The
game moves **whole parts** (objects); it does not bend meshes. So animate **objects**, not bones or shape keys.

| Topic | Rule |
|---|---|
| What moves | Location, rotation and scale of **objects**. Parent the parts that move together to one object (an *Empty* works well) and animate that one. Children follow their parent |
| What stays | Objects without animation and without an animated parent stand still (mast, base) |
| Pivot | The pivot is the **origin of the animated object**. Put the origin of a rotor on its hub before you animate it |
| Loop | The game repeats the animation endlessly. Make the **last frame equal the first** (for a rotor: 0° → 360°) so the loop has no jump |
| Interpolation | Linear, constant and Bézier keys all work (the export samples them). For an even rotation set the keys to **Linear** |
| Length | Up to 18 minutes; a few seconds to a minute is typical. Times are rounded to 1/60 s |
| One animation | Only the first animation (action) in the file is used; a note tells you if there are more |
| Collision | The collision box (*Collision*) does not move. It stays around the rest pose, check MO-08 warns. Give collision only to a fixed base |
| LOD | LOD objects of a moving part must be children of the same animated object (`Rotor`, `Blade_LOD1` as child of `Rotor`) |
| Not possible | Armatures/skinning, shape keys (morph targets), animated materials or textures, sound, starting the movement on an event |

Export as in section 4 with **Animation on** and *Always Sample Animations* on. Example: a wind turbine with a fixed mast
and nacelle, an Empty `Rotor` at the hub with three blades as children, keys on the rotation of `Rotor` at frame 1 (0°)
and frame 97 (360°), linear, 24 fps = 4 s per turn.

In the editor the details of the model show the animation and the box *Play animation*. The build places the model as an
animated object; the bounding box covers the whole movement, so moving parts do not disappear at the edge of the screen.

## 7. Moving materials (water, scrolling light, flag)

Some surfaces move by themselves in the game, without animation: water ripples, an LED band runs, a flag waves. Mark
the material in Blender with a **custom property** (Material Properties → Custom Properties → *New*, type *String* or
*Float*) and export with *Include → Custom Properties* on:

| Property | Value | Effect |
|---|---|---|
| `f1_effect` | `water` | Water with two moving ripple layers (copy of the carrier's lake material). Base colour texture and normal map of the material are used if present, otherwise the carrier's water textures. For flat surfaces (ponds, pools) |
| `f1_effect` | `scroll` | Glowing texture that runs over the surface (LED band, ticker, light strip). The base colour texture is the light pattern |
| `f1_effect` | `flag` | Waving cloth with a running texture (like the flags of the game). Base colour texture with alpha; subdivide the cloth (e.g. 16 × 8) so it can wave; make it double-sided |
| `f1_speed_u`, `f1_speed_v` | number | Running speed along U and V (scroll: default 0.1 / 0; flag: 0.3; larger is faster, negative runs backwards) |
| `f1_brightness` | number | Glow of `scroll` (default 250). The game's scrolling lights: 250 (Singapore building), 750 (Las Vegas colour cycle), 40,000 (Las Vegas neon), all on night tracks. In daylight 400 looked matt in a test; use 750 or more for a visible glow |
| `f1_speed` | number | Factor for the speed of `water` (default 1) |

Tips from the game tests:

- **Flag with a motif** (logo, coat of arms, lettering): set `f1_speed_u` to `0`. With the default 0.3 the texture runs
  sideways over the cloth like on the game's flags; stripes look fine, a motif wanders off. The waving stays.
- **Water**: seen from the car (about 1 m above the road) a surface at ground level is almost invisible. Raise it, e.g. as
  a basin on a plinth, and keep it in front of barriers and walls.

The editor shows *Moving materials* in the details of the model. In `modelle.json` the field `effekte` sets or overrides
the effect per material (`{"Water": "wasser", "LedBand": {"art": "lauftext", "u": 0.2, "helligkeit": 400}}`), or on the
command line `strecke_tool.py modell aendern PROJECT NAME --effekt LedBand=lauftext:u=0.2,helligkeit=400`.

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
| Moving parts (object animation: move, rotate, scale) | yes, one animation per model, repeats endlessly |
| Moving materials: water, scrolling light, waving flag | yes (custom property `f1_effect`, section 7) |
| Skinning (armatures), shape keys, vertex colours, second UV | no |

## Rights

You are responsible for the rights to the models and textures you import and publish. Use only your own work or material
you are allowed to use and share. Do not take models or textures out of the game and do not share game files with a
track you publish. Mapeditor and this guide contain no game data. Packages made from original game tracks are for your own
use only. The editor is an unofficial fan project and not affiliated with EA, Codemasters, Formula One Group or the FIA.
