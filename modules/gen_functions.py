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
_struct_int64 = struct.Struct('<q')
_struct_uint64 = struct.Struct('<Q')
_struct_float = struct.Struct('<f')
_struct_double = struct.Struct('<d')


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
os.system("color")#Enable console colors
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

# read unsigned byte from file
def read_ubyte(file_object, endian = '<'):
     return _struct_ubyte.unpack(file_object.read(1))[0] if endian == '<' else struct.unpack(endian+'B', file_object.read(1))[0]
# read signed byte from file
def read_byte(file_object, endian = '<'):
     return _struct_byte.unpack(file_object.read(1))[0] if endian == '<' else struct.unpack(endian+'b', file_object.read(1))[0]
 # read signed short from file
def read_short(file_object, endian = '<'):
     return _struct_short.unpack(file_object.read(2))[0] if endian == '<' else struct.unpack(endian+'h', file_object.read(2))[0]
# read unsigned short from file
def read_ushort(file_object, endian = '<'):
     return _struct_ushort.unpack(file_object.read(2))[0] if endian == '<' else struct.unpack(endian+'H', file_object.read(2))[0]
# read unsigned integer from filel
def read_uint(file_object, endian = '<'):
     return _struct_uint.unpack(file_object.read(4))[0] if endian == '<' else struct.unpack(endian+'I', file_object.read(4))[0]
# read signed integer from file
def read_int(file_object, endian = '<'):
     return _struct_int.unpack(file_object.read(4))[0] if endian == '<' else struct.unpack(endian+'i', file_object.read(4))[0]
# read unsigned long integer from file
def read_uint64(file_object, endian = '<'):
     return _struct_uint64.unpack(file_object.read(8))[0] if endian == '<' else struct.unpack(endian+'Q', file_object.read(8))[0]
# read signed long integer from file
def read_int64(file_object, endian = '<'):
     return _struct_int64.unpack(file_object.read(8))[0] if endian == '<' else struct.unpack(endian+'q', file_object.read(8))[0]
# read floating point number from file
def read_float(file_object, endian = '<'):
     return _struct_float.unpack(file_object.read(4))[0] if endian == '<' else struct.unpack(endian+'f', file_object.read(4))[0]
# read double from file
def read_double(file_object, endian = '<'):
     return _struct_double.unpack(file_object.read(8))[0] if endian == '<' else struct.unpack(endian+'d', file_object.read(8))[0]
#read null terminated string from file
def read_string(file_object):
     return ''.join(iter(lambda: file_object.read(1).decode('utf-8'), '\x00'))
def read_unicode_string(file_object):#Reads unicode string from file into utf-8 string
	byteString = b''
	wchar = file_object.read(2)
	while wchar != b'\x00\x00':
		byteString += wchar
		wchar = file_object.read(2)
	return byteString.decode("utf-16le").replace('\x00', '')
# write unsigned byte to file
def write_ubyte(file_object,input, endian = '<'):
     file_object.write(_struct_ubyte.pack(input) if endian == '<' else struct.pack(endian+'B', input))
# write signed byte to file
def write_byte(file_object,input, endian = '<'):
     file_object.write(_struct_byte.pack(input) if endian == '<' else struct.pack(endian+'b', input))
 # write signed short to file
def write_short(file_object,input, endian = '<'):
     file_object.write(_struct_short.pack(input) if endian == '<' else struct.pack(endian+'h', input))
 # write unsigned short to file
def write_ushort(file_object,input, endian = '<'):
     file_object.write(_struct_ushort.pack(input) if endian == '<' else struct.pack(endian+'H', input))
 # write unsigned integer to file
def write_uint(file_object,input, endian = '<'):
     file_object.write(_struct_uint.pack(input) if endian == '<' else struct.pack(endian+'I', input))
# write signed integer to file
def write_int(file_object,input, endian = '<'):
     file_object.write(_struct_int.pack(input) if endian == '<' else struct.pack(endian+'i', input))
 # write unsigned long integer to file
def write_uint64(file_object,input, endian = '<'):
     file_object.write(_struct_uint64.pack(input) if endian == '<' else struct.pack(endian+'Q', input))
 # write unsigned long integer to file
def write_int64(file_object,input, endian = '<'):
     file_object.write(_struct_int64.pack(input) if endian == '<' else struct.pack(endian+'q', input))
# write floating point number to file
def write_float(file_object,input, endian = '<'):
     file_object.write(_struct_float.pack(input) if endian == '<' else struct.pack(endian+'f', input))
