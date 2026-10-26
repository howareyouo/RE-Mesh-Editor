#Author: NSA Cloud
#V8
import os
import struct
import glob
import time
from pathlib import Path
import platform
import unicodedata
import re
import subprocess

# Pre-compiled struct objects (avoids repeated format string parsing)
_struct_ubyte = struct.Struct('<B')
_struct_byte = struct.Struct('<b')
_struct_short = struct.Struct('<h')
_struct_ushort = struct.Struct('<H')
_struct_int = struct.Struct('<i')
_struct_uint = struct.Struct('<I')
_struct_uint64 = struct.Struct('<Q')
_struct_float = struct.Struct('<f')


class ClampNameList(list):
	"""代理 list: 读取时自动把越界索引钳制到最后一个有效项,
	用于兼容被故意填坏名称表的 mesh, 让下游解析不越界崩溃."""

	def __getitem__(self, index):
		# 切片按原样返回(仍是 ClampNameList)
		if isinstance(index, slice):
			return ClampNameList(list.__getitem__(self, index))
		if not isinstance(index, int):
			return list.__getitem__(self, index)
		if self:
			if index >= len(self):
				index = len(self) - 1
			elif index < -len(self):
				index = 0
		return list.__getitem__(self, index)


#---General Functions---#
#Note: ANSI colors work in modern Windows terminals without the legacy
#`color` command; the old os.system("color") forked a shell on every import.
class textColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def read_ubyte(file_object):
	return _struct_ubyte.unpack(file_object.read(1))[0]
def read_byte(file_object):
	return _struct_byte.unpack(file_object.read(1))[0]
def read_short(file_object):
	return _struct_short.unpack(file_object.read(2))[0]
def read_ushort(file_object):
	return _struct_ushort.unpack(file_object.read(2))[0]
def read_uint(file_object):
	return _struct_uint.unpack(file_object.read(4))[0]
def read_int(file_object):
	return _struct_int.unpack(file_object.read(4))[0]
def read_uint64(file_object):
	return _struct_uint64.unpack(file_object.read(8))[0]
def read_float(file_object):
	return _struct_float.unpack(file_object.read(4))[0]
def readUTF16String(data, offset, label="string"):
	"""Read a null-terminated UTF-16LE string from bytes `data` at `offset`.

	A terminator only counts when it starts on a 2-byte-aligned offset; a
	\x00\x00 found at an odd offset would split a code unit, so the scan keeps
	looking past it. Returns (string, end_offset) where end_offset points just
	past the terminator. Raises ValueError ("{label} has no string terminator")
	when no aligned terminator exists; malformed UTF-16 raises
	UnicodeDecodeError, a ValueError subclass callers can also catch."""
	endOffset = data.find(b"\x00\x00", offset)
	while endOffset != -1 and (endOffset - offset) % 2:
		endOffset = data.find(b"\x00\x00", endOffset + 1)
	if endOffset == -1:
		raise ValueError(f"{label} has no string terminator")
	return data[offset:endOffset].decode("utf-16le"), endOffset + 2

def read_unicode_string(file_object):#Reads unicode string from file into utf-8 string
	#Delegate the scan+decode to readUTF16String; leave the cursor just past
	#the terminator (callers all seek() explicitly, but keep it tidy). Strings
	#that run to EOF without a terminator decode to EOF instead of raising.
	start = file_object.tell()
	data = file_object.read()
	try:
		value, end_offset = readUTF16String(data, 0, "string")
	except ValueError:
		return data.decode("utf-16le")
	file_object.seek(start + end_offset)
	return value
def write_ubyte(file_object, input):
	file_object.write(_struct_ubyte.pack(input))
def write_byte(file_object, input):
	file_object.write(_struct_byte.pack(input))
def write_short(file_object, input):
	file_object.write(_struct_short.pack(input))
def write_ushort(file_object, input):
	file_object.write(_struct_ushort.pack(input))
def write_uint(file_object, input):
	file_object.write(_struct_uint.pack(input))
