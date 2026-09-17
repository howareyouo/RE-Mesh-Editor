"""Utils for I/O."""

import os
import platform
from ...gen_functions import IS_WINDOWS, IS_LINUX, IS_MAC


def mkdir(directory):
    """Make directory."""
    os.makedirs(directory, exist_ok=True)


def get_ext(file):
    """Get file extension."""
    return file.split('.')[-1].lower()


def get_size(f):
    pos = f.tell()
    f.seek(0, 2)
    size = f.tell()
    f.seek(pos)
    return size


def get_os_name():
    return platform.system()


def is_windows():
    return IS_WINDOWS


def is_linux():
    return IS_LINUX


def is_mac():
    return IS_MAC


def is_arm():
    return 'arm' in platform.machine().lower()