# write double to file
def write_double(file_object,input, endian = '<'):
     file_object.write(_struct_double.pack(input) if endian == '<' else struct.pack(endian+'d', input))
#write null terminated string to file
def write_string(file_object,input):
     file_object.write(bytes(input + '\x00', 'utf-8'))
def write_unicode_string(file_object,input):#Writes utf-8 string as utf-16
     file_object.write(input.encode('UTF-16LE') + b'\x00\x00')
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
def unsetBit(bitFlag, index):
    return bitFlag & ~(1 << index)
def raiseError(error,errorCode = 999):
    try:
        raise Exception()
    except Exception:
        print(textColors.FAIL + "ERROR: " + error + textColors.ENDC)
def raiseWarning(warning):
     print(textColors.WARNING + "WARNING: " + warning + textColors.ENDC)
def getByteSection(byteArray,offset,size):
    return byteArray[offset:(offset+size)]
def removeByteSection(byteArray,offset,size):#removes specified amount of bytes from byte array at offset
    del byteArray[offset:(offset+size)]#Deletes directly from the array passed to it
def insertByteSection(byteArray,offset,input):#inserts bytes into bytearray at offset
    byteArray[offset:offset] = input
def dictString(dictionary):#Return string of dictionary contents
	outputString =""
	for key,value in dictionary.items():
		outputString +=str(key)+": "+str(value)+"\n"
	return outputString
def unsignedToSigned(uintValue):
	return ((uintValue & ((1 << 32) - 1)) & ((1 << 31) - 1)) - ((uintValue & ((1 << 32) - 1)) & (1 << 31))
def signedToUnsigned(intValue):
	return intValue & 0xffffffff

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
	
def getAdjacentFileVersion(rootPath,fileType):
	fileVersion = -1
	search = wildCardFileSearch(os.path.join(glob.escape(rootPath),"*"+fileType+"*"))
	if search != None:
		versionExtension = os.path.splitext(search)[1][1::]
		if versionExtension.isdigit():
			fileVersion = int(versionExtension)
	return fileVersion

def progressBar(iterable, prefix = '', suffix = '', decimals = 1, length = 100, fill = '█', printEnd = "\r"):
    """
    Call in a loop to create terminal progress bar
    @params:
        iterable    - Required  : iterable object (Iterable)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        length      - Optional  : character length of bar (Int)
        fill        - Optional  : bar fill character (Str)
        printEnd    - Optional  : end character (e.g. "\r", "\r\n") (Str)
    """
    total = len(iterable)
    # Progress Bar Printing Function
    def printProgressBar (iteration):
        percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
        filledLength = int(length * iteration // total)
        bar = fill * filledLength + '-' * (length - filledLength)
        print(f'\r{prefix} |{bar}| {percent}% {suffix}', end = printEnd)
    # Initial Call
    printProgressBar(0)
    # Update Progress Bar
    for i, item in enumerate(iterable):
        yield item
        printProgressBar(i + 1)
    # Print New Line on Complete
    print()
	

IS_WINDOWS = platform.system() == 'Windows'
def resolvePath(pathString):
	if IS_WINDOWS:
		return pathString
	else:#Fix issues related to case sensitive paths on linux, doesn't matter on windows
		newPath = pathString.replace("/",os.sep).replace("\\",os.sep)
		if not os.path.isfile(newPath):#Lower case the path in case the pak list is lowercased
			newPath = newPath.lower()
			return newPath
		
def splitInt64(value):#Takes int64 and converts to 2 int32's
	return struct.unpack("ii", value.to_bytes(8, "little", signed=False))

def concatInt(a, b):#Combines two int values into a int64
	return (a << 32) | b

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

def log(*args, tag="RE", **kwargs):
    """集中式日志输出, 统一加标签前缀, 便于排查和过滤.
    用法与 print 一致: log("msg") / log("key=%s", val) 支持 str.format 风格."""
    import time
    ts = time.strftime("%H:%M:%S")
    if len(args) == 1 and isinstance(args[0], str) and kwargs:
        msg = args[0].format(*kwargs.pop("args", ()), **kwargs)
    elif len(args) == 1 and isinstance(args[0], str) and "%" in args[0]:
        msg = args[0] % args[1:]
    else:
        msg = " ".join(str(a) for a in args)
    return print(f"[{ts}] [{tag}] {msg}")
	
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
def r(text): return _color(text, 31)    # 红色  
def g(text): return _color(text, 32)    # 绿色
def b(text): return _color(text, 34)    # 蓝色
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