def write_int(file_object, input):
	file_object.write(_struct_int.pack(input))
def write_uint64(file_object, input):
	file_object.write(_struct_uint64.pack(input))
def write_float(file_object, input):
	file_object.write(_struct_float.pack(input))
def write_unicode_string(file_object,input):#Writes utf-8 string as utf-16
     file_object.write(input.encode('UTF-16LE') + b'\x00\x00')


def read_null_terminated_names(file, name_offsets, next_section_offsets=()):
    """Read a null-terminated ASCII name table addressed by absolute offsets.

    Loads the whole string region in one shot and splits by null terminators
    (avoids per-string seek + byte-by-byte reads). Shared by the RE and MPLY
    mesh parsers, which previously carried two drifted copies of this logic.

    next_section_offsets: absolute offsets of sections that may follow the
    string table; the smallest one above the table start bounds the read so a
    mid-file table doesn't slurp the rest of the file. Empty/unset -> read to
    EOF (the table is the last section in that layout).
    """
    if not name_offsets:
        return []
    first_offset = name_offsets[0]
    file.seek(first_offset)
    bounds = [o for o in next_section_offsets if o and o > first_offset]
    if bounds:
        region = file.read(min(bounds) - first_offset)
    else:
        region = file.read()
    names = []
    for name_offset in name_offsets:
        rel_offset = name_offset - first_offset
        if rel_offset < 0 or rel_offset >= len(region):
            continue  #Skip bad offsets, keep reading the remaining names
        end = region.find(b'\x00', rel_offset)
        if end == -1:
            end = len(region)
        names.append(region[rel_offset:end].decode('utf-8'))
    return names


class StringTableBuilder:
	"""Capcom-style UTF-16 string table builder with shared string offsets.

	Offsets returned by add()/add_always() are relative to the table start;
	callers add the table's absolute file offset when storing entry offsets.

	Serialization is deferred to write() so offsets can be gathered in any
	pass order. write_duplicates controls what write() emits:
	  False -> each unique string once, in first-seen order (sfur,
	           previously collectStringOffsets(...).keys())
	  True  -> every added string, duplicates included, in add order (mdf,
	           fbxskel - matches vanilla file layout)

	  add()         shared offset: first use allocates, later repeats reuse it.
	  add_always()  fresh offset on every call (mdf material names).
	"""

	def __init__(self, write_duplicates=False):
		self._offsets = {}
		self._serialized = []
		self._seen = set()
		self._cursor = 0
		self._write_duplicates = write_duplicates

	def add(self, string):
		offset = self._offsets.get(string)
		if offset is None:
			offset = self._cursor
			self._offsets[string] = offset
			self._cursor += len(string) * 2 + 2
		if self._write_duplicates or string not in self._seen:
			self._serialized.append(string)
			self._seen.add(string)
		return offset

	def add_always(self, string):
		offset = self._cursor
		self._cursor += len(string) * 2 + 2
		if self._write_duplicates or string not in self._seen:
			self._serialized.append(string)
			self._seen.add(string)
		return offset

	def add_shared_alloc(self, string):
		"""Shared offset (first occurrence wins) but every call advances the
		cursor and serializes - the vanilla mdf layout where duplicate strings
		still occupy table space while entries reference the first occurrence
		(previously four hand-rolled dicts + getStringTableOffset)."""
		offset = self._offsets.get(string)
		if offset is None:
			offset = self._cursor
			self._offsets[string] = offset
		self._serialized.append(string)
		self._cursor += len(string) * 2 + 2
		return offset

	def offset_of(self, string):
		return self._offsets[string]

	@property
	def size(self):
		return self._cursor

	def write(self, file):
		for string in self._serialized:
			write_unicode_string(file, string)


def getPaddingAmount(currentPos,alignment):
    return (currentPos*-1)%alignment

# Batch read helpers: read N values from file in one call.
# NOTE: must unpack EXACTLY `count` values. The single-value pre-compiled
# structs (_struct_ushort etc.) cannot be used here - `unpack_from` on a whole
# buffer with a single-element format returns only the FIRST value, silently
# truncating arrays (previously caused bone weights to be dropped on import).
def read_ubyte_array(file_object, count):
    return list(struct.unpack_from(f'<{count}B', file_object.read(count)))
