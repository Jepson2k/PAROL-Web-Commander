"""Selected project archives, validated completely before creating a destination."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Mapping, Iterable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any

from packaging.requirements import Requirement
from waldoctl.setup import SetupSnapshot, validate_name
from waldoctl.world import world_from_dict

MANIFEST = "waldo-project.json"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_FILES = 128
_KINDS = {
    "programs": ".py",
    "setups": ".json",
    "recordings": ".json",
    "worlds": ".json",
    "debug": ".json",
}
_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,127}\Z")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_path(name: str) -> PurePosixPath:
    if not isinstance(name, str):
        raise ValueError("Project paths must be text")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or not path.parts
        or any(
            not _PART.fullmatch(p)
            or p.endswith((".", " "))
            or p.split(".")[0].upper() in _RESERVED
            for p in path.parts
        )
    ):
        raise ValueError(f"Invalid portable project path: {name!r}")
    return path


def _json(data: bytes) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"Nonfinite JSON value: {value}")

    return json.loads(data, object_pairs_hook=unique, parse_constant=nonfinite)


def _requirements(values: Iterable[str]) -> list[str]:
    result = []
    for value in values:
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError("Invalid dependency requirement")
        requirement = Requirement(value)
        if requirement.url:
            raise ValueError(
                "Use package/version requirements; dependency URLs are not exported"
            )
        result.append(str(requirement))
    if len(result) > 100:
        raise ValueError("Too many dependency requirements")
    return sorted(set(result))


def dependency_report(requirements: Iterable[str]) -> list[str]:
    issues = []
    for value in _requirements(requirements):
        requirement = Requirement(value)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        if requirement.extras:
            issues.append(f"Extras need checking separately: {requirement}")
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            issues.append(f"Missing: {requirement}")
            continue
        if requirement.specifier and not requirement.specifier.contains(
            installed, prereleases=True
        ):
            issues.append(f"Incompatible: {requirement}; installed {installed}")
    return issues


def current_requirements(backend: str) -> list[str]:
    result = []
    for package in ("waldo-commander", "waldoctl", backend):
        try:
            result.append(f"{package}=={version(package)}")
        except PackageNotFoundError:
            result.append(package)
    return _requirements(result)


def _validate_content(path: str, data: bytes) -> None:
    name = safe_path(path)
    kind = name.parts[0]
    if len(name.parts) < 2 or kind not in _KINDS or name.suffix != _KINDS[kind]:
        raise ValueError(f"Unsupported project file: {path}")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Project file is too large: {path}")
    if kind == "programs":
        data.decode("utf-8")
        return
    document = _json(data)
    if kind == "setups":
        if len(name.parts) != 2:
            raise ValueError("Named setup files must be directly inside setups/")
        validate_name(name.stem)
        SetupSnapshot.from_dict(document)
    elif kind == "worlds":
        world_from_dict(document)
    elif kind == "recordings":
        from waldo_commander.demonstrations import demonstration_from_dict

        demonstration_from_dict(document)
    elif (
        not isinstance(document, dict)
        or document.get("export") != "numeric-debug"
        or document.get("schema") != 1
        or not isinstance(document.get("events"), list)
    ):
        raise ValueError("Expected a numeric debugging export")


def _validate_files(files: Mapping[str, bytes]) -> None:
    if not files or len(files) > MAX_FILES:
        raise ValueError(f"Select between 1 and {MAX_FILES} project files")
    if sum(len(data) for data in files.values()) > MAX_ARCHIVE_BYTES:
        raise ValueError("Project exceeds the unpacked size limit")
    seen = set()
    for path, data in files.items():
        _validate_content(path, data)
        folded = path.casefold()
        if folded in seen:
            raise ValueError("Project paths collide on a case-insensitive filesystem")
        seen.add(folded)
    for path in seen:
        if any(parent.as_posix() in seen for parent in PurePosixPath(path).parents):
            raise ValueError("A project file conflicts with a directory")


def export_project(
    files: Mapping[str, bytes], *, requirements: Iterable[str] = ()
) -> bytes:
    """Export explicit file contents; no directories or environment files are scanned."""
    _validate_files(files)
    manifest = {
        "schema": 1,
        "requirements": _requirements(requirements),
        "files": [
            {
                "path": path,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for path, data in sorted(files.items())
        ],
    }
    manifest_data = json.dumps(manifest, indent=2, allow_nan=False).encode("utf-8")
    if len(manifest_data) > 256 * 1024:
        raise ValueError("Project manifest is too large")
    if len(manifest_data) + sum(map(len, files.values())) > MAX_ARCHIVE_BYTES:
        raise ValueError("Project exceeds the unpacked size limit")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, manifest_data)
        for path, data in sorted(files.items()):
            archive.writestr(path, data)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise ValueError("Project archive is too large")
    return result


def read_manifest(data: bytes) -> dict[str, Any]:
    document = _json(data)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "requirements", "files"}
        or type(document["schema"]) is not int
        or document["schema"] != 1
    ):
        raise ValueError("Unsupported portable project manifest")
    if (
        not isinstance(document["requirements"], list)
        or not isinstance(document["files"], list)
        or len(document["files"]) > MAX_FILES
    ):
        raise ValueError("Invalid project manifest")
    _requirements(document["requirements"])
    seen = set()
    for entry in document["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise ValueError("Invalid project file entry")
        path = safe_path(entry["path"]).as_posix()
        if (
            path in seen
            or type(entry["size"]) is not int
            or not 0 <= entry["size"] <= MAX_FILE_BYTES
            or not isinstance(entry["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise ValueError("Invalid or duplicate project file entry")
        seen.add(path)
    return document


def inspect_project(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Validate ZIP metadata, paths, checksums and typed data without writing files."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Project archive is too large")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if not 1 < len(members) <= MAX_FILES + 1:
                raise ValueError("Invalid project file count")
            seen = set()
            for info in members:
                safe_path(info.filename)
                mode = info.external_attr >> 16
                if (
                    info.filename in seen
                    or info.is_dir()
                    or stat.S_ISLNK(mode)
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or info.flag_bits & 1
                    or info.compress_type
                    not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or info.file_size > MAX_FILE_BYTES
                ):
                    raise ValueError("Unsupported or duplicate ZIP member")
                seen.add(info.filename)
            if MANIFEST not in seen or archive.getinfo(MANIFEST).file_size > 256 * 1024:
                raise ValueError("Missing or oversized project manifest")
            if sum(info.file_size for info in members) > MAX_ARCHIVE_BYTES:
                raise ValueError("Project exceeds the unpacked size limit")
            manifest = read_manifest(archive.read(MANIFEST))
            if seen != {MANIFEST, *(entry["path"] for entry in manifest["files"])}:
                raise ValueError("ZIP contents do not match the project manifest")
            files = {}
            for entry in manifest["files"]:
                info = archive.getinfo(entry["path"])
                if info.file_size != entry["size"]:
                    raise ValueError("Project file size does not match its manifest")
                content = archive.read(info)
                if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                    raise ValueError(
                        "Project file checksum does not match its manifest"
                    )
                files[entry["path"]] = content
            _validate_files(files)
            return manifest, files
    except (zipfile.BadZipFile, RuntimeError, KeyError, UnicodeError) as error:
        raise ValueError(f"Invalid project archive: {error}") from error


def import_project(data: bytes, parent: Path, *, name: str = "project") -> Path:
    """Write a validated archive to a new unique folder, without executing or applying it."""
    if len(safe_path(name).parts) != 1:
        raise ValueError("Choose a single project folder name")
    manifest, files = inspect_project(data)
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=parent))
    try:
        for path, content in files.items():
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
        (destination / MANIFEST).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        return destination
    except BaseException:
        shutil.rmtree(destination)
        raise
