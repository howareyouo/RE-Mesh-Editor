#Author: NSA Cloud
#Blender refuses to register a class whose CollectionProperty or PointerProperty annotation
#names a type that isn't registered yet, so the per-module `classes` tuples can't simply be
#concatenated: an operator like RE_MESH_OT_batch_exporter lists an ExporterNodePropertyGroup,
#and that property group has to go first. orderClasses reads the annotations and puts every
#referenced type before its user, which keeps the modules free to declare their classes in
#whatever order reads best.

def _referencedTypes(classEntry):
	"""The classes `classEntry` needs registered before it."""
	referenced = []
	for annotation in getattr(classEntry, "__annotations__", {}).values():
		keywords = getattr(annotation, "keywords", None)
		reference = keywords.get("type") if isinstance(keywords, dict) else None
		if isinstance(reference, type):
			referenced.append(reference)
	return referenced


def orderClasses(classes):
	"""`classes` ordered so every annotated property type comes before its user.

	Classes with no dependency between them keep their input order. A set of classes
	that reference each other can't be registered in any order, so those are left in
	input order for Blender to report.
	"""
	unique = list(dict.fromkeys(classes))
	dependencies = {classEntry: [referenced for referenced in _referencedTypes(classEntry)
	                             if referenced in unique]
	                for classEntry in unique}
	ordered = []
	placed = set()
	while len(ordered) < len(unique):
		ready = [classEntry for classEntry in unique
		         if classEntry not in placed
		         and all(referenced in placed for referenced in dependencies[classEntry])]
		if not ready:
			ordered.extend(classEntry for classEntry in unique if classEntry not in placed)
			break
		ordered.extend(ready)
		placed.update(ready)
	return ordered
