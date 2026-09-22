#!/usr/bin/env python3
"""Validate, install, update, roll back, and uninstall tony-agents-pack."""

import argparse
from contextlib import contextmanager, nullcontext
import ctypes
import datetime as dt
import errno
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zlib
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
PLUGIN_FILE = ROOT / ".zcode-plugin" / "plugin.json"
PACKAGE_NAME = "tony-agents-pack"
STATE_SCHEMA_VERSION = 2
SNAPSHOT_SCHEMA_VERSION = 2
EXPECTED_AGENT_COUNT = 22
COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
TOOLS = {"Read", "Glob", "Grep", "Write", "Edit", "Bash", "WebFetch", "WebSearch", "TodoWrite"}
FORBIDDEN_PUBLISHED_KEYS = {"model", "thoughtLevel", "skills"}
PUBLISHED_MODEL_NAME_RE = re.compile(r"(?i)\b(?:glm|gpt|deepseek|kimi|gemini)\b")
INJECTION_DEFENSE_RE = re.compile(r"注入防御|不可信内容(?:与[^\n#]*)?防线|不可信内容|不可信数据|提示注入")
COMMON_ACCEPTANCE_MARKER = "report-id / role / requirement-version / snapshot(commit/source/artifact SHA/build-id) / generated-at"
ACCEPTANCE_AGENTS = {"shencha", "shencha-content", "shencha-ui", "verifier", "shencha-final"}
HARD_READ_ONLY_AGENTS = {"github", "shencha-content"}
HARD_READ_ONLY_FORBIDDEN_TOOLS = {"Bash", "Write", "Edit"}
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:\s*(.*))?$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MIN_SCREENSHOT_BYTES = 10 * 1024
MAX_PNG_CHUNK_BYTES = 64 * 1024 * 1024
MODEL_SCREENSHOTS = (
    "docs/images/zcode-model-glm.png",
    "docs/images/zcode-model-deepseek.png",
    "docs/images/zcode-model-kimi.png",
    "docs/images/zcode-model-google.png",
)
QR_IMAGE = "docs/images/wechat-group-qr.png"
RELEASE_PNGS = MODEL_SCREENSHOTS + (QR_IMAGE,)
MODEL_GUIDE_VENDORS = ("智谱", "DeepSeek", "Kimi", "阿里云百炼", "硅基流动")
QR_URL = "https://cos.files.maozhishi.com/data/web/web-files/wx/tony-apan.png"
PUBLIC_DOC_FORBIDDEN_TEXT = ("/Users/tony", "010_zcode_skills", "github.com/tony-apan/010")
PUBLIC_SECRET_RE = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{12,}\b|\bAIza[A-Za-z0-9_-]{20,}\b|\bBearer\s+[A-Za-z0-9._~+/-]{12,})"
)


class PackError(Exception):
    pass


class DecisionRequired(PackError):
    pass


def transactional(operation: str, dry_run_index: int = 0):
    def decorate(method):
        @functools.wraps(method)
        def wrapped(self, *args, **kwargs):
            self._active_snapshot = None
            self._active_writes = {}
            dry_run = kwargs.get("dry_run")
            if dry_run is None and len(args) > dry_run_index:
                dry_run = args[dry_run_index]
            lock_context = nullcontext() if dry_run else self.operation_lock()
            try:
                with lock_context:
                    try:
                        return method(self, *args, **kwargs)
                    except BaseException as original_error:
                        snapshot = self._active_snapshot
                        if snapshot is None:
                            raise
                        try:
                            self.restore_snapshot(snapshot)
                        except BaseException as rollback_error:
                            raise PackError(
                                "操作失败且自动回滚失败 ({}): 原始错误: {}; 回滚错误: {}".format(
                                    operation, original_error, rollback_error
                                )
                            ) from original_error
                        message = "操作失败且已自动回滚 ({}): {}".format(operation, original_error)
                        if isinstance(original_error, KeyboardInterrupt):
                            raise original_error
                        if isinstance(original_error, DecisionRequired):
                            raise DecisionRequired(message) from original_error
                        raise PackError(message) from original_error
            finally:
                self._active_snapshot = None
                self._active_writes = {}

        return wrapped

    return decorate


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _file_identity(value: os.stat_result) -> Tuple[int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000)),
    )


