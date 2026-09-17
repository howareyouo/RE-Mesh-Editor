"""Metadata-driven binary struct read/write with automatic field batching.

Declare a file-format layout once via _fields_; read()/write() are generated
per version so consecutive fixed-size scalar fields merge into a single
struct batch (one file.read per run of scalars - no per-field reads).

Supported field kinds (see module-level factories):
  u8/i8/u16/i16/u32/i32/u64/i64/f32          single scalar
  f32x3/f32x4                                fixed-size vector batch (e.g. 'fff')
  arr(fmt, count)                            fixed/derived-length array
  nested(cls)                                sub-struct that itself has read/write
  skip(n)                                    pad bytes (seek, no storage)

Field options:
  cond=callable(version)->bool   version-gated field (False omits it)
  count=int | 'attr_name' | callable(self, version)->int

Subclasses keep __init__ to set defaults and may implement:
  post_read(file, version)   after field layout (e.g. resolve string offsets)
  pre_write(file, version)   before field layout (e.g. recompute offsets)

NOTE: mesh hot-path readers keep their hand-tuned numpy/struct batches; this
module targets the fbxskel/sfur/mdf leaf structs.
"""

import struct


_struct_cache = {}


def _struct(fmt):
	s = _struct_cache.get(fmt)
	if s is None:
		s = struct.Struct(fmt)
		_struct_cache[fmt] = s
	return s


class Field:
	__slots__ = ('fmt', 'name', 'cond', 'count', 'nested', 'pad', 'width')

	def __init__(self, fmt=None, name=None, cond=None, count=None, nested=None, pad=None, width=1):
		self.fmt = fmt
		self.name = name
		self.cond = cond
		self.count = count
		self.nested = nested
		self.pad = pad
		self.width = width


def u8(name, cond=None): return Field('B', name, cond)
def i8(name, cond=None): return Field('b', name, cond)
def u16(name, cond=None): return Field('H', name, cond)
def i16(name, cond=None): return Field('h', name, cond)
def u32(name, cond=None): return Field('I', name, cond)
def i32(name, cond=None): return Field('i', name, cond)
def u64(name, cond=None): return Field('Q', name, cond)
def i64(name, cond=None): return Field('q', name, cond)
def f32(name, cond=None): return Field('f', name, cond)
def f32x3(name, cond=None): return Field('fff', name, cond, width=3)
def f32x4(name, cond=None): return Field('ffff', name, cond, width=4)


def arr(name, fmt, count, cond=None):
	return Field(fmt, name, cond, count=count)


def nested(name, cls, cond=None):
	return Field(nested=cls, name=name, cond=cond)


def skip(size, cond=None):
	return Field(pad=size, cond=cond)


def _resolve_count(count, self, version):
	if isinstance(count, int):
		return count
	if isinstance(count, str):
		return getattr(self, count)
	return count(self, version)


def _build_plan(fields, version):
	"""Expand version-gated fields into a plan of batched ops."""
	plan = []
	batch = []  # (fmt, name, width) pairs

	def flush():
		if batch:
			plan.append(('batch', ''.join(f for f, _, _ in batch), [(n, w) for _, n, w in batch]))
			batch.clear()

	for f in fields:
		if f.cond is not None and not f.cond(version):
			continue
		if f.pad is not None:
			flush()
			plan.append(('skip', f.pad))
		elif f.nested is not None:
			flush()
			plan.append(('nested', f.nested, f.name))
		elif f.count is not None:
			flush()
			plan.append(('array', f.fmt, f.count, f.name))
		else:
			batch.append((f.fmt, f.name, f.width))
	flush()
	return plan


_plan_cache = {}


def _get_plan(cls, fields, version):
	key = (cls, version)
	plan = _plan_cache.get(key)
	if plan is None:
		plan = _build_plan(fields, version)
		_plan_cache[key] = plan
	return plan


class BinaryStruct:
	_fields_ = []

	def read(self, file, version=None):
		plan = _get_plan(type(self), self._fields_, version)
		for item in plan:
			kind = item[0]
			if kind == 'batch':
				_, fmt, targets = item
				st = _struct(fmt)
				vals = st.unpack(file.read(st.size))
				idx = 0
				for name, width in targets:
					if width > 1:
						setattr(self, name, vals[idx:idx + width])
						idx += width
					else:
						setattr(self, name, vals[idx])
						idx += 1
			elif kind == 'skip':
				file.seek(item[1], 1)
			elif kind == 'nested':
				obj = item[1]()
				obj.read(file, version)
				setattr(self, item[2], obj)
			else:  # array
				_, fmt, count_desc, name = item
				count = _resolve_count(count_desc, self, version)
				st = _struct(fmt * count)
				setattr(self, name, list(st.unpack(file.read(st.size))))
		self.post_read(file, version)

	def write(self, file, version=None):
		self.pre_write(file, version)
		plan = _get_plan(type(self), self._fields_, version)
		for item in plan:
			kind = item[0]
			if kind == 'batch':
				_, fmt, targets = item
				st = _struct(fmt)
				values = []
				for name, width in targets:
					attr = getattr(self, name)
					if width > 1:
						values.extend(attr)
					else:
						values.append(attr)
				file.write(st.pack(*values))
			elif kind == 'skip':
				file.seek(item[1], 1)
			elif kind == 'nested':
				getattr(self, item[2]).write(file, version)
			else:  # array
				_, fmt, count_desc, name = item
				count = _resolve_count(count_desc, self, version)
				st = _struct(fmt * count)
				file.write(st.pack(*getattr(self, name)))

	def post_read(self, file, version):
		pass

	def pre_write(self, file, version):
		pass