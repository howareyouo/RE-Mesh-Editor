# RE Mesh Editor

A Blender addon that reads and writes the mesh, material (mdf2), texture (tex), shell fur (sfur) and
skeleton (fbxskel) files of RE Engine games. This context exists because the same model has to be
made to satisfy both Blender's object model and the game's file formats at once, and the formats
differ per game.

## Language

### Games and versions

**Game profile**:
The complete set of format facts for one game: its mesh, mdf and tex file versions, its vertex
weight layout, and which name it reports.
_Avoid_: game config, game info, game data

**File version**:
The number in a file's extension, as in `name.mesh.230110883`. Mesh, mdf and tex files each carry
their own independent file version, and it names the format that file was written in, not the game.
_Avoid_: version number, format version

**Internal version**:
The version number a mesh file stores in its own header. It identifies the layout, but several
games share one, so it cannot name a game by itself.
_Avoid_: header version

**Remap version**:
The addon's own strictly increasing mesh version number, assigned so format branches can be selected
in a fixed order no matter how irregularly the games shipped.
_Avoid_: mesh version, new version

**Resolved mesh version**:
What a mesh's three version numbers resolve to together — its file version, remap version and
internal version — plus the reason a fallback was used, when one was.
_Avoid_: version info, version tuple

**Unsupported version**:
A version outside the known tables, served by the nearest known remap version instead of being
refused. It imports approximately, not correctly.

**Build variant**:
A game row for a pre-release or alternative build that reports the retail game's name while carrying
its own versions.
_Avoid_: beta game, variant game

**Reported name**:
The retail game name a build variant answers to. Several rows may report the same name.

**MDF alias**:
A game whose mdf file version is shared with another game, so that number alone cannot name it.
_Avoid_: alias game

**Alias winner**:
The one game a shared numeric mdf version resolves to.

**Unconfirmed tex version**:
A game whose tex file version has never been verified. Its textures are located by base name rather
than by version number.

### Mesh files

**Mesh file**:
The RE Engine container for one model: its skeleton, LODs, vertex data and blend shapes.
_Avoid_: model file

**Meshlet (MPLY)**:
The alternative mesh container used by stage and prop models. The same model in a different
container, sharing the vertex and blend shape vocabulary.
_Avoid_: stage mesh, mply file

**LOD group**:
One level of detail inside a mesh file, carrying the distance at which the game switches to it and
the mesh groups drawn at that level.
_Avoid_: LOD, level

**Mesh group**:
One drawable group inside an LOD group, binding a visibility condition to a vertex range and to the
material subdivisions drawn from it.
_Avoid_: group, mesh block

**Material subdivision**:
The smallest drawable unit of a mesh: the faces of one material inside one mesh group.
_Avoid_: submesh, sub mesh, subdivision

**Visibility condition group**:
The identifier a mesh group shares with the game's visibility system, which decides when it is
drawn.

**Content flag**:
The header bitfield saying which kinds of content a mesh file carries — skeleton, blend shapes,
shadow or occlusion meshes.

**Shadow mesh**:
The copy of a mesh the game renders into shadow maps. It follows the model's LODs and cannot have
LODs of its own.

**Occlusion mesh**:
The extra LODs a mesh file carries for occlusion culling, imported only when asked for.

**Streaming mesh**:
The companion mesh file, under a `streaming` folder, that holds the vertex and face buffers of a
large model. A streaming model needs both files to import.

**Skeleton**:
The bone hierarchy stored inside a mesh file: each bone's local transform and its parent, sibling
and child.

**Blend shape**:
A named set of per-vertex offsets that displaces the mesh, imported as a shape key.
_Avoid_: shapekey, shape key, morph target

**Blend target**:
One named shape a blend shape can be displaced to.

**Pragmata blend shape layout**:
The half-precision offset layout one game family ships instead of the usual short or byte layout,
carrying its own prefix and offset interpretation.

### Vertex data

**Vertex element**:
One typed stream of vertex data — position, normal and tangent, UV, weight, colour — which a submesh
declares at an offset inside its vertex buffer.
_Avoid_: vertex attribute, channel

**Typing**:
The integer tag naming which kind of data a vertex element carries.
_Avoid_: element type

**Vertex buffer**:
The byte blob holding every vertex element of a submesh, as laid out by the elements' strides.

**Bone weight**:
The per-vertex influence binding a vertex to a bone.
_Avoid_: skin weight, skinning weight

**Six weight**:
A vertex layout storing six bone weights per vertex instead of four.