def read_optional_regular_bytes(
    path: Path,
    label: str,
    error_type=DecisionRequired,
) -> Optional[bytes]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    parent_token = None
    parent_fd: Optional[int] = None
    fd: Optional[int] = None
    try:
        try:
            if ".tony-agents-pack" in path.parts:
                _, parent_token = open_directory_no_symlinks(path.parent, create=False)
                parent_fd = directory_fd_value(parent_token)
                if parent_fd is None:
                    before = path.lstat()
                    if not stat.S_ISREG(before.st_mode):
                        raise error_type("{} is not a regular file: {}".format(label, path))
                    fd = os.open(str(path), flags)
                else:
                    before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if not stat.S_ISREG(before.st_mode):
                        raise error_type("{} is not a regular file: {}".format(label, path))
                    fd = os.open(path.name, flags, dir_fd=parent_fd)
            else:
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode):
                    raise error_type("{} is not a regular file: {}".format(label, path))
                fd = os.open(str(path), flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise error_type("cannot open {} safely: {}".format(label, exc))

        with os.fdopen(fd, "rb") as handle:
            fd = None
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _file_identity(opened) != _file_identity(before):
                raise error_type("{} changed before it could be read safely: {}".format(label, path))
            data = handle.read()
            after_read = os.fstat(handle.fileno())

        try:
            after = (
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                if parent_fd is not None
                else path.lstat()
            )
        except OSError as exc:
            raise error_type("{} changed while it was being read: {}".format(label, exc))
        identities = {_file_identity(value) for value in (before, opened, after_read, after)}
        if len(identities) != 1 or len(data) != after.st_size or not stat.S_ISREG(after.st_mode):
            raise error_type("{} changed while it was being read: {}".format(label, path))
        return data
    finally:
        if fd is not None:
            os.close(fd)
        close_directory_token(parent_token)


def read_required_regular_bytes(path: Path, label: str, error_type=PackError) -> bytes:
    data = read_optional_regular_bytes(path, label, error_type)
    if data is None:
        raise error_type("{} is missing: {}".format(label, path))
    return data


def validate_png_structure(data: bytes) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise PackError("does not have a valid PNG signature")

    offset = len(PNG_SIGNATURE)
    chunk_index = 0
    saw_idat = False
    saw_iend = False
    while offset < len(data):
        if len(data) - offset < 8:
            raise PackError("has a truncated PNG chunk header")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        if length > MAX_PNG_CHUNK_BYTES:
            raise PackError("has an oversized PNG chunk")
        if len(data) - offset < 12:
            raise PackError("has a truncated PNG chunk")
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise PackError("has a PNG chunk length beyond the file boundary")

        chunk_type = data[offset + 4 : offset + 8]
        if not re.fullmatch(b"[A-Za-z]{4}", chunk_type):
            raise PackError("has a PNG chunk type that is not four ASCII letters")
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise PackError("has a PNG chunk with an invalid CRC")

        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                raise PackError("must start with a 13-byte IHDR chunk")
            width, height = struct.unpack(">II", chunk_data[:8])
            if width == 0 or height == 0:
                raise PackError("has zero PNG width or height")
        elif chunk_type == b"IHDR":
            raise PackError("has an IHDR chunk after the first chunk")

        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0:
                raise PackError("has a non-empty IEND chunk")
            saw_iend = True
            offset = chunk_end
            if offset != len(data):
                raise PackError("has trailing data after IEND")
            break

        offset = chunk_end
        chunk_index += 1

    if not saw_idat:
        raise PackError("does not contain an IDAT chunk")
    if not saw_iend:
        raise PackError("does not contain an IEND chunk")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def unique_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def open_windows_lock_file(path: Path) -> int:
    from ctypes import wintypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
    get_information.restype = wintypes.BOOL

    generic_read_write = 0xC0000000
    file_share_read_write = 0x00000003
    open_always = 4
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value

    handle = create_file(
        str(path),
        generic_read_write,
        file_share_read_write,
        None,
        open_always,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        raise DecisionRequired("cannot open operation lock safely: {}".format(ctypes.FormatError(error)))
    information = ByHandleFileInformation()
    if not get_information(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        close_handle(handle)
        raise DecisionRequired("cannot inspect operation lock safely: {}".format(ctypes.FormatError(error)))
    if information.file_attributes & (file_attribute_directory | file_attribute_reparse_point):
        close_handle(handle)
        raise DecisionRequired("operation lock is not a regular non-reparse file")
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except BaseException:
        close_handle(handle)
        raise


class WindowsDirectoryGuard:
    def __init__(self, handles: List[int], close_handle) -> None:
        self.handles = handles
        self._close_handle = close_handle

    def close(self) -> None:
        while self.handles:
            self._close_handle(self.handles.pop())


def directory_fd_value(token) -> Optional[int]:
    return token if isinstance(token, int) else None


def close_directory_token(token) -> None:
    if token is None:
        return
    if isinstance(token, int):
        os.close(token)
    else:
        token.close()


def open_windows_directory_chain(path: Path, create: bool) -> Tuple[Path, WindowsDirectoryGuard]:
    from ctypes import wintypes

    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    if not create and not absolute.exists():
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(absolute))

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
    get_information.restype = wintypes.BOOL

    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value

    current = Path(absolute.anchor)
    chain = [current]
    for part in absolute.parts[1:]:
        current = current / part
        chain.append(current)

    handles: List[int] = []
    try:
        for current in chain:
            handle = create_file(
                str(current),
                file_read_attributes,
                file_share_read | file_share_write,
                None,
                open_existing,
                file_flag_open_reparse_point | file_flag_backup_semantics,
                None,
            )
            if handle == invalid_handle:
                error = ctypes.get_last_error()
                if error in (2, 3) and create:
                    try:
                        current.mkdir()
                    except FileExistsError:
                        pass
                    handle = create_file(
                        str(current),
                        file_read_attributes,
                        file_share_read | file_share_write,
                        None,
                        open_existing,
                        file_flag_open_reparse_point | file_flag_backup_semantics,
                        None,
                    )
                    if handle == invalid_handle:
                        error = ctypes.get_last_error()
                if handle == invalid_handle:
                    if error in (2, 3) and not create:
                        raise FileNotFoundError(error, ctypes.FormatError(error), str(current))
                    raise DecisionRequired(
                        "cannot open Windows directory guard {}: {}".format(current, ctypes.FormatError(error))
                    )
            information = ByHandleFileInformation()
            if not get_information(handle, ctypes.byref(information)):
                error = ctypes.get_last_error()
                close_handle(handle)
                raise DecisionRequired(
                    "cannot inspect Windows directory guard {}: {}".format(current, ctypes.FormatError(error))
                )
            if not information.file_attributes & file_attribute_directory:
                close_handle(handle)
                raise DecisionRequired("metadata path is not a directory: {}".format(current))
            if information.file_attributes & file_attribute_reparse_point:
                close_handle(handle)
                raise DecisionRequired("metadata path contains a reparse point: {}".format(current))
            handles.append(handle)
        return absolute, WindowsDirectoryGuard(handles, close_handle)
    except BaseException:
        while handles:
            close_handle(handles.pop())
        raise


def open_directory_no_symlinks(path: Path, create: bool = True):
    if os.name == "nt":
        return open_windows_directory_chain(path, create)

    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    parts = absolute.parts
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        metadata_index = parts.index(".tony-agents-pack")
    except ValueError:
        resolved = absolute.resolve(strict=False)
        if create:
            resolved.mkdir(parents=True, exist_ok=True)
        try:
            return resolved, os.open(str(resolved), flags)
        except OSError as exc:
            raise DecisionRequired("cannot open directory safely {}: {}".format(resolved, exc))

    current = Path(parts[0], *parts[1:metadata_index]).resolve(strict=False)
    if create:
        current.mkdir(parents=True, exist_ok=True)
    try:
        directory_fd = os.open(str(current), flags)
    except FileNotFoundError:
        if not create:
            raise
        raise DecisionRequired("metadata parent disappeared while opening: {}".format(current))
    except OSError as exc:
        raise DecisionRequired("cannot open metadata parent safely {}: {}".format(current, exc))
    try:
        for part in parts[metadata_index:]:
            try:
                child_fd = os.open(part, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                child_fd = os.open(part, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise DecisionRequired("metadata path contains a symlink or non-directory: {} ({})".format(current / part, exc))
            os.close(directory_fd)
            directory_fd = child_fd
            current = current / part
        return current, directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def ensure_directory_tree_no_symlinks(path: Path) -> Path:
    resolved, directory_token = open_directory_no_symlinks(path)
    close_directory_token(directory_token)
    return resolved


def fsync_directory(path: Path, directory_token=None) -> None:
    if os.name == "nt":
        return
    directory_fd = directory_fd_value(directory_token)
    close_after = directory_fd is None
    if directory_fd is None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_fd = os.open(str(path), flags)
        except OSError as exc:
            raise PackError("cannot open directory for durability sync {}: {}".format(path, exc))
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        raise PackError("cannot sync directory {}: {}".format(path, exc))
    finally:
        if close_after:
            os.close(directory_fd)


def rename_path(
    source: Path,
    target: Path,
    source_dir_fd: Optional[int] = None,
    target_dir_fd: Optional[int] = None,
) -> None:
    if source_dir_fd is None:
        os.replace(source, target)
    else:
        os.replace(
            source.name,
            target.name,
            src_dir_fd=source_dir_fd,
            dst_dir_fd=target_dir_fd,
        )


def exclusive_rename(
    source: Path,
    target: Path,
    source_dir_fd: Optional[int] = None,
    target_dir_fd: Optional[int] = None,
) -> bool:
    source_bytes = os.fsencode(source.name if source_dir_fd is not None else source)
    target_bytes = os.fsencode(target.name if target_dir_fd is not None else target)
    source_fd = source_dir_fd if source_dir_fd is not None else -100
    target_fd = target_dir_fd if target_dir_fd is not None else -100
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = libc.renameatx_np
        renameatx_np.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(source_fd, source_bytes, target_fd, target_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise DecisionRequired("filesystem runtime does not provide exclusive rename")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        result = renameat2(source_fd, source_bytes, target_fd, target_bytes, 0x00000001)
    elif os.name == "nt":
        try:
            os.rename(source, target)
        except FileExistsError:
            return False
        return True
    else:
        raise DecisionRequired("platform does not provide exclusive rename")
    if result == 0:
        return True
    error = ctypes.get_errno()
    if error in (errno.EEXIST, errno.ENOTEMPTY):
        return False
    raise DecisionRequired("exclusive rename failed for {}: {}".format(target, os.strerror(error)))


def unlink_path(path: Path, directory_fd: Optional[int] = None) -> None:
    if directory_fd is None:
        path.unlink()
    else:
        os.unlink(path.name, dir_fd=directory_fd)


def temporary_file(parent: Path, directory_fd: Optional[int], prefix: str, suffix: str = "") -> Tuple[int, Path]:
    if directory_fd is None:
        fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=str(parent))
        return fd, Path(name)
    for _ in range(100):
        name = prefix + secrets.token_hex(8) + suffix
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(name, flags, 0o600, dir_fd=directory_fd), parent / name
        except FileExistsError:
            continue
    raise PackError("cannot allocate a unique temporary file in {}".format(parent))


def restore_claimed_path(claimed: Path, target: Path, directory_token=None) -> Optional[Path]:
    directory_fd = directory_fd_value(directory_token)
    if exclusive_rename(claimed, target, directory_fd, directory_fd):
        fsync_directory(target.parent, directory_token)
        return None
    return claimed


def claim_expected_path(
    path: Path,
    expected_sha: str,
    parent: Optional[Path] = None,
    directory_token=None,
) -> Path:
    owns_directory_token = parent is None
    if parent is None:
        parent, directory_token = open_directory_no_symlinks(path.parent)
    directory_fd = directory_fd_value(directory_token)
    path = parent / path.name
    try:
        fd, claimed = temporary_file(parent, directory_fd, path.name + ".tony-agents-pack.concurrent.")
        os.close(fd)
        try:
            rename_path(path, claimed, directory_fd, directory_fd)
        except BaseException:
            try:
                unlink_path(claimed, directory_fd)
            except FileNotFoundError:
                pass
            raise DecisionRequired("target changed before ownership claim: {}".format(path))
        fsync_directory(parent, directory_token)
        try:
            claimed_data = read_required_regular_bytes(claimed, "claimed target", DecisionRequired)
            if sha256_bytes(claimed_data) != expected_sha:
                candidate = restore_claimed_path(claimed, path, directory_token)
                suffix = "" if candidate is None else "; preserved candidate {}".format(candidate)
                raise DecisionRequired("target changed before ownership claim: {}{}".format(path, suffix))
        except BaseException:
            try:
                restore_claimed_path(claimed, path, directory_token)
            except BaseException:
                pass
            raise
        return claimed
    finally:
        if owns_directory_token:
            close_directory_token(directory_token)


def conditional_atomic_write(path: Path, data: bytes, expected_sha: Optional[str]) -> None:
    """Publish bytes only after atomically claiming the approved preimage."""
    parent, directory_token = open_directory_no_symlinks(path.parent)
    directory_fd = directory_fd_value(directory_token)
    path = parent / path.name
    fd, temporary = temporary_file(parent, directory_fd, "." + path.name + ".", ".tmp")
    claimed: Optional[Path] = None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_sha is None:
            if not exclusive_rename(temporary, path, directory_fd, directory_fd):
                raise DecisionRequired("target changed immediately before exclusive publish: {}".format(path))
        else:
            claimed = claim_expected_path(path, expected_sha, parent, directory_token)
            if not exclusive_rename(temporary, path, directory_fd, directory_fd):
                raise DecisionRequired(
                    "target changed during exclusive publish: {}; preserved candidate {}".format(path, claimed)
                )
        fsync_directory(parent, directory_token)
        if claimed is not None:
            unlink_path(claimed, directory_fd)
            fsync_directory(parent, directory_token)
    except BaseException:
        if claimed is not None:
            try:
                restore_claimed_path(claimed, path, directory_token)
            except BaseException:
                pass
        try:
            unlink_path(temporary, directory_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        close_directory_token(directory_token)


def atomic_write(
    path: Path,
    data: bytes,
    expected_sha: Optional[str] = None,
    verify_preimage: bool = False,
) -> None:
    if verify_preimage:
        conditional_atomic_write(path, data, expected_sha)
        return
    parent, directory_token = open_directory_no_symlinks(path.parent)
    directory_fd = directory_fd_value(directory_token)
    path = parent / path.name
    fd, temporary = temporary_file(parent, directory_fd, "." + path.name + ".", ".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        rename_path(temporary, path, directory_fd, directory_fd)
        fsync_directory(parent, directory_token)
    except BaseException:
        try:
            unlink_path(temporary, directory_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        close_directory_token(directory_token)


def atomic_json(
    path: Path,
    value: object,
    expected_sha: Optional[str] = None,
    verify_preimage: bool = False,
) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write(
        path,
        data.encode("utf-8"),
        expected_sha=expected_sha,
        verify_preimage=verify_preimage,
    )


def parse_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PackError("invalid quoted frontmatter value: {}".format(exc))
        if not isinstance(parsed, str):
            raise PackError("frontmatter scalar must be a string")
        return parsed
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def frontmatter_parts(text: str) -> Tuple[List[str], List[str]]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise PackError("frontmatter must start with an exact --- delimiter")
    closing = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            closing = index
            break
    if closing is None:
        raise PackError("frontmatter closing --- delimiter is missing")
    return lines[1:closing], lines[closing + 1 :]


def parse_frontmatter(text: str) -> Dict[str, object]:
    frontmatter, _ = frontmatter_parts(text)
    result: Dict[str, object] = {}
    index = 0
    while index < len(frontmatter):
        raw = frontmatter[index].rstrip("\r\n")
        if not raw or raw.lstrip().startswith("#") or raw[:1].isspace():
            index += 1
            continue
        match = KEY_RE.match(raw)
        if not match:
            raise PackError("invalid top-level frontmatter line: {!r}".format(raw))
        key, value = match.group(1), (match.group(2) or "").strip()
        if key in result:
            raise PackError("duplicate top-level frontmatter key: {}".format(key))
        if key in {"tools", "disallowedTools"}:
            if value:
                if not (value.startswith("[") and value.endswith("]")):
                    raise PackError("tools must be an inline array or an indented list")
                inner = value[1:-1].strip()
                result[key] = [] if not inner else [parse_scalar(item.strip()) for item in inner.split(",")]
            else:
                items: List[str] = []
                lookahead = index + 1
                while lookahead < len(frontmatter):
                    candidate = frontmatter[lookahead].rstrip("\r\n")
                    item = re.match(r"^\s+-\s+(.+?)\s*$", candidate)
                    if item:
                        items.append(parse_scalar(item.group(1)))
                        lookahead += 1
                        continue
                    if not candidate.strip() or candidate.lstrip().startswith("#"):
                        lookahead += 1
                        continue
                    break
                result[key] = items
                index = lookahead - 1
        else:
            result[key] = parse_scalar(value)
        index += 1
    return result


def validate_agent_text(text: str, expected_name: Optional[str] = None, published: bool = False) -> Dict[str, object]:
    metadata = parse_frontmatter(text)
    for key in ("name", "description", "color", "tools"):
        if key not in metadata:
            raise PackError("missing required frontmatter key: {}".format(key))
    name = metadata["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise PackError("name must use lowercase letters, digits, and single hyphens")
    if expected_name is not None and name != expected_name:
        raise PackError("filename/name mismatch: {} != {}".format(expected_name, name))
    if not isinstance(metadata["description"], str) or not metadata["description"].strip():
        raise PackError("description must be a non-empty string")
    if metadata["color"] not in COLORS:
        raise PackError("invalid color: {}".format(metadata["color"]))
    if not isinstance(metadata["tools"], list) or not metadata["tools"]:
        raise PackError("tools must be a non-empty list")
    invalid_tools = [tool for tool in metadata["tools"] if tool not in TOOLS]
    if invalid_tools:
        raise PackError("invalid tools: {}".format(", ".join(invalid_tools)))
    if published:
        forbidden = sorted(FORBIDDEN_PUBLISHED_KEYS.intersection(metadata))
        if forbidden:
            raise PackError("published agent contains local-only keys: {}".format(", ".join(forbidden)))
        if PUBLISHED_MODEL_NAME_RE.search(metadata["description"]):
            raise PackError("published description contains a concrete model or vendor name")
    model = metadata.get("model")
    if model is not None and (not isinstance(model, str) or not model.startswith("custom:")):
        raise PackError("model must be a custom: model identifier")
    thought = metadata.get("thoughtLevel")
    if thought is not None and (not isinstance(thought, str) or not thought):
        raise PackError("thoughtLevel must be a non-empty string")
    return metadata


def render_agent(source_text: str, model_fields: Dict[str, str]) -> str:
    frontmatter, body = frontmatter_parts(source_text)
    filtered: List[str] = []
    for line in frontmatter:
        match = KEY_RE.match(line.rstrip("\r\n")) if line and not line[:1].isspace() else None
        if match and match.group(1) in {"model", "thoughtLevel"}:
            continue
        filtered.append(line)
    for key in ("model", "thoughtLevel"):
        if key in model_fields:
            filtered.append("{}: {}\n".format(key, json.dumps(model_fields[key], ensure_ascii=False)))
    rendered = "---\n" + "".join(filtered) + "---\n" + "".join(body)
    validate_agent_text(rendered)
    return rendered


def read_json_document(path: Path, label: str) -> object:
    try:
        data = path.expanduser().read_bytes()
    except (OSError, RuntimeError) as exc:
        raise PackError("cannot read {}: {}".format(label, exc))
    try:
        return json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackError("cannot read {}: {}".format(label, exc))


def read_model_map(
    path: Optional[str],
    known_names: List[str],
    inventory_path: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    if not path:
        return {}
    value = read_json_document(Path(path), "model map")
    if not isinstance(value, dict):
        raise PackError("model map must be a JSON object")
    unknown = sorted(set(value) - set(known_names))
    if unknown:
        raise PackError("model map contains unknown agents: {}".format(", ".join(unknown)))

    available_models: Dict[Tuple[str, str], set] = {}
    if inventory_path:
        inventory = read_json_document(Path(inventory_path), "model inventory")
        if not isinstance(inventory, dict):
            raise PackError("model inventory must be a JSON object")
        schema_version = inventory.get("schema_version")
        if type(schema_version) is not int or schema_version != 1 or inventory.get("generator") != "tony-agents-pack/model_inventory":
            raise PackError("model inventory has an unsupported schema or generator")
        if inventory.get("verification") != "DECLARED_UNVERIFIED":
            raise PackError("model inventory verification must be DECLARED_UNVERIFIED")
        providers = inventory.get("providers")
        if not isinstance(providers, list):
            raise PackError("model inventory must contain a providers array")
        for provider in providers:
            if not isinstance(provider, dict) or provider.get("enabled") is not True:
                continue
            provider_id = provider.get("id")
            models = provider.get("models")
            if not isinstance(provider_id, str) or not isinstance(models, list):
                continue
            for model_entry in models:
                if not isinstance(model_entry, dict) or not isinstance(model_entry.get("name"), str):
                    continue
                reasoning = model_entry.get("reasoning")
                variants = reasoning.get("variants", []) if isinstance(reasoning, dict) else []
                available_models[(provider_id, model_entry["name"])] = set(
                    item for item in variants if isinstance(item, str)
                )

    result: Dict[str, Dict[str, str]] = {}
    for name, mapping in value.items():
        if not isinstance(mapping, dict) or set(mapping) - {"model", "thoughtLevel"}:
            raise PackError("model map entry for {} has invalid fields".format(name))
        model = mapping.get("model")
        thought = mapping.get("thoughtLevel")
        if not isinstance(model, str) or not model.startswith("custom:"):
            raise PackError("model map entry for {} requires a custom: model".format(name))
        if thought is not None and (not isinstance(thought, str) or not thought):
            raise PackError("thoughtLevel for {} must be a non-empty string".format(name))
        if inventory_path:
            parts = model.split(":", 2)
            if len(parts) != 3 or not parts[1] or not parts[2]:
                raise PackError("model map entry for {} has an invalid custom: model".format(name))
            variants = available_models.get((parts[1], parts[2]))
            if variants is None:
                raise PackError("model map entry for {} is not in the enabled inventory".format(name))
            if thought is not None and thought not in variants:
                raise PackError("thoughtLevel for {} is not declared by the model inventory".format(name))
        result[name] = {"model": model}
        if thought is not None:
            result[name]["thoughtLevel"] = thought
    return result


def source_agents() -> Dict[str, Path]:
    return {path.stem: path for path in sorted(AGENTS_DIR.glob("*.md"))}


def normalize_name_options(values: Optional[List[str]]) -> List[str]:
    names: List[str] = []
    for value in values or []:
        for item in value.split(","):
            name = item.strip()
            if not name:
                continue
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                raise PackError("invalid agent name: {}".format(name))
            if name not in names:
                names.append(name)
    return names


def select_source_agents(
    agents: Dict[str, Path],
    only: Optional[List[str]] = None,
    skip: Optional[List[str]] = None,
) -> Dict[str, Path]:
    only_names = set(normalize_name_options(only))
    skip_names = set(normalize_name_options(skip))
    if only is not None and not only_names:
        raise PackError("agent selection is empty")
    if skip is not None and not skip_names:
        raise PackError("skip selection is empty")
    known = set(agents)
    unknown = sorted((only_names | skip_names) - known)
    if unknown:
        raise PackError("unknown agent selection: {}".format(", ".join(unknown)))
    selected = only_names if only_names else known
    selected -= skip_names
    return {name: agents[name] for name in sorted(selected)}


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def normalize_records_for_state(records: Dict[str, dict]) -> Dict[str, dict]:
    normalized: Dict[str, dict] = {}
    for name, original in records.items():
        record = dict(original)
        base_value = record.get("base_path")
        if not isinstance(base_value, str):
            raise PackError("cannot migrate state without a valid base_path for {}".format(name))
        base_data = read_optional_regular_bytes(Path(base_value), "state base", PackError)
        if base_data is None:
            raise PackError("cannot migrate state without a valid base_path for {}".format(name))
        record["base_sha"] = sha256_bytes(base_data)
        normalized[name] = record
    return normalized


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False


def path_key(path: Path) -> str:
    return os.path.abspath(os.fspath(path.expanduser()))


def target_preimage_sha(path: Path) -> Optional[str]:
    data = read_optional_regular_bytes(path, "target")
    return sha256_bytes(data) if data is not None else None


def read_plugin() -> dict:
    try:
        value = json.loads(PLUGIN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackError("cannot read plugin manifest: {}".format(exc))
    if not isinstance(value, dict):
        raise PackError("plugin manifest must be an object")
    return value


def package_version() -> str:
    version = read_plugin().get("version")
    if not isinstance(version, str):
        raise PackError("plugin version is missing")
    return version


def validate_package(verbose: bool = True) -> bool:
    errors: List[str] = []
    required_files = (
        ".zcode-plugin/plugin.json",
        ".github/workflows/validate.yml",
        "LICENSE",
        "README.md",
        "MODEL_SETUP.md",
        "INSTALL-FOR-AI.md",
        "CHANGELOG.md",
        "scripts/manage.py",
        "scripts/model_inventory.py",
        "scripts/install.sh",
        "scripts/install.ps1",
        "scripts/release.sh",
        "scripts/release_gate.py",
        "scripts/setup-hooks.sh",
        ".githooks/pre-push",
        "release-audits/README.md",
        "tests/test_manage.py",
        "tests/test_model_inventory.py",
        "tests/test_release_gate.py",
        *RELEASE_PNGS,
    )
    for relative in required_files:
        if not (ROOT / relative).is_file():
            errors.append("missing required release file: {}".format(relative))
    agents = source_agents()
    if len(agents) != EXPECTED_AGENT_COUNT:
        errors.append("expected exactly {} agents, found {}".format(EXPECTED_AGENT_COUNT, len(agents)))
    for name, path in agents.items():
        try:
            text = path.read_text(encoding="utf-8")
            metadata = validate_agent_text(text, name, published=True)
            if "# 模型需求：" not in text:
                raise PackError("missing model requirement comment")
            if not INJECTION_DEFENSE_RE.search(text):
                raise PackError("missing prompt-injection defense")
            if name in ACCEPTANCE_AGENTS:
                report_marker = (
                    "report-id / role / requirement-version / snapshot(commit|source|artifact SHA|build-id) / generated-at"
                    if name == "shencha-content"
                    else COMMON_ACCEPTANCE_MARKER
                )
                for marker in ("PASS", "BLOCK", "INCONCLUSIVE", report_marker):
                    if marker not in text:
                        raise PackError("missing acceptance marker: {}".format(marker))
            if name in HARD_READ_ONLY_AGENTS:
                if HARD_READ_ONLY_FORBIDDEN_TOOLS.intersection(metadata["tools"]):
                    raise PackError("{} tools must be strictly read-only".format(name))
                if not HARD_READ_ONLY_FORBIDDEN_TOOLS.issubset(set(metadata.get("disallowedTools", []))):
                    raise PackError("{} disallowedTools must include Bash, Write, and Edit".format(name))
            if name == "dongcha":
                dongcha_tools = set(metadata["tools"])
                if {"Bash", "Edit"}.intersection(dongcha_tools):
                    raise PackError("dongcha tools must not include Bash or Edit")
                if "Write" not in dongcha_tools:
                    raise PackError("dongcha tools must include Write")
                _, dongcha_body = frontmatter_parts(text)
                claim_status_lines = re.findall(r"(?m)^claim_status:\s*(.+)$", "".join(dongcha_body))
                if any("PRODUCTION_ELIGIBLE" in line for line in claim_status_lines):
                    raise PackError("dongcha claim_status enum must not contain PRODUCTION_ELIGIBLE")
            if name == "github":
                for marker in (
                    "REPO_REVIEW",
                    "README_POLISH",
                    "RELEASE_GATE",
                    "RELEASE_NOTES",
                    "target_version",
                    "package_fingerprint",
                    "base_ref",
                    "target_ref",
                    "changed_files",
                    "removed_files",
                    "changed_agents",
                    "reviewer: github",
                    "| evidence-id | check | result | evidence |",
                    "| finding-id | severity | status | summary |",
                    "| improvement-id | user-value | evidence-ref |",
                    "| owner | action | status |",
                    "breaking_impact",
                    "## Scope",
                    "## Evidence",
                    "## Findings",
                    "## Agent Links",
                    "## Improvements",
                    "## Blockers",
                    "## Unverified",
                    "## Migration",
                    "## Hand-off",
                    "blob/v<target_version>",
                ):
                    if marker not in text:
                        raise PackError("missing github release-gate marker: {}".format(marker))
            role_markers = {
                "frontend": ("## 模式", "可访问性", "截图", "## 界面文案（微文案）", "[文案待确认"),
                "gonghao": (
                    "## 模式",
                    "G1 单篇",
                    "G2 系列",
                    "G3 周运营",
                    "G4 纯策略",
                    "## 平台规则与合规（公众号特有）",
                    "诱导分享",
                    "诱导关注",
                    "绝对化用语",
                    "原创声明",
                    "留言区",
                    "review_profiles=[editorial,social]",
                    "CONTENT_STATUS",
                    "FINAL_CONTENT",
                    "DRAFT_DO_NOT_PUBLISH",
                    "DRAFT_COMPLETE_NATIVE_REVIEW_REQUIRED",
                    "BLOCKED",
                ),
                "mermaid": ("永远只输出一个 `mermaid` 代码块", "`graph TD`", "`click`", "集合"),
                "shencha-content": (
                    "editorial",
                    "review_profiles",
                    "review_tier",
                    "## dongcha claim 复核",
                    "QUICK",
                    "STANDARD",
                    "HIGH_RISK",
                    "claim ledger",
                    "publication_decision",
                    "GO",
                    "NO_GO",
                    "OBSERVED",
                    "VERIFIED_EXTERNAL",
                    "READY_FOR_RETEST",
                    "PENDING_NATIVE_REVIEW",
                    "SEND_BLOCKED",
                    "READY_FOR_HUMAN_SEND_REVIEW",
                    "FINAL_CONTENT",
                    "FINAL_DRAFT",
                    "非法组合",
                    "绝不得 PASS",
                    'SHA256(snapshot-id + "|" + claim-id)',
                    "`claim_count>40`",
                    "Phase A 只输出完整总 claim index",
                    "零 finding 也不得豁免",
                    "无论长短、是否分批或是否有 finding",
                    "原审查实例不得在同一会话关闭",
                    "任一 P0/P1 未达 VERIFIED",
                ),
                "writer": (
                    "## dongcha 事实接口",
                    "usable_as_fact",
                ),
                "writer-pro": (
                    "## dongcha 事实接口",
                    "usable_as_fact",
                ),
                "shencha-final": (
                    "## dongcha 生产资格授予",
                    "production_verdict",
                    "PRODUCTION_ELIGIBLE",
                ),
                "outreach": (
                    "SEND_BLOCKED",
                    "## dongcha angle_draft 契约",
                    "claim_status<VERIFIED",
                    "production_verdict!=PRODUCTION_ELIGIBLE",
                    "usage_scope",
                    "REFUTED",
                ),
                "huoke": (
                    "## 证据分类",
                    "## dongcha ICP 验证",
                    "FIT=True",
                    "N1 边界反例",
                    "N2 匹配但不买",
                    "N3 与 >=E3 来源冲突",
                ),
                "seoer": (
                    "## dongcha 专项 verdict",
                    "SERP_CONFIRMED",
                    "SERP_ABSENT",
                    "SERP_INTENT",
                    "CANNIBALIZED",
                    "NO_CONFLICT",
                    "WINNABILITY_A-D",
                    "SERP_VALIDATED",
                    "SEARCH_VALIDATED",
                    "site_asset_inventory",
                ),
                "sheyun": (
                    "## dongcha 选题输入",
                    "public_discussion_safety",
                    "visual_evidence_type",
                    "hook_angle",
                    "interaction_trigger",
                    "lead_magnet",
                    "brand_risk",
                    "PRIVATE_FORBIDDEN",
                ),
                "dongcha": (
                    "VALIDATION_BACKLOG",
                    "claim_status",
                    "production_verdict",
                    "PRODUCTION_ELIGIBLE",
                    "E0_UNATTRIBUTED",
                    "E4_PRIMARY_OR_VERIFIABLE",
                    "SEARCH_VALIDATED",
                    "SERP_VALIDATED",
                    "QUERY_HYPOTHESIS",
                    "AI_PROMPT_VALIDATED",
                    "usable_as_fact",
                    "public_discussion_safety",
                    "origin_source_id",
                    "snapshot_id",
                    "只提议不授予",
                    "## 审查轮次上限",
                    "## claim_type 证据下限表",
                    "| claim_type | 允许 source_type 白名单 | 最小独立来源数 | 禁止替代 |",
                    "| `pain` |",
                    "| `need_jtbd` |",
                    "| `search_behavior` |",
                    "| `ai_prompt_behavior` |",
                    "| `buying_behavior` |",
                    "| `transaction` |",
                    "不得以改标 `need_jtbd` 绕过 `search_behavior`/`buying_behavior` 下限",
                    "=> claim_status=VERIFIED ∧ claim_type 证据下限满足 ∧ 无未决冲突 ∧ freshness 通过",
                    "不可信内容防线",
                ),
                "jiankong": ("pending",),
                "tijian": ("ACTIVE_SECURITY",),
                "coder-ds": ("MODE=PARALLEL_ALTERNATIVE", "MODE=OVERFLOW"),
            }
            for marker in role_markers.get(name, ()):
                if marker not in text:
                    raise PackError("missing role contract marker: {}".format(marker))
            if verbose:
                print("OK agent {}".format(path.name))
        except (OSError, UnicodeError, PackError) as exc:
            errors.append("{}: {}".format(path, exc))
    try:
        plugin = read_plugin()
        if plugin.get("name") != PACKAGE_NAME:
            errors.append("plugin name must be {}".format(PACKAGE_NAME))
        version = plugin.get("version")
        if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
            errors.append("plugin version must be valid SemVer")
        if plugin.get("agents") != "agents":
            errors.append("plugin agents must equal 'agents'")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        if isinstance(version, str) and not re.search(r"^## \[{}\]".format(re.escape(version)), changelog, re.MULTILINE):
            errors.append("CHANGELOG.md does not contain version {}".format(version))
    except (OSError, UnicodeError, PackError) as exc:
        errors.append("plugin/changelog validation failed: {}".format(exc))
    try:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
        for action, major in (("checkout", "v4"), ("setup-python", "v5"), ("upload-artifact", "v4")):
            pattern = r"actions/{}@[0-9a-f]{{40}}\s+#\s*{}\b".format(re.escape(action), major)
            if not re.search(pattern, workflow):
                errors.append("workflow action {} must use a full commit SHA with # {}".format(action, major))
        if "fetch-depth: 0" not in workflow:
            errors.append("workflow checkout must fetch full history and tags with fetch-depth: 0")
        if re.search(r"uses:\s+actions/[^@\s]+@v\d+\b", workflow):
            errors.append("workflow contains a floating official action major tag")
        if "find agents scripts tests .githooks release-audits docs" not in workflow:
            errors.append("workflow checksum generation must include the docs directory")
        if "MODEL_SETUP.md" not in workflow:
            errors.append("workflow checksum generation must include MODEL_SETUP.md")
    except (OSError, UnicodeError) as exc:
        errors.append("cannot validate workflow action pins: {}".format(exc))
    for relative in RELEASE_PNGS:
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
            try:
                validate_png_structure(data)
            except PackError as exc:
                errors.append("{} {}".format(relative, exc))
            if len(data) <= MIN_SCREENSHOT_BYTES:
                errors.append("{} must be larger than 10 KiB".format(relative))
        except OSError as exc:
            errors.append("cannot read {}: {}".format(relative, exc))
    for filename in ("README.md", "INSTALL-FOR-AI.md", "MODEL_SETUP.md"):
        try:
            text = (ROOT / filename).read_text(encoding="utf-8")
            if filename != "MODEL_SETUP.md" and "ZCode 专用" not in text and "ZCode-only" not in text:
                errors.append("{} must identify the package as ZCode 专用 or ZCode-only".format(filename))
            for forbidden in PUBLIC_DOC_FORBIDDEN_TEXT + ("<OWNER>",):
                if forbidden in text:
                    errors.append("{} contains forbidden text {}".format(filename, forbidden))
            if PUBLIC_SECRET_RE.search(text):
                errors.append("{} appears to contain a secret value".format(filename))
            direct_read_patterns = (
                r"(?im)^\s*(?:cat|less|more|head|tail)\s+[^\n]*config\.json",
                r"(?im)^\s*(?:请|让 AI|AI 应|AI 先|使用 Read|用 Read)[^\n]*(?:读取|读|Read|cat)[^\n]*config\.json",
            )
            if any(re.search(pattern, text) for pattern in direct_read_patterns):
                errors.append("{} instructs AI to read config.json directly".format(filename))
            if filename != "MODEL_SETUP.md" and ("scripts/model_inventory.py" not in text or "model-inventory" not in text):
                errors.append("{} does not document the sanitized model inventory helper".format(filename))
        except (OSError, UnicodeError) as exc:
            errors.append("cannot read {}: {}".format(filename, exc))
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            for forbidden in PUBLIC_DOC_FORBIDDEN_TEXT:
                if forbidden in text:
                    errors.append("{} contains forbidden text {}".format(relative.as_posix(), forbidden))
            if PUBLIC_SECRET_RE.search(text):
                errors.append("{} appears to contain a secret value".format(relative.as_posix()))
        except (OSError, UnicodeError) as exc:
            errors.append("cannot scan public Markdown {}: {}".format(relative.as_posix(), exc))
    try:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for marker in (
            'src="{}"'.format(QR_IMAGE),
            'href="{}"'.format(QR_IMAGE),
            QR_URL,
            'width="25%"',
            'alt="扫码入群"',
            "仓库内图片固定随版本审计",
            "MODEL_SETUP.md",
        ):
            if marker not in readme:
                errors.append("README.md is missing required community/model-guide marker: {}".format(marker))
    except (OSError, UnicodeError) as exc:
        errors.append("cannot validate README.md community/model-guide markers: {}".format(exc))
    try:
        guide = (ROOT / "MODEL_SETUP.md").read_text(encoding="utf-8")
        for vendor in MODEL_GUIDE_VENDORS:
            if vendor not in guide:
                errors.append("MODEL_SETUP.md is missing vendor keyword: {}".format(vendor))
        if not re.search(r"API Key[^\n]*(?:禁止|不要|不得)|(?:禁止|不要|不得)[^\n]*API Key", guide, re.IGNORECASE):
            errors.append("MODEL_SETUP.md is missing an API Key safety warning")
        if not re.search(r"核验日期[^\n]*\d{4}-\d{2}-\d{2}", guide):
            errors.append("MODEL_SETUP.md is missing a dated verification marker")
        if "http://" in guide:
            errors.append("MODEL_SETUP.md contains a non-HTTPS URL")
        for relative in MODEL_SCREENSHOTS:
            alt_pattern = r"!\[([^\]]*[\u4e00-\u9fff][^\]]*)\]\({}\)".format(re.escape(relative))
            if not re.search(alt_pattern, guide):
                errors.append("MODEL_SETUP.md must reference {} with Chinese alt text".format(relative))
    except (OSError, UnicodeError) as exc:
        errors.append("cannot validate MODEL_SETUP.md content: {}".format(exc))
    if errors:
        print("VALIDATION FAILED ({} error(s))".format(len(errors)), file=sys.stderr)
        for error in errors:
            print("ERROR {}".format(error), file=sys.stderr)
        return False
    print("VALIDATION OK: {} agents, plugin {} v{}".format(len(agents), PACKAGE_NAME, package_version()))
    return True


class Manager:
    def __init__(self, target_dir: Path):
        self.target_dir = target_dir.expanduser().resolve(strict=False)
        self.meta_dir = self.target_dir / ".tony-agents-pack"
        self.state_file = self.meta_dir / "state.json"
        self.snapshots_dir = self.meta_dir / "snapshots"
        self.bases_dir = self.meta_dir / "bases"
        self.backups_dir = self.meta_dir / "backups"
        self.lock_file = self.meta_dir / "operation.lock"
        self._active_snapshot: Optional[Path] = None
        self._active_writes: Dict[str, Optional[str]] = {}

    @contextmanager
    def operation_lock(self):
        _, meta_token = open_directory_no_symlinks(self.meta_dir)
        meta_fd = directory_fd_value(meta_token)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd: Optional[int] = None
        try:
            if os.name == "nt":
                fd = open_windows_lock_file(self.lock_file)
                current = None
            elif meta_fd is None:
                fd = os.open(str(self.lock_file), flags, 0o600)
                current = self.lock_file.lstat()
            else:
                fd = os.open(self.lock_file.name, flags, 0o600, dir_fd=meta_fd)
                current = os.stat(self.lock_file.name, dir_fd=meta_fd, follow_symlinks=False)
            handle = os.fdopen(fd, "r+b")
            fd = None
        except BaseException as exc:
            if fd is not None:
                os.close(fd)
            close_directory_token(meta_token)
            if isinstance(exc, OSError):
                raise DecisionRequired("cannot open operation lock safely: {}".format(exc))
            raise
        try:
            opened = os.fstat(handle.fileno())
            if current is not None and (
                not stat.S_ISREG(current.st_mode)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise DecisionRequired("operation lock changed while opening")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    unsupported = {errno.ENOLCK, errno.ENOSYS}
                    if hasattr(errno, "ENOTSUP"):
                        unsupported.add(errno.ENOTSUP)
                    if hasattr(errno, "EOPNOTSUPP"):
                        unsupported.add(errno.EOPNOTSUPP)
                    if exc.errno in unsupported:
                        raise DecisionRequired("filesystem does not support operation locking")
                    raise DecisionRequired("another package operation is already running")
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    unsupported = {errno.ENOLCK, errno.ENOSYS}
                    if hasattr(errno, "ENOTSUP"):
                        unsupported.add(errno.ENOTSUP)
                    if hasattr(errno, "EOPNOTSUPP"):
                        unsupported.add(errno.EOPNOTSUPP)
                    if exc.errno in unsupported:
                        raise DecisionRequired("filesystem does not support operation locking")
                    raise DecisionRequired("another package operation is already running")
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            close_directory_token(meta_token)
    def load_state(self, required: bool = False) -> Optional[dict]:
        state_data = read_optional_regular_bytes(self.state_file, "state.json", PackError)
        if state_data is None:
            if required:
                raise PackError("no installed package state found in {}".format(self.state_file))
            return None
        try:
            state = json.loads(state_data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PackError("cannot read state: {}".format(exc))
        if not isinstance(state, dict) or not isinstance(state.get("files"), dict):
            raise PackError("state.json has an invalid structure")
        if state.get("package") != PACKAGE_NAME:
            raise PackError("state.json belongs to a different package")
        schema_version = state.get("schema_version", 1)
        if type(schema_version) is not int or schema_version not in (1, STATE_SCHEMA_VERSION):
            raise PackError("state.json has an unsupported schema version")
        selected_agents = state.get("selected_agents")
        if selected_agents is not None:
            if (
                not isinstance(selected_agents, list)
                or not all(
                    isinstance(name, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                    for name in selected_agents
                )
                or len(selected_agents) != len(set(selected_agents))
            ):
                raise PackError("state.json has an invalid selected_agents value")
        for name, record in state["files"].items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                raise PackError("state.json has an invalid agent name")
            if not isinstance(record, dict):
                raise PackError("state.json has an invalid file record")
            installed_sha = record.get("installed_sha")
            source_sha = record.get("source_sha")
            if not isinstance(installed_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", installed_sha):
                raise PackError("state.json has an invalid installed_sha")
            if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
                raise PackError("state.json has an invalid source_sha")
            for field, root in (("backup_path", self.backups_dir), ("base_path", self.bases_dir)):
                value = record.get(field)
                if value is not None and (not isinstance(value, str) or not path_is_within(Path(value), root)):
                    raise PackError("state.json {} escapes the package metadata directory".format(field))
            base_value = record.get("base_path")
            if not isinstance(base_value, str):
                raise PackError("state.json base_path is missing")
            base_data = read_optional_regular_bytes(Path(base_value), "state base", PackError)
            if base_data is None:
                raise PackError("state.json base_path is missing")
            base_sha = record.get("base_sha")
            if schema_version == STATE_SCHEMA_VERSION:
                if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", base_sha):
                    raise PackError("state.json has an invalid base_sha")
                if sha256_bytes(base_data) != base_sha:
                    raise PackError("state.json base content does not match base_sha")
            else:
                try:
                    validate_agent_text(base_data.decode("utf-8"), name)
                except (UnicodeError, PackError) as exc:
                    raise PackError("legacy state base is invalid: {}".format(exc))
        return state

    def _scan_summary(self) -> dict:
        state = self.load_state()
        tracked = state["files"] if state is not None else {}
        package_agents = source_agents()
        entries = []
        if self.target_dir.is_dir():
            for path in sorted(self.target_dir.glob("*.md")):
                name = path.stem
                try:
                    sha = target_preimage_sha(path)
                except DecisionRequired:
                    status = "SPECIAL_UNMANAGED"
                    sha = None
                else:
                    tracked_record = tracked.get(name)
                    if isinstance(tracked_record, dict):
                        installed_sha = tracked_record.get("installed_sha")
                        status = "TRACKED_CLEAN" if installed_sha == sha else "TRACKED_MODIFIED"
                    elif name in package_agents:
                        status = "COLLISION_UNMANAGED"
                    else:
                        status = "FOREIGN"
                entries.append({"name": name, "path": str(path), "sha256": sha, "status": status})
        return {
            "target_dir": str(self.target_dir),
            "state": "MANAGED" if state is not None else "ABSENT",
            "package_agents": sorted(package_agents),
            "entries": entries,
        }

    def scan(self, json_output: bool = False) -> dict:
        summary = self._scan_summary()
        if json_output:
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        else:
            print(
                "Agent inventory: {} existing, {} package candidates".format(
                    len(summary["entries"]), len(summary["package_agents"])
                )
            )
            for entry in summary["entries"]:
                print("{} {}".format(entry["status"], entry["path"]))
        return summary

    def write_target(self, path: Path, data: bytes, expected_sha: Optional[str]) -> None:
        conditional_atomic_write(path, data, expected_sha)
        self._active_writes[path_key(path)] = sha256_bytes(data)

    def remove_target(self, path: Path, expected_sha: str) -> None:
        parent, directory_token = open_directory_no_symlinks(path.parent)
        directory_fd = directory_fd_value(directory_token)
        try:
            claimed = claim_expected_path(path, expected_sha, parent, directory_token)
            unlink_path(claimed, directory_fd)
            fsync_directory(parent, directory_token)
        finally:
            close_directory_token(directory_token)
        self._active_writes[path_key(path)] = None

    def write_state(self, state: dict, expected_sha: Optional[str]) -> None:
        data = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        conditional_atomic_write(self.state_file, data, expected_sha)
        self._active_writes[path_key(self.state_file)] = sha256_bytes(data)

    def rollback_owns_current_path(self, path: Path) -> Optional[bool]:
        if self._active_snapshot is None:
            return True
        key = path_key(path)
        if key not in self._active_writes:
            return None
        post_sha = self._active_writes[key]
        try:
            current_sha = target_preimage_sha(path)
        except DecisionRequired:
            return False
        return current_sha == post_sha

    def create_snapshot(self, names: List[str], operation: str) -> Path:
        snapshot = self.snapshots_dir / unique_id()
        files_dir = snapshot / "files"
        captured_files: Dict[str, bytes] = {}
        manifest = {
            "created_at": now_iso(),
            "operation": operation,
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "files": {},
            "state_existed": False,
            "state_sha256": None,
        }
        for name in sorted(names):
            target = self.target_dir / (name + ".md")
            target_data = read_optional_regular_bytes(target, "snapshot target")
            existed = target_data is not None
            manifest["files"][name] = {
                "existed": existed,
                "sha256": sha256_bytes(target_data) if target_data is not None else None,
            }
            if target_data is not None:
                captured_files[name] = target_data

        state_data = read_optional_regular_bytes(self.state_file, "state.json")
        if state_data is not None:
            manifest["state_existed"] = True
            manifest["state_sha256"] = sha256_bytes(state_data)

        for name, data in captured_files.items():
            atomic_write(files_dir / (name + ".md"), data)
        if state_data is not None:
            atomic_write(snapshot / "state.json", state_data)
        atomic_json(snapshot / "snapshot.json", manifest)
        print("Snapshot: {}".format(snapshot))
        return snapshot

    def install_plan(
        self,
        model_map_path: Optional[str],
        force: bool = False,
        only: Optional[List[str]] = None,
        skip: Optional[List[str]] = None,
        overwrite: Optional[List[str]] = None,
        keep: Optional[List[str]] = None,
        inventory_path: Optional[str] = None,
        allow_unverified_model_map: bool = False,
    ) -> Tuple[dict, Dict[str, Path], Dict[str, Dict[str, str]]]:
        previous_state = self.load_state()
        if previous_state is not None and not force:
            raise PackError("package state already exists; use update instead")
        if force and previous_state is not None and (only or skip):
            raise PackError("force install cannot change the selected agent set; use update --add/--remove first")
        if force and previous_state is not None:
            existing_selection = previous_state.get("selected_agents") or sorted(previous_state["files"])
            existing_selection = sorted(set(existing_selection) & set(source_agents()))
            if not existing_selection:
                raise PackError("force install has no selected agents remaining in the package")
            only = [",".join(existing_selection)]
            skip = None
        agents = select_source_agents(source_agents(), only, skip)
        if not agents:
            raise PackError("agent selection is empty")
        if model_map_path and not inventory_path and not allow_unverified_model_map:
            raise DecisionRequired(
                "model-map requires --inventory; use --allow-unverified-model-map only after explicit user approval"
            )
        model_map = read_model_map(model_map_path, list(agents), inventory_path)
        extra_mapping = sorted(set(model_map) - set(agents))
        if extra_mapping:
            raise PackError("model map contains agents outside the selected install set: {}".format(", ".join(extra_mapping)))

        overwrite_names = set(normalize_name_options(overwrite))
        keep_names = set(normalize_name_options(keep))
        selected_names = set(agents)
        unknown_decisions = sorted((overwrite_names | keep_names) - selected_names)
        if unknown_decisions:
            raise PackError("conflict decision contains unselected agents: {}".format(", ".join(unknown_decisions)))
        if overwrite_names & keep_names:
            raise PackError("an agent cannot be both overwrite and keep")
        if previous_state is not None:
            managed_decisions = sorted((overwrite_names | keep_names) & set(previous_state["files"]))
            if managed_decisions:
                raise PackError(
                    "overwrite/keep apply only to unmanaged collisions: {}".format(
                        ", ".join(managed_decisions)
                    )
                )
        nonexistent_decisions = sorted(
            name
            for name in overwrite_names | keep_names
            if not (self.target_dir / (name + ".md")).exists()
        )
        if nonexistent_decisions:
            raise PackError(
                "overwrite/keep require an existing unmanaged collision: {}".format(
                    ", ".join(nonexistent_decisions)
                )
            )

        entries = []
        unresolved = []
        for name, source in agents.items():
            target = self.target_dir / source.name
            previous_record = previous_state["files"].get(name) if previous_state is not None else None
            target_sha = target_preimage_sha(target)
            if previous_record is not None:
                action = "MANAGED_REINSTALL"
            elif target_sha is None:
                action = "INSTALL"
            elif name in overwrite_names:
                action = "OVERWRITE_BACKUP"
            elif name in keep_names:
                action = "KEEP_EXISTING"
            else:
                action = "DECISION_REQUIRED"
                unresolved.append(name)
            fields = model_map.get(name, {})
            entries.append(
                {
                    "name": name,
                    "action": action,
                    "source_sha": sha256_file(source),
                    "target_sha": target_sha,
                    "model": fields.get("model"),
                    "thoughtLevel": fields.get("thoughtLevel"),
                }
            )

        model_status = "DEFAULT_MODEL"
        if model_map:
            model_status = "DECLARED_UNVERIFIED" if inventory_path else "UNVERIFIED_USER_ACCEPTED"
        plan = {
            "operation": "force-install" if previous_state is not None else "install",
            "package": PACKAGE_NAME,
            "version": package_version(),
            "target_dir": str(self.target_dir),
            "state_sha": target_preimage_sha(self.state_file),
            "model_status": model_status,
            "model_map_sha": sha256_file(Path(model_map_path).expanduser()) if model_map_path else None,
            "inventory_sha": sha256_file(Path(inventory_path).expanduser()) if inventory_path else None,
            "existing_agents": self._scan_summary()["entries"],
            "entries": entries,
            "unresolved": sorted(unresolved),
        }
        plan["digest"] = canonical_digest(plan)
        return plan, agents, model_map

    def print_install_plan(self, plan: dict) -> None:
        print(
            "{} plan: {} selected agent(s), {} unresolved conflict(s), model status {}".format(
                "Force install" if plan["operation"] == "force-install" else "Install",
                len(plan["entries"]),
                len(plan["unresolved"]),
                plan["model_status"],
            )
        )
        foreign_count = sum(1 for entry in plan["existing_agents"] if entry["status"] == "FOREIGN")
        if foreign_count:
            print("FOREIGN agents preserved: {}".format(foreign_count))
        for entry in plan["entries"]:
            print(
                "{} {} model={} thoughtLevel={} status={}".format(
                    entry["action"],
                    self.target_dir / (entry["name"] + ".md"),
                    entry["model"] or "<default>",
                    entry["thoughtLevel"] or "<omitted>",
                    plan["model_status"],
                )
            )
        print("PLAN_DIGEST {}".format(plan["digest"]))

    @transactional("install")
    def install(
        self,
        dry_run: bool,
        model_map_path: Optional[str],
        force: bool = False,
        only: Optional[List[str]] = None,
        skip: Optional[List[str]] = None,
        overwrite: Optional[List[str]] = None,
        keep: Optional[List[str]] = None,
        inventory_path: Optional[str] = None,
        confirm_plan: Optional[str] = None,
        allow_unverified_model_map: bool = False,
    ) -> None:
        previous_state = self.load_state()
        plan, agents, model_map = self.install_plan(
            model_map_path,
            force=force,
            only=only,
            skip=skip,
            overwrite=overwrite,
            keep=keep,
            inventory_path=inventory_path,
            allow_unverified_model_map=allow_unverified_model_map,
        )
        self.print_install_plan(plan)
        if dry_run:
            print("DRY-RUN: no files changed")
            if plan["unresolved"]:
                raise DecisionRequired(
                    "unmanaged collisions require --overwrite or --keep: {}".format(
                        ", ".join(plan["unresolved"])
                    )
                )
            return
        if plan["unresolved"]:
            raise DecisionRequired(
                "unmanaged collisions require --overwrite or --keep: {}".format(", ".join(plan["unresolved"]))
            )
        requires_confirmation = True
        if requires_confirmation and confirm_plan != plan["digest"]:
            raise DecisionRequired("install requires --confirm-plan {}".format(plan["digest"]))

        self.target_dir.mkdir(parents=True, exist_ok=True)
        operation_id = unique_id()
        if previous_state is not None:
            snapshot_names = sorted(set(agents) | set(previous_state["files"]))
            self._active_snapshot = self.create_snapshot(snapshot_names, "force-install")
        else:
            self._active_snapshot = self.create_snapshot(list(agents), "install")
        records: Dict[str, dict] = {}
        actions = {entry["name"]: entry for entry in plan["entries"]}
        for name, source in agents.items():
            entry = actions[name]
            action = entry["action"]
            if action == "KEEP_EXISTING":
                print("Kept unmanaged {}".format(self.target_dir / source.name))
                continue
            source_bytes = source.read_bytes()
            if sha256_bytes(source_bytes) != entry["source_sha"]:
                raise DecisionRequired("source changed after plan confirmation: {}".format(source))
            source_text = source_bytes.decode("utf-8")
            target = self.target_dir / source.name
            current_target_sha = target_preimage_sha(target)
            if current_target_sha != entry["target_sha"]:
                raise DecisionRequired("target changed after plan confirmation: {}".format(target))
            previous_record = previous_state["files"].get(name) if previous_state is not None else None
            preexisting = current_target_sha is not None and previous_record is None
            backup_path: Optional[Path] = None
            previous_backup_data: Optional[bytes] = None
            if isinstance(previous_record, dict):
                backup_value = previous_record.get("backup_path")
                if isinstance(backup_value, str) and backup_value:
                    previous_backup_data = read_optional_regular_bytes(
                        Path(backup_value),
                        "state backup",
                        PackError,
                    )
            if previous_backup_data is not None:
                backup_path = self.backups_dir / operation_id / source.name
                atomic_write(backup_path, previous_backup_data)
                preexisting = True
            elif preexisting:
                target_bytes = read_required_regular_bytes(target, "install target", DecisionRequired)
                if sha256_bytes(target_bytes) != current_target_sha:
                    raise DecisionRequired("target changed before backup: {}".format(target))
                backup_path = self.backups_dir / operation_id / source.name
                atomic_write(backup_path, target_bytes)
            rendered = render_agent(source_text, model_map.get(name, {}))
            rendered_bytes = rendered.encode("utf-8")
            rendered_sha = sha256_bytes(rendered_bytes)
            self.write_target(target, rendered_bytes, entry["target_sha"])
            installed_data = read_required_regular_bytes(target, "installed target", DecisionRequired)
            if sha256_bytes(installed_data) != rendered_sha:
                raise DecisionRequired("target changed after install write: {}".format(target))
            validate_agent_text(installed_data.decode("utf-8"), name)
            base_path = self.bases_dir / operation_id / source.name
            atomic_write(base_path, rendered_bytes)
            records[name] = {
                "source_sha": sha256_file(source),
                "installed_sha": rendered_sha,
                "preexisting": preexisting,
                "backup_path": str(backup_path) if backup_path else None,
                "base_path": str(base_path),
                "base_sha": sha256_file(base_path),
                "operation_time": now_iso(),
            }
            print("Installed {}".format(target))
        state = {
            "package": PACKAGE_NAME,
            "schema_version": STATE_SCHEMA_VERSION,
            "version": package_version(),
            "source": str(ROOT),
            "operation_time": now_iso(),
            "selected_agents": sorted(records),
            "files": records,
        }
        self.write_state(state, plan["state_sha"])
        print("Install complete: {}".format(self.state_file))

    @transactional("update")
    def update(
        self,
        dry_run: bool,
        model_map_path: Optional[str],
        inventory_path: Optional[str] = None,
        allow_unverified_model_map: bool = False,
        confirm_plan: Optional[str] = None,
        add: Optional[List[str]] = None,
        remove: Optional[List[str]] = None,
        overwrite: Optional[List[str]] = None,
        keep: Optional[List[str]] = None,
    ) -> None:
        state = self.load_state(required=True)
        assert state is not None
        all_agents = source_agents()
        configured_selection = state.get("selected_agents")
        if configured_selection is None:
            selected_names = set(state["files"])
        elif isinstance(configured_selection, list) and all(isinstance(name, str) for name in configured_selection):
            selected_names = set(configured_selection)
        else:
            raise PackError("state.json has an invalid selected_agents value")
        add_names = set(normalize_name_options(add))
        remove_names = set(normalize_name_options(remove))
        overwrite_names = set(normalize_name_options(overwrite))
        keep_names = set(normalize_name_options(keep))
        for label, values, names in (
            ("add", add, add_names),
            ("remove", remove, remove_names),
            ("overwrite", overwrite, overwrite_names),
            ("keep", keep, keep_names),
        ):
            if values is not None and not names:
                raise PackError("{} selection is empty".format(label))
        unknown = sorted((add_names | remove_names | overwrite_names | keep_names) - set(all_agents))
        if unknown:
            raise PackError("update selection contains unknown agents: {}".format(", ".join(unknown)))
        if add_names & remove_names:
            raise PackError("an agent cannot be both added and removed")
        if overwrite_names & keep_names:
            raise PackError("an agent cannot be both overwrite and keep")
        if (overwrite_names | keep_names) - add_names:
            raise PackError("update conflict decisions apply only to explicitly added agents")
        managed_decisions = sorted((overwrite_names | keep_names) & set(state["files"]))
        if managed_decisions:
            raise PackError(
                "overwrite/keep apply only to unmanaged added collisions: {}".format(
                    ", ".join(managed_decisions)
                )
            )
        nonexistent_decisions = sorted(
            name
            for name in overwrite_names | keep_names
            if not (self.target_dir / (name + ".md")).exists()
        )
        if nonexistent_decisions:
            raise PackError(
                "overwrite/keep require an existing unmanaged added collision: {}".format(
                    ", ".join(nonexistent_decisions)
                )
            )
        selected_names |= add_names
        selected_names -= remove_names
        selected_names -= keep_names
        agents = {name: all_agents[name] for name in sorted(selected_names & set(all_agents))}
        available_not_selected = sorted(set(all_agents) - selected_names - remove_names - set(state["files"]))
        source_names = set(agents)
        state_names = set(state["files"])
        added = sorted(source_names - state_names)
        common = sorted(source_names & state_names)
        removed = sorted(state_names - source_names)
        if model_map_path and not inventory_path and not allow_unverified_model_map:
            raise DecisionRequired(
                "model-map requires --inventory; use --allow-unverified-model-map only after explicit user approval"
            )
        model_map = read_model_map(model_map_path, sorted(source_names), inventory_path)
        changed = []
        for name in common:
            record = state["files"][name]
            target = self.target_dir / (name + ".md")
            target_sha = target_preimage_sha(target)
            if target_sha is None or target_sha != record.get("installed_sha"):
                changed.append(name)
        removed_modified = []
        for name in removed:
            record = state["files"][name]
            target = self.target_dir / (name + ".md")
            target_sha = target_preimage_sha(target)
            if target_sha is None or target_sha != record.get("installed_sha"):
                removed_modified.append(name)
        added_collisions = sorted(
            name for name in added if (self.target_dir / (name + ".md")).exists() and name not in overwrite_names
        )
        print(
            "Update plan: {} selected source agents, {} available but not selected, {} added, {} removed, {} locally modified/missing, {} unresolved added collision(s)".format(
                len(agents),
                len(available_not_selected),
                len(added),
                len(removed),
                len(changed) + len(removed_modified),
                len(added_collisions),
            )
        )
        for name in available_not_selected:
            print("AVAILABLE NOT SELECTED {}".format(name))
        for name in added:
            target = self.target_dir / (name + ".md")
            if name in added_collisions:
                suffix = " (unmanaged collision; choose --overwrite or --keep)"
            elif target.exists():
                suffix = " (will be backed up before explicit overwrite)"
            else:
                suffix = ""
            print("ADDED {}{}".format(name, suffix))
        for name in removed:
            suffix = " (locally modified/missing; will remain tracked)" if name in removed_modified else ""
            print("REMOVED {}{}".format(name, suffix))
        for name in changed:
            print("LOCAL CHANGE {} (three-way merge required)".format(name))
        for name in removed_modified:
            print("LOCAL CHANGE {} (removed from package; will remain tracked)".format(name))
        update_model_status = "PRESERVE_EXISTING"
        if model_map:
            update_model_status = "DECLARED_UNVERIFIED" if inventory_path else "UNVERIFIED_USER_ACCEPTED"
        for name in sorted(source_names):
            fields = model_map.get(name)
            print(
                "BINDING {} model={} thoughtLevel={} status={}".format(
                    name,
                    fields.get("model") if fields else "<preserve>",
                    fields.get("thoughtLevel", "<omitted>") if fields else "<preserve>",
                    update_model_status,
                )
            )
        target_shas = {
            name: target_preimage_sha(self.target_dir / (name + ".md"))
            for name in sorted(source_names | state_names)
        }
        plan = {
            "operation": "update",
            "package": PACKAGE_NAME,
            "version": package_version(),
            "target_dir": str(self.target_dir),
            "state_sha": target_preimage_sha(self.state_file),
            "source_shas": {name: sha256_file(path) for name, path in sorted(agents.items())},
            "target_shas": target_shas,
            "model_map_sha": sha256_file(Path(model_map_path).expanduser()) if model_map_path else None,
            "inventory_sha": sha256_file(Path(inventory_path).expanduser()) if inventory_path else None,
            "model_status": update_model_status,
            "selected_agents": sorted(selected_names),
            "available_not_selected": available_not_selected,
            "requested_add": sorted(add_names),
            "requested_remove": sorted(remove_names),
            "requested_overwrite": sorted(overwrite_names),
            "requested_keep": sorted(keep_names),
            "unresolved": added_collisions,
            "added": added,
            "removed": removed,
            "changed": changed,
            "removed_modified": removed_modified,
        }
        plan["digest"] = canonical_digest(plan)
        print("PLAN_DIGEST {}".format(plan["digest"]))
        if dry_run:
            print("DRY-RUN: no files changed")
            if added_collisions:
                raise DecisionRequired(
                    "unmanaged added collisions require --overwrite or --keep: {}".format(
                        ", ".join(added_collisions)
                    )
                )
            return plan
        if added_collisions:
            raise DecisionRequired(
                "unmanaged added collisions require --overwrite or --keep: {}".format(
                    ", ".join(added_collisions)
                )
            )
        if confirm_plan != plan["digest"]:
            raise DecisionRequired("update requires --confirm-plan {}".format(plan["digest"]))
        if target_preimage_sha(self.state_file) != plan["state_sha"]:
            raise DecisionRequired("state changed after plan confirmation")
        update_id = unique_id()
        self._active_snapshot = self.create_snapshot(sorted(source_names | state_names), "update")
        new_records: Dict[str, dict] = {}

        for name in added:
            source = agents[name]
            source_bytes = source.read_bytes()
            if sha256_bytes(source_bytes) != plan["source_shas"][name]:
                raise DecisionRequired("source changed after plan confirmation: {}".format(source))
            source_text = source_bytes.decode("utf-8")
            target = self.target_dir / source.name
            current_target_sha = target_preimage_sha(target)
            if current_target_sha != plan["target_shas"][name]:
                raise DecisionRequired("target changed after plan confirmation: {}".format(target))
            preexisting = current_target_sha is not None
            backup_path: Optional[Path] = None
            if preexisting:
                target_bytes = read_required_regular_bytes(target, "update target", DecisionRequired)
                if sha256_bytes(target_bytes) != current_target_sha:
                    raise DecisionRequired("target changed before backup: {}".format(target))
                backup_path = self.backups_dir / update_id / source.name
                atomic_write(backup_path, target_bytes)
            rendered = render_agent(source_text, model_map.get(name, {}))
            rendered_bytes = rendered.encode("utf-8")
            rendered_sha = sha256_bytes(rendered_bytes)
            self.write_target(target, rendered_bytes, plan["target_shas"][name])
            installed_data = read_required_regular_bytes(target, "installed target", DecisionRequired)
            if sha256_bytes(installed_data) != rendered_sha:
                raise DecisionRequired("target changed after update write: {}".format(target))
            validate_agent_text(installed_data.decode("utf-8"), name)
            base_path = self.bases_dir / update_id / source.name
            atomic_write(base_path, rendered_bytes)
            new_records[name] = {
                "source_sha": sha256_file(source),
                "installed_sha": rendered_sha,
                "preexisting": preexisting,
                "backup_path": str(backup_path) if backup_path else None,
                "base_path": str(base_path),
                "base_sha": sha256_file(base_path),
                "operation_time": now_iso(),
            }
            print("Added {}".format(target))

        for name in common:
            source = agents[name]
            source_bytes = source.read_bytes()
            if sha256_bytes(source_bytes) != plan["source_shas"][name]:
                raise DecisionRequired("source changed after plan confirmation: {}".format(source))
            source_text = source_bytes.decode("utf-8")
            record = dict(state["files"][name])
            target = self.target_dir / source.name
            current_target_sha = target_preimage_sha(target)
            if current_target_sha != plan["target_shas"][name]:
                raise DecisionRequired("target changed after plan confirmation: {}".format(target))
            current_data = (
                read_required_regular_bytes(target, "update target", DecisionRequired)
                if current_target_sha is not None
                else None
            )
            if current_data is not None and sha256_bytes(current_data) != current_target_sha:
                raise DecisionRequired("target changed before merge: {}".format(target))
            current_text = current_data.decode("utf-8") if current_data is not None else ""
            current_fields: Dict[str, str] = {}
            if current_text:
                try:
                    current_metadata = parse_frontmatter(current_text)
                    for key in ("model", "thoughtLevel"):
                        value = current_metadata.get(key)
                        if isinstance(value, str):
                            current_fields[key] = value
                except PackError:
                    pass
            base_value = record.get("base_path")
            base_data = (
                read_optional_regular_bytes(Path(base_value), "state base", PackError)
                if isinstance(base_value, str) and base_value
                else None
            )
            if not current_fields and base_data is not None:
                try:
                    base_metadata = parse_frontmatter(base_data.decode("utf-8"))
                    for key in ("model", "thoughtLevel"):
                        value = base_metadata.get(key)
                        if isinstance(value, str):
                            current_fields[key] = value
                except (UnicodeError, PackError):
                    pass
            fields = dict(model_map[name]) if name in model_map else current_fields
            remote = render_agent(source_text, fields)
            if current_target_sha is None:
                validate_agent_text(remote, name)
                remote_bytes = remote.encode("utf-8")
                remote_sha = sha256_bytes(remote_bytes)
                self.write_target(target, remote_bytes, plan["target_shas"][name])
                installed_data = read_required_regular_bytes(target, "installed target", DecisionRequired)
                if sha256_bytes(installed_data) != remote_sha:
                    raise DecisionRequired("target changed after update write: {}".format(target))
                base_path = self.bases_dir / update_id / source.name
                atomic_write(base_path, remote_bytes)
                record.update(
                    {
                        "source_sha": sha256_file(source),
                        "installed_sha": remote_sha,
                        "base_path": str(base_path),
                        "base_sha": sha256_file(base_path),
                        "operation_time": now_iso(),
                    }
                )
                new_records[name] = record
                print("Reinstalled missing {}".format(name))
                continue
            unmodified = current_target_sha == record.get("installed_sha")
            installed_text: Optional[str] = None
            if unmodified:
                installed_text = remote
            else:
                if current_target_sha is not None and base_data is not None:
                    installed_text = self.merge(base_data.decode("utf-8"), current_text, remote)
                if installed_text is None:
                    incoming = target.with_name(
                        target.name + ".tony-agents-pack.incoming." + unique_id()
                    )
                    conditional_atomic_write(incoming, remote.encode("utf-8"), None)
                    print("CONFLICT {} preserved; candidate {}".format(target, incoming))
                    new_records[name] = record
                    continue
            validate_agent_text(installed_text, name)
            installed_bytes = installed_text.encode("utf-8")
            installed_sha = sha256_bytes(installed_bytes)
            self.write_target(target, installed_bytes, plan["target_shas"][name])
            installed_data = read_required_regular_bytes(target, "installed target", DecisionRequired)
            if sha256_bytes(installed_data) != installed_sha:
                raise DecisionRequired("target changed after update write: {}".format(target))
            validate_agent_text(installed_data.decode("utf-8"), name)
            base_path = self.bases_dir / update_id / source.name
            atomic_write(base_path, remote.encode("utf-8"))
            record.update(
                {
                    "source_sha": sha256_file(source),
                    "installed_sha": installed_sha,
                    "base_path": str(base_path),
                    "base_sha": sha256_file(base_path),
                    "operation_time": now_iso(),
                }
            )
            new_records[name] = record
            suffix = " (merged local changes)" if not unmodified else ""
            print("Updated {}{}".format(target, suffix))

        for name in removed:
            record = dict(state["files"][name])
            target = self.target_dir / (name + ".md")
            current_matches = target_preimage_sha(target) == record.get("installed_sha")
            if not current_matches:
                new_records[name] = record
                print("REMOVED BUT MODIFIED/MISSING {} preserved and remains tracked".format(target))
                continue
            if record.get("preexisting"):
                backup_value = record.get("backup_path")
                backup_data = (
                    read_optional_regular_bytes(Path(backup_value), "state backup", PackError)
                    if isinstance(backup_value, str) and backup_value
                    else None
                )
                if backup_data is None:
                    new_records[name] = record
                    print("REMOVED BUT BACKUP MISSING {} preserved and remains tracked".format(target))
                    continue
                self.write_target(target, backup_data, record.get("installed_sha"))
                print("Removed package ownership and restored {}".format(target))
            else:
                self.remove_target(target, record.get("installed_sha"))
                print("Removed {}".format(target))

        new_records = normalize_records_for_state(new_records)
        state.update(
            {
                "version": package_version(),
                "schema_version": STATE_SCHEMA_VERSION,
                "source": str(ROOT),
                "operation_time": now_iso(),
                "selected_agents": sorted(source_names | set(removed_modified)),
                "files": new_records,
            }
        )
        self.write_state(state, plan["state_sha"])
        print("Update complete")

    @staticmethod
    def merge(base: str, local: str, remote: str) -> Optional[str]:
        git = shutil.which("git")
        if not git:
            return None
        with tempfile.TemporaryDirectory(prefix="tony-agents-merge-") as temporary:
            directory = Path(temporary)
            paths = {"local": directory / "local", "base": directory / "base", "remote": directory / "remote"}
            for key, value in (("local", local), ("base", base), ("remote", remote)):
                atomic_write(paths[key], value.encode("utf-8"))
            result = subprocess.run(
                [git, "merge-file", "-p", str(paths["local"]), str(paths["base"]), str(paths["remote"])],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            return result.stdout if result.returncode == 0 else None

    @transactional("uninstall")
    def uninstall(self, dry_run: bool) -> None:
        state = self.load_state(required=True)
        assert state is not None
        state_sha = target_preimage_sha(self.state_file)
        if state_sha is None:
            raise DecisionRequired("state changed after validation")
        names = sorted(state["files"])
        modified = []
        for name in names:
            target = self.target_dir / (name + ".md")
            record = state["files"][name]
            target_sha = target_preimage_sha(target)
            if target_sha is not None and target_sha != record.get("installed_sha"):
                modified.append(name)
        print("Uninstall plan: {} tracked agents, {} locally modified".format(len(names), len(modified)))
        if dry_run:
            print("DRY-RUN: no files changed")
            return
        self._active_snapshot = self.create_snapshot(names, "uninstall")
        remaining: Dict[str, dict] = {}
        for name in names:
            target = self.target_dir / (name + ".md")
            record = state["files"][name]
            current_matches = target_preimage_sha(target) == record.get("installed_sha")
            if not current_matches:
                backup_value = record.get("backup_path")
                backup_data = (
                    read_optional_regular_bytes(Path(backup_value), "state backup", PackError)
                    if record.get("preexisting") and isinstance(backup_value, str) and backup_value
                    else None
                )
                if backup_data is not None:
                    candidate = target.with_name(
                        target.name + ".tony-agents-pack.restore." + unique_id()
                    )
                    conditional_atomic_write(candidate, backup_data, None)
                    print("MODIFIED {} preserved; restore candidate {}".format(target, candidate))
                else:
                    print("MODIFIED/MISSING {} preserved".format(target))
                remaining[name] = record
                continue
            if record.get("preexisting"):
                backup_value = record.get("backup_path")
                backup_data = (
                    read_optional_regular_bytes(Path(backup_value), "state backup", PackError)
                    if isinstance(backup_value, str) and backup_value
                    else None
                )
                if backup_data is None:
                    print("MISSING BACKUP {} preserved".format(target))
                    remaining[name] = record
                    continue
                self.write_target(target, backup_data, record.get("installed_sha"))
                print("Restored {}".format(target))
            else:
                self.remove_target(target, record.get("installed_sha"))
                print("Removed {}".format(target))
        if remaining:
            remaining = normalize_records_for_state(remaining)
            state.update(
                {
                    "operation_time": now_iso(),
                    "schema_version": STATE_SCHEMA_VERSION,
                    "selected_agents": sorted(remaining),
                    "files": remaining,
                }
            )
            self.write_state(state, state_sha)
            print("Uninstall incomplete: {} modified item(s) remain tracked".format(len(remaining)))
        else:
            self.remove_target(self.state_file, state_sha)
            print("Uninstall complete")

    def read_snapshot(self, snapshot: Path) -> Tuple[dict, Dict[str, bytes], Optional[bytes], str]:
        snapshot = Path(os.path.abspath(os.fspath(snapshot)))
        manifest_file = snapshot / "snapshot.json"
        manifest_data = read_required_regular_bytes(manifest_file, "snapshot manifest")
        try:
            manifest = json.loads(manifest_data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PackError("cannot read snapshot manifest {}: {}".format(manifest_file, exc))
        if not isinstance(manifest, dict):
            raise PackError("snapshot manifest must be a JSON object")
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise PackError("snapshot manifest has invalid files data: {}".format(manifest_file))
        snapshot_schema = manifest.get("schema_version", 1)
        if type(snapshot_schema) is not int or snapshot_schema not in (1, SNAPSHOT_SCHEMA_VERSION):
            raise PackError("snapshot manifest has an unsupported schema version")

        saved_files: Dict[str, bytes] = {}
        for name, info in files.items():
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or not isinstance(info, dict)
                or not isinstance(info.get("existed"), bool)
            ):
                raise PackError("snapshot manifest has an invalid file entry")
            expected_snapshot_sha = info.get("sha256")
            if snapshot_schema == SNAPSHOT_SCHEMA_VERSION:
                if info["existed"]:
                    if not isinstance(expected_snapshot_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_snapshot_sha):
                        raise PackError("snapshot manifest has an invalid file sha256")
                elif expected_snapshot_sha is not None:
                    raise PackError("snapshot manifest has an unexpected file sha256")
            if info["existed"]:
                saved = snapshot / "files" / (name + ".md")
                saved_bytes = read_required_regular_bytes(saved, "snapshot file")
                if snapshot_schema == SNAPSHOT_SCHEMA_VERSION and sha256_bytes(saved_bytes) != expected_snapshot_sha:
                    raise PackError("snapshot file content does not match its manifest: {}".format(saved))
                saved_files[name] = saved_bytes

        state_existed = manifest.get("state_existed")
        if not isinstance(state_existed, bool):
            raise PackError("snapshot manifest has invalid state_existed data")
        expected_state_sha = manifest.get("state_sha256")
        if snapshot_schema == SNAPSHOT_SCHEMA_VERSION:
            if state_existed:
                if not isinstance(expected_state_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_state_sha):
                    raise PackError("snapshot manifest has an invalid state_sha256")
            elif expected_state_sha is not None:
                raise PackError("snapshot manifest has an unexpected state_sha256")

        state_data: Optional[bytes] = None
        if state_existed:
            saved_state = snapshot / "state.json"
            state_data = read_required_regular_bytes(saved_state, "snapshot state")
            if snapshot_schema == SNAPSHOT_SCHEMA_VERSION and sha256_bytes(state_data) != expected_state_sha:
                raise PackError("snapshot state content does not match its manifest: {}".format(saved_state))
        return manifest, saved_files, state_data, sha256_bytes(manifest_data)

    def write_rollback_candidate(self, path: Path, data: bytes) -> Path:
        candidate = path.with_name(
            path.name + ".tony-agents-pack.rollback." + unique_id()
        )
        conditional_atomic_write(candidate, data, None)
        return candidate

    def restore_snapshot(
        self,
        snapshot: Path,
        expected_preimages: Optional[Dict[str, Optional[str]]] = None,
        expected_plan_digest: Optional[str] = None,
    ) -> None:
        if expected_plan_digest is not None:
            current_plan, current_snapshot, current_preimages = self.rollback_plan(
                snapshot.name,
                planned_preimages=expected_preimages,
            )
            if (
                current_plan["digest"] != expected_plan_digest
                or path_key(current_snapshot) != path_key(snapshot)
                or current_preimages != expected_preimages
            ):
                raise DecisionRequired("rollback snapshot changed after confirmation; run dry-run again")
        manifest, saved_files, state_data, manifest_sha = self.read_snapshot(snapshot)
        if expected_plan_digest is not None:
            expected_entries = {entry["name"]: entry["snapshot_sha"] for entry in current_plan["entries"]}
            actual_entries = {
                name: sha256_bytes(data)
                for name, data in saved_files.items()
            }
            actual_entries.update(
                {
                    name: None
                    for name, info in manifest["files"].items()
                    if not info["existed"]
                }
            )
            actual_state_sha = sha256_bytes(state_data) if state_data is not None else None
            if (
                manifest_sha != current_plan["manifest_sha"]
                or actual_entries != expected_entries
                or actual_state_sha != current_plan["state_snapshot_sha"]
            ):
                raise DecisionRequired("rollback snapshot changed after plan confirmation; run dry-run again")
        files = manifest["files"]

        for name, info in files.items():
            target = self.target_dir / (name + ".md")
            if expected_preimages is not None:
                expected_sha = expected_preimages[path_key(target)]
                current_sha = target_preimage_sha(target)
                if current_sha != expected_sha:
                    candidate_data = saved_files.get(name)
                    if candidate_data is not None:
                        candidate = self.write_rollback_candidate(target, candidate_data)
                        print("ROLLBACK PRESERVED LATE CHANGE {}; candidate {}".format(target, candidate))
                    else:
                        print("ROLLBACK PRESERVED LATE CHANGE {}; snapshot would remove it".format(target))
                    raise DecisionRequired("rollback target changed after plan confirmation: {}".format(target))
                if info["existed"]:
                    try:
                        conditional_atomic_write(target, saved_files[name], expected_sha)
                    except DecisionRequired:
                        candidate = self.write_rollback_candidate(target, saved_files[name])
                        print("ROLLBACK PRESERVED LATE CHANGE {}; candidate {}".format(target, candidate))
                        raise
                    self._active_writes[path_key(target)] = sha256_bytes(saved_files[name])
                elif expected_sha is not None:
                    try:
                        self.remove_target(target, expected_sha)
                    except DecisionRequired:
                        print("ROLLBACK PRESERVED LATE CHANGE {}; snapshot would remove it".format(target))
                        raise
                continue

            ownership = self.rollback_owns_current_path(target)
            if ownership is None:
                continue
            if not ownership:
                print("ROLLBACK PRESERVED CONCURRENT CHANGE {}".format(target))
                continue
            current_sha = target_preimage_sha(target)
            if info["existed"]:
                try:
                    conditional_atomic_write(target, saved_files[name], current_sha)
                except DecisionRequired:
                    print("ROLLBACK PRESERVED CONCURRENT CHANGE {}".format(target))
            elif current_sha is not None:
                try:
                    self.remove_target(target, current_sha)
                except DecisionRequired:
                    print("ROLLBACK PRESERVED CONCURRENT CHANGE {}".format(target))

        if expected_preimages is not None:
            expected_state_sha = expected_preimages[path_key(self.state_file)]
            current_state_sha = target_preimage_sha(self.state_file)
            if current_state_sha != expected_state_sha:
                if state_data is not None:
                    candidate = self.write_rollback_candidate(self.state_file, state_data)
                    print("ROLLBACK PRESERVED LATE CHANGE {}; candidate {}".format(self.state_file, candidate))
                else:
                    print("ROLLBACK PRESERVED LATE CHANGE {}; snapshot would remove it".format(self.state_file))
                raise DecisionRequired("rollback state changed after plan confirmation")
            if state_data is not None:
                try:
                    conditional_atomic_write(self.state_file, state_data, expected_state_sha)
                except DecisionRequired:
                    candidate = self.write_rollback_candidate(self.state_file, state_data)
                    print("ROLLBACK PRESERVED LATE CHANGE {}; candidate {}".format(self.state_file, candidate))
                    raise
                self._active_writes[path_key(self.state_file)] = sha256_bytes(state_data)
            elif expected_state_sha is not None:
                try:
                    self.remove_target(self.state_file, expected_state_sha)
                except DecisionRequired:
                    print("ROLLBACK PRESERVED LATE CHANGE {}; snapshot would remove it".format(self.state_file))
                    raise
            return

        state_ownership = self.rollback_owns_current_path(self.state_file)
        if state_ownership is None:
            return
        if state_ownership:
            current_state_sha = target_preimage_sha(self.state_file)
            if state_data is not None:
                try:
                    conditional_atomic_write(self.state_file, state_data, current_state_sha)
                except DecisionRequired:
                    print("ROLLBACK PRESERVED CONCURRENT CHANGE {}".format(self.state_file))
            elif current_state_sha is not None:
                try:
                    self.remove_target(self.state_file, current_state_sha)
                except DecisionRequired:
                    print("ROLLBACK PRESERVED CONCURRENT CHANGE {}".format(self.state_file))
        elif self._active_writes:
            print("ROLLBACK PRESERVED CONCURRENT CHANGE {}".format(self.state_file))

    def resolve_snapshot(self, snapshot_id: str) -> Path:
        if snapshot_id != "latest" and not re.fullmatch(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z", snapshot_id):
            raise PackError("invalid snapshot id")
        try:
            snapshots_root, snapshots_token = open_directory_no_symlinks(
                self.snapshots_dir,
                create=False,
            )
        except FileNotFoundError:
            raise PackError("no snapshots found")
        snapshots_fd = directory_fd_value(snapshots_token)
        try:
            names = os.listdir(snapshots_fd) if snapshots_fd is not None else os.listdir(snapshots_root)
            valid_names = []
            for name in names:
                if not re.fullmatch(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z", name):
                    continue
                candidate = snapshots_root / name
                try:
                    candidate_root, candidate_token = open_directory_no_symlinks(candidate, create=False)
                except (FileNotFoundError, DecisionRequired):
                    continue
                candidate_fd = directory_fd_value(candidate_token)
                try:
                    if candidate_fd is None:
                        manifest_stat = (candidate_root / "snapshot.json").lstat()
                    else:
                        manifest_stat = os.stat(
                            "snapshot.json",
                            dir_fd=candidate_fd,
                            follow_symlinks=False,
                        )
                    if stat.S_ISREG(manifest_stat.st_mode):
                        valid_names.append(name)
                except FileNotFoundError:
                    pass
                finally:
                    close_directory_token(candidate_token)
            if not valid_names:
                raise PackError("no snapshots found")
            selected = sorted(valid_names)[-1] if snapshot_id == "latest" else snapshot_id
            if selected not in valid_names:
                raise PackError("snapshot not found: {}".format(snapshot_id))
            snapshot = snapshots_root / selected
            snapshot_root, snapshot_token = open_directory_no_symlinks(snapshot, create=False)
            close_directory_token(snapshot_token)
            return snapshot_root
        finally:
            close_directory_token(snapshots_token)

    def rollback_plan(
        self,
        snapshot_id: str,
        planned_preimages: Optional[Dict[str, Optional[str]]] = None,
    ) -> Tuple[dict, Path, Dict[str, Optional[str]]]:
        snapshot = self.resolve_snapshot(snapshot_id)
        manifest, saved_files, state_data, manifest_sha = self.read_snapshot(snapshot)
        entries = []
        preimages: Dict[str, Optional[str]] = {}
        for name, info in sorted(manifest["files"].items()):
            target = self.target_dir / (name + ".md")
            key = path_key(target)
            current_sha = (
                planned_preimages[key]
                if planned_preimages is not None
                else target_preimage_sha(target)
            )
            preimages[key] = current_sha
            entries.append(
                {
                    "name": name,
                    "action": "RESTORE" if info["existed"] else "REMOVE",
                    "current_sha": current_sha,
                    "snapshot_sha": sha256_bytes(saved_files[name]) if info["existed"] else None,
                }
            )
        state_key = path_key(self.state_file)
        state_sha = (
            planned_preimages[state_key]
            if planned_preimages is not None
            else target_preimage_sha(self.state_file)
        )
        preimages[state_key] = state_sha
        plan = {
            "operation": "rollback",
            "target_dir": str(self.target_dir),
            "snapshot_id": snapshot.name,
            "snapshot_operation": manifest.get("operation", "unknown"),
            "manifest_sha": manifest_sha,
            "entries": entries,
            "state_action": "RESTORE" if manifest["state_existed"] else "REMOVE",
            "state_current_sha": state_sha,
            "state_snapshot_sha": sha256_bytes(state_data) if state_data is not None else None,
        }
        plan["digest"] = canonical_digest(plan)
        return plan, snapshot, preimages

    @transactional("rollback", dry_run_index=1)
    def rollback(self, snapshot_id: str, dry_run: bool, confirm_plan: Optional[str] = None) -> dict:
        plan, snapshot, preimages = self.rollback_plan(snapshot_id)
        print("Rollback plan: {} ({})".format(snapshot.name, plan["snapshot_operation"]))
        for entry in plan["entries"]:
            print(
                "{} {} current={} snapshot={}".format(
                    entry["action"],
                    self.target_dir / (entry["name"] + ".md"),
                    entry["current_sha"] or "<absent>",
                    entry["snapshot_sha"] or "<absent>",
                )
            )
        print(
            "{} {} current={} snapshot={}".format(
                plan["state_action"],
                self.state_file,
                plan["state_current_sha"] or "<absent>",
                plan["state_snapshot_sha"] or "<legacy-unverified>",
            )
        )
        print("PLAN_DIGEST {}".format(plan["digest"]))
        if dry_run:
            print("DRY-RUN: no files changed")
            return plan
        if confirm_plan != plan["digest"]:
            raise DecisionRequired("rollback requires --confirm-plan {}".format(plan["digest"]))

        current_plan, current_snapshot, current_preimages = self.rollback_plan(snapshot.name)
        if current_plan["digest"] != confirm_plan or current_snapshot != snapshot or current_preimages != preimages:
            raise DecisionRequired("rollback plan changed after confirmation; run dry-run again")
        self._active_snapshot = self.create_snapshot(
            sorted(entry["name"] for entry in plan["entries"]),
            "rollback",
        )
        self.restore_snapshot(
            snapshot,
            expected_preimages=preimages,
            expected_plan_digest=confirm_plan,
        )
        print("Rollback complete: {}".format(snapshot.name))
        return plan


def nonempty_path_argument(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("path must not be empty")
    return Path(value)


def nonempty_string_argument(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("value must not be empty")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the release package")
    scan = subparsers.add_parser("scan", help="inventory existing agents without writing")
    scan.add_argument("--json", action="store_true", dest="json_output")
    scan.add_argument("--target-dir", type=nonempty_path_argument, default=Path.home() / ".zcode" / "agents")
    for command in ("install", "update"):
        child = subparsers.add_parser(command, help="{} agents".format(command))
        child.add_argument("--dry-run", action="store_true")
        child.add_argument("--model-map", metavar="PATH", type=nonempty_string_argument)
        child.add_argument(
            "--inventory",
            metavar="PATH",
            type=nonempty_string_argument,
            help="sanitized model inventory used to validate model-map",
        )
        child.add_argument(
            "--allow-unverified-model-map",
            action="store_true",
            help="accept a model-map without inventory validation after explicit user approval",
        )
        child.add_argument("--target-dir", type=nonempty_path_argument, default=Path.home() / ".zcode" / "agents")
        child.add_argument("--confirm-plan", help="digest printed by the matching dry-run plan")
        if command == "install":
            child.add_argument("--force", action="store_true", help="replace existing package state")
            child.add_argument("--only", action="append", help="install only selected agent names (repeat or comma-separate)")
            child.add_argument("--skip", action="append", help="skip selected agent names (repeat or comma-separate)")
            child.add_argument("--overwrite", action="append", help="overwrite and back up an unmanaged collision")
            child.add_argument("--keep", action="append", help="preserve an unmanaged collision and leave it untracked")
        else:
            child.add_argument("--add", action="append", help="add selected package agents (repeat or comma-separate)")
            child.add_argument("--remove", action="append", help="remove selected package agents from the managed set")
            child.add_argument("--overwrite", action="append", help="overwrite and back up an unmanaged added collision")
            child.add_argument("--keep", action="append", help="keep an unmanaged added collision untracked")
    rollback = subparsers.add_parser("rollback", help="restore a pre-operation snapshot")
    rollback.add_argument("snapshot", nargs="?", default="latest")
    rollback.add_argument("--dry-run", action="store_true")
    rollback.add_argument("--confirm-plan", help="digest printed by the matching rollback dry-run")
    rollback.add_argument("--target-dir", type=nonempty_path_argument, default=Path.home() / ".zcode" / "agents")
    uninstall = subparsers.add_parser("uninstall", help="remove managed agents")
    uninstall.add_argument("--dry-run", action="store_true")
    uninstall.add_argument("--target-dir", type=nonempty_path_argument, default=Path.home() / ".zcode" / "agents")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "validate":
            return 0 if validate_package() else 1
        manager = Manager(args.target_dir)
        if args.command == "scan":
            manager.scan(args.json_output)
        elif args.command == "install":
            manager.install(
                args.dry_run,
                args.model_map,
                args.force,
                only=args.only,
                skip=args.skip,
                overwrite=args.overwrite,
                keep=args.keep,
                inventory_path=args.inventory,
                confirm_plan=args.confirm_plan,
                allow_unverified_model_map=args.allow_unverified_model_map,
            )
        elif args.command == "update":
            manager.update(
                args.dry_run,
                args.model_map,
                args.inventory,
                args.allow_unverified_model_map,
                args.confirm_plan,
                args.add,
                args.remove,
                args.overwrite,
                args.keep,
            )
        elif args.command == "rollback":
            manager.rollback(args.snapshot, args.dry_run, args.confirm_plan)
        elif args.command == "uninstall":
            manager.uninstall(args.dry_run)
        return 0
    except DecisionRequired as exc:
        print("DECISION REQUIRED: {}".format(exc), file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("ERROR: operation interrupted", file=sys.stderr)
        return 130
    except (PackError, OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
