"""Read visible audio trees and delete only a confirmed, unchanged audio file."""
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import stat
import sys
import windows_usb

AUDIO_EXTENSIONS = frozenset({'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wma', '.aif', '.aiff', '.ape', '.alac'})


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def excluded(name, info):
    return name.startswith('.') or bool(getattr(info, 'st_file_attributes', 0) & (0x2 | 0x4 | 0x400)) or stat.S_ISLNK(info.st_mode)


@dataclass(frozen=True)
class Entry:
    relative: tuple
    name: str
    children: tuple = ()
    is_folder: bool = False
    count: int = 1
    file_id: tuple = ()


@dataclass(frozen=True)
class Snapshot:
    root: Path
    identity: tuple
    entries: tuple
    count: int


def identity(root):
    root = Path(root)
    if sys.platform == 'win32':
        return windows_usb.volume_identity(root)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError('USB folder is unavailable.')
    return (info.st_dev, info.st_ino)


def scan(root, cancel=None):
    root = Path(root).resolve(strict=True)
    expected = identity(root)
    pending = [(root, ())]
    folders = []
    contents = {}
    while pending:
        check_cancel(cancel)
        folder, relative = pending.pop()
        folders.append(relative)
        contents[relative] = []
        try:
            if relative:
                current = folder.lstat()
                if excluded(relative[-1], current) or not stat.S_ISDIR(current.st_mode):
                    continue
            with os.scandir(folder) as stream:
                for item in stream:
                    check_cancel(cancel)
                    try:
                        info = item.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if excluded(item.name, info):
                        continue
                    child = relative + (item.name,)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append((Path(item.path), child))
                    elif stat.S_ISREG(info.st_mode) and Path(item.name).suffix.lower() in AUDIO_EXTENSIONS:
                        if sys.platform == 'win32':
                            # Use the same native API here and at deletion time. Python
                            # path-stat and fd-stat metadata can differ on Windows.
                            try:
                                file_id = windows_file_identity(Path(item.path))
                            except OSError:
                                continue
                        else:
                            file_id = fingerprint(info)
                        contents[relative].append(Entry(child, Path(item.name).stem, file_id=file_id))
        except OSError:
            if not relative:
                raise
    # Build bottom-up, dropping folders with no visible audio descendants.
    built = {}
    for relative in reversed(folders):
        entries = contents[relative]
        entries.sort(key=lambda entry: (entry.is_folder, entry.name.casefold(), entry.relative))
        built[relative] = tuple(entries)
        if relative and entries:
            contents[relative[:-1]].append(Entry(relative, relative[-1], tuple(entries), True,
                                                sum(entry.count for entry in entries)))
    check_cancel(cancel)
    if identity(root) != expected:
        raise OSError('USB changed while reading its songs.')
    entries = built[()]
    return Snapshot(root, expected, entries, sum(entry.count for entry in entries))


def find_entry(entries, relative):
    pending = list(entries)
    while pending:
        entry = pending.pop()
        if entry.relative == relative:
            return entry
        pending.extend(entry.children)
    return None


def validate_file(info, entry):
    if excluded(entry.relative[-1], info) or not stat.S_ISREG(info.st_mode) or fingerprint(info) != entry.file_id:
        raise OSError('This song has changed. Refresh USB and try again.')


def delete_song(snapshot, entry, cancel=None):
    """Never delete folders, links, hidden items, changed files, or a replacement drive."""
    check_cancel(cancel)
    if entry.is_folder or not entry.relative or any(part in ('', '.', '..') or '/' in part or '\\' in part or ':' in part for part in entry.relative):
        raise ValueError('Choose a song from USB.')
    if Path(entry.relative[-1]).suffix.lower() not in AUDIO_EXTENSIONS or find_entry(snapshot.entries, entry.relative) != entry:
        raise ValueError('Choose a song from USB.')
    if identity(snapshot.root) != snapshot.identity:
        raise OSError('USB changed. Refresh USB and try again.')
    parent = snapshot.root
    for component in entry.relative[:-1]:
        parent = parent / component
        info = parent.lstat()
        if excluded(component, info) or not stat.S_ISDIR(info.st_mode):
            raise OSError('The song folder has changed. Refresh USB and try again.')
    target = parent / entry.relative[-1]
    current = target.lstat()
    if excluded(entry.relative[-1], current) or not stat.S_ISREG(current.st_mode):
        raise OSError('This song has changed. Refresh USB and try again.')
    if sys.platform != 'win32':
        validate_file(current, entry)
    check_cancel(cancel)
    if sys.platform == 'win32':
        _delete_windows(snapshot, entry, target, cancel)
    else:
        _delete_posix(snapshot, entry, cancel)