def read_ushort_array(file_object, count):
    raw = file_object.read(count * 2)
    return list(struct.unpack_from(f'<{count}H', raw))
def read_uint_array(file_object, count):
    raw = file_object.read(count * 4)
    return list(struct.unpack_from(f'<{count}I', raw))
def read_uint64_array(file_object, count):
    raw = file_object.read(count * 8)
    return list(struct.unpack_from(f'<{count}Q', raw))
def read_short_array(file_object, count):
    raw = file_object.read(count * 2)
    return list(struct.unpack_from(f'<{count}h', raw))
def read_float_array(file_object, count):
    raw = file_object.read(count * 4)
    return list(struct.unpack_from(f'<{count}f', raw))
def read_int_array(file_object, count):
    raw = file_object.read(count * 4)
    return list(struct.unpack_from(f'<{count}i', raw))

# Batch write helpers
def write_ubyte_array(file_object, values):
    file_object.write(struct.pack(f'<{len(values)}B', *values))
def write_ushort_array(file_object, values):
    file_object.write(struct.pack(f'<{len(values)}H', *values))
def write_uint_array(file_object, values):
    file_object.write(struct.pack(f'<{len(values)}I', *values))
def write_uint64_array(file_object, values):
    file_object.write(struct.pack(f'<{len(values)}Q', *values))
def write_short_array(file_object, values):
    file_object.write(struct.pack(f'<{len(values)}h', *values))
def write_float_array(file_object, values):
    file_object.write(struct.pack(f'<{len(values)}f', *values))
def write_int_array(file_object, values):
    file_object.write(struct.pack(f'<{len(values)}i', *values))
#bitflag operations
def getBit(bitFlag, index):#Index starting from rightmost bit
    return bool((bitFlag >> index) & 1)
def setBit(bitFlag, index):
    return bitFlag | (1 << index)
def raiseError(error,errorCode = 999):
	"""Print a colored error message and abort by raising.

	Callers rely on this stopping execution (e.g. failed file opens must not
	fall through to using an unbound file object), so it always raises.
	"""
	print(textColors.FAIL + "ERROR: " + error + textColors.ENDC)
	raise Exception(error)

def openFileRead(filepath, buffering=-1):
	"""Open a binary file for reading; raise a descriptive error on failure."""
	try:
		return open(filepath, "rb", buffering=buffering)
	except OSError as err:
		raise Exception("Failed to open " + filepath + " for reading: " + str(err))

def openFileWrite(filepath, buffering=-1):
	"""Open a binary file for writing; raise a descriptive error on failure."""
	try:
		return open(filepath, "wb", buffering=buffering)
	except OSError as err:
		raise Exception("Failed to open " + filepath + " for writing: " + str(err))


# Set to True to re-enable the warnings printed after a mesh/MDF export.
ENABLE_EXPORT_WARNINGS = False

def raiseWarning(warning):
     print(textColors.WARNING + "WARNING: " + warning + textColors.ENDC)
	 
def getPaddedPos(currentPos,alignment):
	return ((currentPos*-1)%alignment)+currentPos

def getFolderSize(path='.'):
	total = 0
	try:
		for entry in os.scandir(path):
			if entry.is_file():
				total += entry.stat().st_size
			elif entry.is_dir():
				total += getFolderSize(entry.path)
	except:
		total = -1
	return total

def formatByteSize(num, suffix="B"):
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"

def wildCardFileSearch(wildCardFilePath):#Returns first file found matching wildcard, none if not found
	search = glob.glob(wildCardFilePath)
	if not search:
		return None
	return search[0]

def wildCardFileSearchList(wildCardFilePath):#Returns all files matching wildcard
	return glob.glob(wildCardFilePath)

