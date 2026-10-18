"""容器只交换受限普通文件快照；先完整校验，再修改宿主工作树。"""

import io
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile

from .file_access import Repository, linked

MAX_FILES = 4000
MAX_SINGLE = 4_000_000
CACHES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
GIT_CONTROLS = {".gitattributes", ".gitmodules"}


def inventory(root: Path, limit: int) -> dict[str, tuple[bytes, int]]:
    repo = Repository(root)
    files, total = {}, 0
    for name in repo.files():
        if any(part in CACHES for part in PurePosixPath(name).parts):
            continue
        path = repo.resolve(name)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("snapshot_special_file")
        with path.open("rb") as stream:
            data = stream.read(MAX_SINGLE + 1)
        total += len(data)
        if len(data) > MAX_SINGLE or total > limit or len(files) >= MAX_FILES:
            raise ValueError("snapshot_limit")
        files[name] = (data, 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644)
    validate_names(files)
    return files


def validate_names(files):
    folded = {name.casefold() for name in files}
    if len(folded) != len(files):
        raise ValueError("snapshot_case_collision")
    components = {}
    for name in files:
        for path in [PurePosixPath(name), *PurePosixPath(name).parents]:
            spelling = path.as_posix()
            previous = components.setdefault(spelling.casefold(), spelling)
            if previous != spelling:
                raise ValueError('snapshot_case_collision')
        reserved = {'con', 'prn', 'aux', 'nul'} | {f'{p}{n}' for p in ('com', 'lpt') for n in range(1, 10)}
        if any(part.endswith(('.', ' ')) or any(c in '<>"|?*' or ord(c) < 32 for c in part)
               or part.split('.')[0].casefold() in reserved
               for part in PurePosixPath(name).parts):
            raise ValueError('snapshot_nonportable_path')
        if any(parent.as_posix().casefold() in folded for parent in PurePosixPath(name).parents
               if parent.as_posix() != "."):
            raise ValueError("snapshot_file_directory_collision")


def pack(files) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        directories = sorted({parent.as_posix() for name in files for parent in PurePosixPath(name).parents
                              if parent.as_posix() != "."}, key=lambda name: (name.count("/"), name))
        for name in directories:
            entry = tarfile.TarInfo(name)
            entry.type, entry.mode, entry.uid, entry.gid = tarfile.DIRTYPE, 0o755, 65532, 65532
            archive.addfile(entry)
        for name, (data, mode) in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.uid, entry.gid = len(data), mode, 65532, 65532
            archive.addfile(entry, io.BytesIO(data))
    return output.getvalue()


def unpack(data: bytes, limit: int) -> dict[str, tuple[bytes, int]]:
    files, seen, total, entries = {}, set(), 0, 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for entry in archive:
            entries += 1
            if entries > MAX_FILES * 4:
                raise ValueError("archive_entry_limit")
            name = entry.name
            while name.startswith("./"):
                name = name[2:]
            if name in {"", "."} and entry.isdir():
                continue
            if ("\\" in name or name.startswith("/") or any(p in {"", ".", ".."} for p in name.split("/"))
                    or ":" in name or "\0" in name):
                raise ValueError("archive_path_denied")
            if not (entry.isfile() or entry.isdir()):
                raise ValueError("archive_special_file")
            if any(part in CACHES for part in PurePosixPath(name).parts):
                continue
            Repository._guard(name)
            if name in seen:
                raise ValueError("archive_duplicate_path")
            seen.add(name)
            if entry.isdir():
                continue
            total += entry.size
            if entry.size < 0 or entry.size > MAX_SINGLE or total > limit or len(files) >= MAX_FILES:
                raise ValueError("archive_size_limit")
            stream = archive.extractfile(entry)
            content = stream.read(entry.size + 1)
            if len(content) != entry.size:
                raise ValueError("archive_incomplete_file")
            files[name] = (content, 0o755 if entry.mode & 0o111 else 0o644)
    validate_names(files)
    return files


def apply(root: Path, before, after, *, writable: bool, limit: int):
    if inventory(root, limit) != before:
        raise ValueError("host_snapshot_changed")
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    if changed and not writable:
        raise ValueError("isolated_readonly_tree_changed")
    if any(PurePosixPath(name).name.casefold() in GIT_CONTROLS for name in changed):
        raise ValueError("git_control_file_change_denied")
    repo = Repository(root)
    # 所有路径和归档条目通过后才开始写；失败导入不改变源码。
    for name in changed:
        target = repo.resolve(name, must_exist=False)
        if target.is_dir() or any(parent.exists() and not parent.is_dir()
                                  for parent in target.parents if parent != root):
            raise ValueError('import_file_directory_transition_denied')
    for name in changed:
        if name not in after:
            repo.resolve(name).unlink()
    for name in changed:
        if name not in after:
            continue
        target = repo.resolve(name, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        if linked(target.parent):
            raise ValueError("link_denied")
        content, mode = after[name]
        descriptor, staging = tempfile.mkstemp(dir=target.parent, prefix=".repopilot-import-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            os.chmod(staging, mode)
            os.replace(staging, target)
        finally:
            Path(staging).unlink(missing_ok=True)
    return changed