def _delete_posix(snapshot, entry, cancel):
    descriptor = os.open(snapshot.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in entry.relative[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        validate_file(os.stat(entry.relative[-1], dir_fd=descriptor, follow_symlinks=False), entry)
        if identity(snapshot.root) != snapshot.identity:
            raise OSError('USB changed. Refresh USB and try again.')
        check_cancel(cancel)
        os.unlink(entry.relative[-1], dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _delete_windows(snapshot, entry, target, cancel):
    # Delete by a verified file handle, rather than reopening a possibly replaced path.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(target), 0x10000 | 0x80, 0x1 | 0x2, None, 3, 0x200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        observed = windows_file_identity(handle=handle)
        if observed != entry.file_id:
            error = OSError('This song has changed. Refresh USB and try again.')
            error.add_note(f'Expected native identity={entry.file_id}; observed={observed}')
            raise error
        final_path = kernel.GetFinalPathNameByHandleW
        final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        final_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        size = final_path(handle, buffer, len(buffer), 1)  # VOLUME_NAME_GUID
        if not size or size >= len(buffer):
            raise OSError('Could not verify the song location. No song was deleted.')
        volume_root, volume_guid = snapshot.identity
        base = snapshot.root.relative_to(Path(volume_root))
        expected = str(Path(volume_guid) / base / Path(*entry.relative))
        if buffer.value.casefold() != expected.casefold() or identity(snapshot.root) != snapshot.identity:
            raise OSError('USB or song location changed. No song was deleted.')
        check_cancel(cancel)
        class Disposition(ctypes.Structure):
            _fields_ = [('DeleteFile', ctypes.c_ubyte)]
        mark = kernel.SetFileInformationByHandle
        mark.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        mark.restype = wintypes.BOOL
        disposition = Disposition(1)
        if not mark(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def windows_file_identity(path=None, *, handle=None):
    """Read consistent file IDs/times through GetFileInformationByHandle only."""
    import ctypes
    from ctypes import wintypes
    kernel, Information = _windows_file_api()
    owned = handle is None
    if owned:
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        handle = create(str(path), 0x80, 0x1 | 0x2 | 0x4, None, 3, 0x200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    query = kernel.GetFileInformationByHandle
    try:
        info = Information()
        if not query(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x2 | 0x4 | 0x10 | 0x400):
            raise OSError('This song has changed. Refresh USB and try again.')
        combine = lambda high, low: (int(high) << 32) | int(low)
        return ('win32', int(info.volume), combine(info.index_high, info.index_low),
                combine(info.size_high, info.size_low),
                combine(info.written.dwHighDateTime, info.written.dwLowDateTime),
                combine(info.created.dwHighDateTime, info.created.dwLowDateTime))
    finally:
        if owned:
            kernel.CloseHandle(handle)


@lru_cache(maxsize=1)
def _windows_file_api():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Information(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                    ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME),
                    ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                    ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                    ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]
    query = kernel.GetFileInformationByHandle
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Information)]
    query.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel, Information


def smoke_test():
    """Exercise scanning and native deletion using only our own temporary files."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='YouTubeToUSB library ') as work:
        root = Path(work)
        (root / 'Song.mp3').write_bytes(b'test fixture')
        (root / 'Classics').mkdir()
        (root / 'Classics' / 'Keep.wav').write_bytes(b'keep fixture')
        (root / '.hidden.mp3').write_bytes(b'hidden fixture')
        snapshot = scan(root)
        if snapshot.count != 2:
            raise RuntimeError('USB library scan returned an incorrect song count.')
        delete_song(snapshot, find_entry(snapshot.entries, ('Song.mp3',)))
        if (root / 'Song.mp3').exists() or not (root / 'Classics' / 'Keep.wav').exists() or scan(root).count != 1:
            raise RuntimeError('USB library deletion check failed.')