**Extra weight**:
A second weight buffer carried alongside the main one, raising how many bones may influence a
vertex.

**Secondary weight**:
The extra weight buffer that scales armour geometry, stored under a typing of its own. Not the same
as an extra weight.
_Avoid_: second weight buffer

### Materials (MDF)

**MDF file (mdf2)**:
The material file that goes with a mesh, holding one material per name.
_Avoid_: material file

**Material**:
One named entry of an MDF, holding its properties, texture bindings and flags. Meshes find it by
name alone.
_Avoid_: shader, material entry

**Material property**:
A named, ordered block of float parameters belonging to a material.
_Avoid_: parameter, prop

**Texture binding**:
The pairing of a texture type with the texture path a material samples. It is the unit every
texture-path edit works on.
_Avoid_: binding, texture entry

**Texture type**:
The name of the slot a texture binding fills — what the shader samples that texture for.
_Avoid_: slot, channel

**Texture path**:
The game-relative path a binding points at, written without the version suffix.

**Material flags**:
The bitfields on a material that the game reads as rendering behaviour.

**Material name**:
The single name a mesh and its MDF material must agree on. Nothing else links the two, so a
mismatch leaves the model invisible or checkered in game.
_Avoid_: mdf name

**Material name sync**:
Keeping mesh object names and MDF material names equal when either side is renamed.
_Avoid_: name sync, rename sync

**Material preset**:
A saved material — properties, bindings and flags — for one game, shareable between users and
reusable on other models.
_Avoid_: preset file

**Shader node tree**:
The Blender node network built from an MDF material so the material renders in Blender.

**MMTRS data**:
A block of index lists attached to a material that only one game family writes.
_Avoid_: MMTRS

**GPU buffer entry**:
A named buffer a material references for the game's GPU-side material data.

### Textures (tex)

**Tex file**:
The RE Engine texture container. One file holds a single image, a mip chain, or a whole array of
images.
_Avoid_: texture file

**Mip level**:
One resolution step in a tex file's chain of progressively smaller copies of an image.

**Streaming tex**:
The companion tex file, under a `streaming` folder, holding the mip levels too small to keep in the
main file.
_Avoid_: streaming texture

**Tex version**:
The number in a `.tex.<N>` extension, specific to the game the texture ships for.

**Texture lookup**:
Finding the tex files on disk behind a texture path by base name, or additionally by the game's tex
version. An empty result means the texture is not on disk, not that it is unknown.
_Avoid_: texture search, texture resolution

**DDS**:
The intermediate image format textures pass through when moving between tex files and a Blender
scene.
_Avoid_: texture cache format

**Texture cache**:
The on-disk store of converted DDS copies, so re-importing a material does not re-convert its
textures.

### Shell fur (SFur)

**SFur file**:
The companion file describing shell fur, with one entry per material that carries fur.
_Avoid_: fur file, shell fur file

**Shell**:
One layer of a fur volume. A material's shell count is how many layers it draws.

**Grooming texture**:
The texture steering fur direction across the surface.

### Blender-side conventions

**RE mesh collection**:
The Blender collection marked as holding one mesh file's exportable objects. A scene may hold
several, which is how several mesh files are exported from one blend.
_Avoid_: mesh collection

**RE MDF collection**:
The Blender collection marked as holding one MDF's material objects.

**RE SFur collection**:
The Blender collection marked as holding one SFur file's shell fur objects.

**RE material object**:
The `Material NN (name)` object carrying one MDF material's settings. It is the unit every
material-side operation works on, and its parenthesised part is the material name.
_Avoid_: material empty, material object

**Mesh object name**:
The `[LOD_N_]Group_<G>_Sub_<S>__<materialName>` name of a mesh object, from which the exporter reads
a submesh's identity and the material it binds to.

**Material name suffix**:
The part of a mesh object name after `__`, which is the material that object binds to.

### Workflow

**Mod workspace**:
A blend file and output folder set up for one mod, carrying the mod directory that exports are
written under.
_Avoid_: workspace

**Mod directory**:
The folder a mod's files are written into, mirroring the game's own folder layout.

**Chunk path**:
The game-relative folder a file lives at, reproduced inside the mod directory so the game finds the
file.

**Porting**:
Turning a model built outside RE Engine into RE format by baking a game's material presets onto it.
_Avoid_: conversion, convert to RE Engine

**Preset analysis**:
Scoring a Blender material against one game's presets to choose the one to bake it to.

**Texture packer**:
The workflow combining a model's source images into the channel layout one RE texture expects,
driven by the target material preset.