def splitNativesPath(filePath):#Splits file path of RE Engine natives/platform folder, returns none if there's no natives folder
	path = Path(filePath)	
	parts = path.parts
	try:
		if "natives" in filePath.lower():
			nativesIndex = next((i for i, part in enumerate(parts) if part.lower() == "natives"), None)
			rootPath = str(Path(*parts[:nativesIndex+2]))#stage\m01\a02\m01a02_iwa.mesh.2109148288
			nativesPath = str(Path(*parts[nativesIndex+2::]))#F:\MHR_EXTRACT\extract\re_chunk_000\natives\STM
			return (rootPath,nativesPath)
		else:
			return None
	except:
		return None

# Name of the sub folder (directly under the natives game folder) that mirrors the
# texture path tree and holds the high resolution mip halves of streaming textures.
STREAMING_FOLDER = "streaming"

def getAdjacentFileVersion(rootPath,fileType):
	fileVersion = -1
	search = wildCardFileSearch(os.path.join(glob.escape(rootPath),"*"+fileType+"*"))
	if search != None:
		versionExtension = os.path.splitext(search)[1][1::]
		if versionExtension.isdigit():
			fileVersion = int(versionExtension)
	return fileVersion

IS_WINDOWS = platform.system() == 'Windows'
IS_LINUX = platform.system() == 'Linux'
IS_MAC = platform.system() == 'Darwin'
# Addon install root (folder containing __init__.py) and shared Presets directory
ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_DIR = os.path.join(ADDON_ROOT, "Presets")

def slugify(value, allow_unicode=False):
    """
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single dashes. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize("NFKC", value)
    else:
        value = (
            unicodedata.normalize("NFKD", value)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
    value = re.sub(r"[^\w\s-]", "", value)
    return re.sub(r"[-\s]+", "_", value).strip("-_")

def openFolder(path):
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])

def isLinux():
    return not IS_WINDOWS

def _case_insensitive_pattern(name):
    escaped = glob.escape(name)
    pattern = ""
    i = 0
    while i < len(escaped):
        c = escaped[i]
        # Handle escaped characters like \*
        if c == "\\" and i + 1 < len(escaped):
            pattern += escaped[i:i+2]
            i += 2
            continue

        if c.isalpha():
            pattern += f"[{c.lower()}{c.upper()}]"
        else:
            pattern += c
        i += 1
    return pattern


def resolveLinuxPath(path):
    path = os.path.abspath(path)
    if os.path.exists(path):
        return path

    parts = path.strip(os.sep).split(os.sep)
    current = os.sep if path.startswith(os.sep) else os.getcwd()

    for part in parts:
        pattern = _case_insensitive_pattern(part)
        search_pattern = os.path.join(current, pattern)
        matches = glob.glob(search_pattern)
        if not matches:
            return None
        current = matches[0]
    return current

def _color(text, code=33): return f"\033[{code}m{text}\033[0m"
def y(text): return _color(text, 33)    # 黄色
def fname(path): return y(os.path.basename(path))

# --- Public timing utilities ---

_timeFormat = "%d"

def formatMs(seconds):
	"""Convert seconds (float) to a formatted millisecond string, e.g. '318'."""
	return _timeFormat % (seconds * 1000)

def printElapsed(label, startTime, color=y, suffix=""):
	"""Print elapsed time since startTime in milliseconds.
	Args:
		label: Description of the operation, e.g. 'Mesh build'.
		startTime: time.time() captured before the operation.
		color: Color function to highlight the time value (default: y/yellow).
			 Pass False or None to disable coloring.
		suffix: Extra text appended after 'ms.', e.g. ' (5 workers, 12 meshes).'.
	"""
	ms = formatMs(time.time() - startTime)
	if color:
		ms = color(ms)
	print(f"{label} took {ms} ms.{suffix}")


def parseFileVersion(filepath, default=None):
	"""Parse the integer version number from a file's extension.
	
	e.g. 'file.mesh.221108797' -> 221108797
	Returns `default` if parsing fails.
	"""
	try:
		return int(os.path.splitext(filepath)[1].replace(".", ""))
	except:
		return default


