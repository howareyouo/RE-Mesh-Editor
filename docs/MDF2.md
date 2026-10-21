# MDF2 string table serialization (write_duplicates)

The mdf2 writer deliberately keeps **duplicate strings** in the string table. This is the vanilla Capcom layout, not an oversight.

## How it works

`modules/mdf/file_re_mdf.py` (`MDFFile.recalculateHashesAndOffsets`) builds the string table with:

```python
stringTable = StringTableBuilder(write_duplicates=True)
```

- `add_shared_alloc` — every string occurrence advances the table cursor and is serialized, even if an identical string was already written. Entries (mmtr paths, texture type names, texture paths, property names, GPBF names) all reference the offset of the **first** occurrence.
- `add_always` — material names always get a fresh slot, even if two materials share a name.

The builder lives in `modules/gen_functions.py` (`StringTableBuilder`); its docstring documents the mechanics and notes that `sfur` uses deduplicated output (`write_duplicates=False`) while `mdf`/`fbxskel` write duplicates.

## Why duplicates are kept

Byte-identical round trips: vanilla files serialize every occurrence and let later entries point at the first one, so a faithful writer must do the same. V0.60's changelog states the goal: *"Reworked MDF file serialization to produce files that are 100% identical to the original files."* The `tests/test_blender.py` round-trip tests (import → export → re-import) guard MDF data integrity.

Do not switch to deduplicated output casually. If the writer is ever changed, re-run the MDF round-trip tests and verify a vanilla import → export produces a byte-identical file.

## Size implication (expected user questions)

Because property names and texture type names repeat for **every material**, the duplicated table grows linearly with material count and often dominates file size. Tools that deduplicate the string table on save — e.g. REE-Content-Editor via REE-Lib's `StringTable.Add` (`StringMap`) — produce much smaller mdf2 files for identical content. Both files are valid; the game reads strings by offset, so dedup changes nothing in-game.

Other minor differences vs REE-Content-Editor output:

| Item | This addon | REE-Content-Editor |
|---|---|---|
| String table | duplicates kept (vanilla layout) | deduplicated |
| Per-material property block | raw size, no alignment | 16-byte aligned (`Utils.Align16`) |
| Texture entry tail 8 bytes (v13+) | written as zeros | written as zeros |
| Materials written | all | all |

If deduplicated output is ever wanted: `write_duplicates=False` in `file_re_mdf.py` and share material-name slots (`add_always` → shared offset). The reader is layout-agnostic and needs no changes.
