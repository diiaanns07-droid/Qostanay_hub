#!/usr/bin/env python3
"""Adal offline kit: inventory, bundle and read-only offline readiness check (target: Windows x64).

    python adal_offline_kit.py inventory     [--scenario student] [--out FILE]
    python adal_offline_kit.py plan          [--scenario student] [--kit DIR]
    python adal_offline_kit.py build         --out KIT --models-dir DIR --wheels-dir DIR --electron-zip PATH ...
    python adal_offline_kit.py check         --kit KIT [--scenario student]
    python adal_offline_kit.py check-install [--scenario student] [--models-dir DIR]

Guarantees (tested in tests/test_offline_kit.py):
* never downloads and never opens sockets; never runs pip, npm, node, Electron or any other process;
* "check" and "check-install" never write anything (also no __pycache__); "build" writes only into --out,
  which must be a new directory outside the repository and outside every input;
* expected sizes/hashes come from the repository's committed manifests and lockfiles, never from the input;
  an artifact whose expected hash or license cannot be established is BLOCKED, never PASS.
Standard library only, Python >= 3.11 (the Adal target interpreter is CPython 3.12 x64).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # read-only checks must not leave __pycache__ next to the kit or the source

import argparse
import base64
import email.parser
import hashlib
import json
import os
import re
import shutil
import struct
import sysconfig
import tarfile
import tomllib
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

TOOL_VERSION = "1.0.0"
MANIFEST_SCHEMA = "adal.offline-kit/1"
SPEC_SCHEMA = "adal.offline-kit-spec/1"
MANIFEST_NAME = "adal-offline-kit.manifest.json"
SUMS_NAME = "SHA256SUMS.txt"
README_NAME = "README-OFFLINE.txt"
HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parents[1]  # .../proctoring
SPEC_PATH = HERE / "kit_spec.json"
CHUNK = 1 << 20

EXIT_READY, EXIT_NOT_READY, EXIT_USAGE = 0, 1, 2

# Target platform of the demo PCs. Everything platform-specific is derived from this one record.
TARGET = {
    "id": "win-x64",
    "sys_platform": "win32",
    "platform_machine": "AMD64",
    "platform_system": "Windows",
    "os_name": "nt",
    "python": (3, 12),
    "sysconfig_platform": "win-amd64",
    "wheel_platforms": ("win_amd64",),
    "pe_machine": 0x8664,
    "electron_platform": "win32",
    "electron_arch": "x64",
    "npm_os": "win32",
    "npm_cpu": "x64",
}
PE_MACHINES = {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64", 0x1C4: "arm"}
ELECTRON_MIRROR = "https://github.com/electron/electron/releases/download/"

STATUS_ORDER = {"FAIL": 0, "BLOCKED": 1, "WARN": 2, "PASS": 3, "SKIP": 4}


class UsageError(Exception):
    """Wrong arguments or an unsafe path; exit code 2."""


# --------------------------------------------------------------------------------------------- files


def sha256_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def digest_file(path: Path, algorithm: str) -> bytes:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.digest()


def multi_digest(path: Path, algorithms: tuple[str, ...]) -> tuple[dict[str, str], int]:
    digests, size = {name: hashlib.new(name) for name in algorithms}, 0
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            size += len(chunk)
            for digest in digests.values():
                digest.update(chunk)
    return {name: digest.hexdigest() for name, digest in digests.items()}, size


def safe_relative(text: str) -> PurePosixPath:
    """A kit-relative path from a manifest: no absolute paths, drive letters, '..' or backslashes."""
    if not text or "\\" in text or ":" in text or text.startswith("/"):
        raise ValueError(f"unsafe relative path: {text!r}")
    path = PurePosixPath(text)
    if any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe relative path: {text!r}")
    return path


def inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """(size, mtime_ns) of every file below root; used by the no-write self test."""
    result = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return result


def human(size: int | None) -> str:
    if size is None:
        return "?"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return str(size)


def read_git_head(root: Path) -> str | None:
    """Commit of the source checkout without running git (worktrees and packed refs supported)."""
    for candidate in (root, *root.parents):
        dotgit = candidate / ".git"
        if dotgit.exists():
            break
    else:
        return None
    try:
        gitdir = dotgit
        if dotgit.is_file():
            text = dotgit.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = (dotgit.parent / text.split(":", 1)[1].strip()).resolve()
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head if re.fullmatch(r"[0-9a-f]{40}", head) else None
        ref = head.split(":", 1)[1].strip()
        common = gitdir
        commondir = gitdir / "commondir"
        if commondir.is_file():
            common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
        for base in (gitdir, common):
            loose = base / ref
            if loose.is_file():
                return loose.read_text(encoding="utf-8").strip()
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        return None
    return None


# ------------------------------------------------------------------------------------- platform probes


def pe_machine(head: bytes) -> str:
    """Architecture of an executable from its first bytes: x64/x86/arm64, 'elf', 'mach-o' or 'unknown'."""
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return "mach-o"
    if head[:2] != b"MZ" or len(head) < 0x40:
        return "unknown"
    offset = struct.unpack_from("<I", head, 0x3C)[0]
    if offset + 6 > len(head) or head[offset:offset + 4] != b"PE\0\0":
        return "unknown"
    machine = struct.unpack_from("<H", head, offset + 4)[0]
    return PE_MACHINES.get(machine, f"pe-0x{machine:04x}")


def pe_machine_of(path: Path) -> str:
    with open(path, "rb") as stream:
        return pe_machine(stream.read(4096))


def parse_wheel_name(filename: str) -> tuple[str, str, set[tuple[str, str, str]]]:
    """(normalized name, version, {(python, abi, platform)}) from a PEP 427 wheel file name."""
    if not filename.endswith(".whl"):
        raise ValueError("not a wheel")
    parts = filename[:-4].split("-")
    if len(parts) not in (5, 6):
        raise ValueError(f"malformed wheel name: {filename}")
    name, version = parts[0], parts[1]
    pys, abis, plats = parts[-3].split("."), parts[-2].split("."), parts[-1].split(".")
    tags = {(p, a, pl) for p in pys for a in abis for pl in plats}
    return normalize_name(name), version, tags


def tag_compatible(tag: tuple[str, str, str], target: dict = TARGET) -> bool:
    """Is a wheel tag installable on CPython <target python> for the target platform?"""
    py, abi, plat = tag
    major, minor = target["python"]
    if plat != "any" and plat not in target["wheel_platforms"]:
        return False
    cp = f"cp{major}{minor}"
    if abi == cp:
        return py == cp
    if abi == "abi3":
        found = re.fullmatch(r"cp(\d)(\d+)", py)
        return bool(found) and int(found.group(1)) == major and int(found.group(2)) <= minor
    if abi == "none":
        if py in (cp, f"py{major}", f"py{major}{minor}"):
            return True
        found = re.fullmatch(r"py(\d)(\d+)", py)
        return bool(found) and int(found.group(1)) == major and int(found.group(2)) <= minor
    return False


def wheel_platform_verdict(tags: set[tuple[str, str, str]], target: dict = TARGET) -> tuple[bool, str]:
    good = sorted("-".join(t) for t in tags if tag_compatible(t, target))
    if good:
        return True, good[0]
    return False, ", ".join(sorted({t[2] for t in tags}))


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# --------------------------------------------------------------------------- environment markers (PEP 508)

_MARKER_TOKEN = re.compile(r"\s*(?:(\()|(\))|(and\b)|(or\b)|(===|==|!=|<=|>=|~=|<|>|not\s+in\b|in\b)|"
                           r"'([^']*)'|\"([^\"]*)\"|([A-Za-z_][A-Za-z0-9_.]*))")


def marker_environment(target: dict = TARGET) -> dict[str, str]:
    major, minor = target["python"]
    return {
        "sys_platform": target["sys_platform"], "platform_machine": target["platform_machine"],
        "platform_system": target["platform_system"], "os_name": target["os_name"],
        "python_version": f"{major}.{minor}", "python_full_version": f"{major}.{minor}.0",
        "implementation_name": "cpython", "platform_python_implementation": "CPython",
        "extra": "",
    }


def evaluate_marker(text: str, env: dict[str, str]) -> bool:
    """Small fail-closed evaluator for the marker subset uv exports (and/or/parentheses/comparisons)."""
    tokens, pos = [], 0
    text = text.strip()
    while pos < len(text):
        match = _MARKER_TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise ValueError(f"unsupported marker: {text!r}")
        pos = match.end()
        lpar, rpar, and_, or_, op, sq, dq, name = match.groups()
        if lpar: tokens.append(("(", None))
        elif rpar: tokens.append((")", None))
        elif and_: tokens.append(("and", None))
        elif or_: tokens.append(("or", None))
        elif op: tokens.append(("op", re.sub(r"\s+", " ", op)))
        elif sq is not None or dq is not None: tokens.append(("str", sq if sq is not None else dq))
        else:
            if name not in env:
                raise ValueError(f"unknown marker variable: {name}")
            tokens.append(("str", env[name]))
    index = 0

    def peek():
        return tokens[index][0] if index < len(tokens) else None

    def take(kind):
        nonlocal index
        if peek() != kind:
            raise ValueError(f"unsupported marker: {text!r}")
        index += 1
        return tokens[index - 1][1]

    def version_tuple(value):
        return tuple(int(x) for x in re.findall(r"\d+", value))

    def atom():
        if peek() == "(":
            take("(")
            value = expr()
            take(")")
            return value
        left, op, right = take("str"), take("op"), take("str")
        if op in ("==", "==="): return left == right
        if op == "!=": return left != right
        if op == "in": return left in right
        if op == "not in": return left not in right
        a, b = version_tuple(left), version_tuple(right)
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "~=": a >= b and a[:len(b) - 1] == b[:len(b) - 1]}[op]

    def conj():
        value = atom()
        while peek() == "and":
            take("and")
            value = atom() and value
        return value

    def expr():
        value = conj()
        while peek() == "or":
            take("or")
            value = conj() or value
        return value

    result = expr()
    if index != len(tokens):
        raise ValueError(f"unsupported marker: {text!r}")
    return result


# ---------------------------------------------------------------------------------- lockfile readers


@dataclass
class Requirement:
    name: str
    version: str
    marker: str | None
    hashes: set[str]


def parse_requirements(path: Path) -> list[Requirement]:
    """Hash-pinned requirements exported by uv (`name==ver ; marker \\ --hash=sha256:...`)."""
    logical, current = [], ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            if current:
                logical.append(current)
                current = ""
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        current += line
        logical.append(current)
        current = ""
    if current:
        logical.append(current)
    result = []
    for entry in logical:
        entry = entry.strip()
        if not entry or entry.startswith("-"):
            continue
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", entry))
        spec = re.sub(r"--hash=sha256:[0-9a-f]{64}", "", entry).strip()
        requirement, _, marker = spec.partition(";")
        found = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;]+)\s*", requirement)
        if not found:
            raise ValueError(f"{path.name}: not an exact pin: {requirement.strip()!r}")
        result.append(Requirement(normalize_name(found.group(1)), found.group(2), marker.strip() or None, hashes))
    return result


def lock_wheels(root: Path) -> dict[str, list[dict]]:
    """uv.lock wheels per normalized package name (url, sha256, size)."""
    data = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {}
    for package in data.get("package", []):
        rows = []
        for wheel in package.get("wheels", []):
            algo, _, value = wheel.get("hash", "").partition(":")
            if algo == "sha256":
                rows.append({"url": wheel["url"], "sha256": value, "size": wheel.get("size"),
                             "filename": wheel["url"].rsplit("/", 1)[-1], "version": package.get("version")})
        result[normalize_name(package["name"])] = rows
    return result


def wheel_license(path: Path) -> dict:
    """License declared inside a wheel's METADATA (the only offline evidence available for wheels)."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.count("/") == 1 and n.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                return {"status": "UNCONFIRMED", "id": None, "evidence": "METADATA not found in wheel"}
            meta = email.parser.Parser().parsestr(archive.read(names[0]).decode("utf-8", "replace"), headersonly=True)
            license_files = [n for n in archive.namelist() if re.search(r"\.dist-info/(licenses/|LICEN[CS]E|COPYING)", n)]
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        return {"status": "UNCONFIRMED", "id": None, "evidence": f"unreadable wheel: {type(exc).__name__}"}
    expression = (meta.get("License-Expression") or "").strip()
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    declared = (meta.get("License") or "").strip()
    if expression:
        value, source = expression, "METADATA License-Expression"
    elif classifiers:
        value, source = "; ".join(classifiers), "METADATA License classifier"
    elif declared and len(declared) <= 120 and "\n" not in declared:
        value, source = declared, "METADATA License"
    elif declared:
        value, source = "see License field (full text)", "METADATA License (long text)"
    else:
        value, source = None, None
    if not value:
        return {"status": "UNCONFIRMED", "id": None, "evidence": "no license field in METADATA",
                "license_files": license_files[:5]}
    return {"status": "DECLARED", "id": value, "evidence": source, "license_files": license_files[:5]}


def npm_lock(root: Path) -> dict:
    return json.loads((root / "desktop" / "package-lock.json").read_text(encoding="utf-8"))


def sri_to_hex(integrity: str) -> tuple[str, str]:
    algo, _, value = integrity.split()[0].partition("-")
    return algo, base64.b64decode(value).hex()


def cacache_content_path(cache: Path, integrity: str) -> Path:
    algo, hexdigest = sri_to_hex(integrity)
    return cache / "_cacache" / "content-v2" / algo / hexdigest[:2] / hexdigest[2:4] / hexdigest[4:]


def electron_cache_relpath(version: str, filename: str) -> str:
    """Path of an artifact inside an @electron/get cache root (electron_config_cache)."""
    directory = hashlib.sha256(f"{ELECTRON_MIRROR}v{version}".encode()).hexdigest()
    return f"{directory}/{filename}"


def electron_checksums(root: Path, version: str, npm_cache: Path | None) -> tuple[dict | None, str]:
    """checksums.json shipped in the locked electron npm package; returns (mapping, evidence)."""
    installed = root / "desktop" / "node_modules" / "electron"
    try:
        package = json.loads((installed / "package.json").read_text(encoding="utf-8"))
        if package.get("version") == version and (installed / "checksums.json").is_file():
            return (json.loads((installed / "checksums.json").read_text(encoding="utf-8")),
                    f"desktop/node_modules/electron/checksums.json (electron@{version})")
    except (OSError, ValueError):
        pass
    if npm_cache is not None:
        entry = npm_lock(root)["packages"].get("node_modules/electron", {})
        integrity = entry.get("integrity")
        if integrity:
            tarball = cacache_content_path(npm_cache, integrity)
            algo, expected = sri_to_hex(integrity)
            if tarball.is_file() and digest_file(tarball, algo).hex() == expected:
                with tarfile.open(tarball, "r:gz") as archive:
                    member = archive.extractfile("package/checksums.json")
                    if member is not None:
                        return (json.loads(member.read().decode("utf-8")),
                                f"electron-{version}.tgz from npm cache, verified against package-lock integrity")
    return None, ("checksums.json of electron@" + version + " not found: need desktop/node_modules/electron "
                  "(after npm ci) or --npm-cache containing the locked electron tarball")


# ------------------------------------------------------------------------------------------ the spec


def load_spec(path: Path = SPEC_PATH) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("schema") != SPEC_SCHEMA:
        raise UsageError(f"{path.name}: unexpected schema {spec.get('schema')!r}")
    return spec


def scenario_level(levels: dict, scenario: str, features: set[str]) -> str:
    level = levels.get(scenario, "not-used")
    if level.startswith("optional:") and level.split(":", 1)[1] in features:
        return "required"
    return level


@dataclass
class Expected:
    id: str
    kind: str
    kit_path: str
    level: str  # required | optional:<feature> | not-used
    sha256: str | None = None
    size: int | None = None
    integrity: str | None = None
    source: dict = field(default_factory=dict)
    license: dict = field(default_factory=dict)
    compatibility: dict = field(default_factory=dict)
    prepare: str = ""
    install: str = ""
    blocked: str | None = None  # why the expectation itself cannot be established
    extra: dict = field(default_factory=dict)


def _audio_constants(root: Path) -> dict:
    """Pinned Silero values from backend/proctor/audio/assets.py, parsed without importing the backend."""
    text = (root / "backend" / "proctor" / "audio" / "assets.py").read_text(encoding="utf-8")
    values = dict(re.findall(r'^(REVISION|MODEL_NAME|MODEL_SHA256)\s*=\s*"([^"]+)"', text, re.M))
    url = re.search(r'^MODEL_URL\s*=\s*f"([^"]+)"', text, re.M)
    lic = re.search(r'^LICENSE_URL\s*=\s*f"([^"]+)"', text, re.M)
    if set(values) != {"REVISION", "MODEL_NAME", "MODEL_SHA256"} or not url or not lic:
        raise ValueError("backend/proctor/audio/assets.py: pinned Silero constants not found")
    fill = lambda s: s.replace("{REVISION}", values["REVISION"]).replace("{MODEL_NAME}", values["MODEL_NAME"])
    return {"revision": values["REVISION"], "name": values["MODEL_NAME"], "sha256": values["MODEL_SHA256"],
            "url": fill(url.group(1)), "license_url": fill(lic.group(1))}


def expected_models(root: Path, spec: dict, scenario: str, features: set[str]) -> list[Expected]:
    rows = []
    for entry in spec["models"]:
        level = scenario_level(entry["levels"], scenario, features)
        common = dict(level=level, prepare=entry["prepare_online"], install=entry["install"],
                      license={k: entry[k] for k in ("license_status", "license_evidence", "distribution",
                                                     "distribution_note") if k in entry},
                      compatibility={"target": TARGET["id"], "rule": entry.get("compatibility", "platform-independent model file; runtime: " + entry.get("runtime", ""))})
        if entry["manifest_kind"] == "single":
            manifest = json.loads((root / entry["manifest"]).read_text(encoding="utf-8"))
            items = [manifest]
        elif entry["manifest_kind"] == "list":
            items = json.loads((root / entry["manifest"]).read_text(encoding="utf-8"))["models"]
        elif entry["manifest_kind"] == "audio-assets":
            pinned = _audio_constants(root)
            items = [{"model_id": f"silero-vad-{pinned['revision'][:12]}", "file": f"audio/{pinned['name']}",
                      "sha256": pinned["sha256"], "size_bytes": None, "source_url": pinned["url"],
                      "license": "MIT", "license_url": pinned["license_url"], "revision": pinned["revision"]}]
        else:
            raise ValueError(f"unknown manifest_kind {entry['manifest_kind']}")
        for item in items:
            safe_relative(item["file"])
            license_info = dict(common["license"])
            license_info["id"] = item.get("license")
            if item.get("license_url"):
                license_info["url"] = item["license_url"]
            rows.append(Expected(
                id=f"model:{item['model_id']}", kind="model", kit_path=f"models/{item['file']}",
                sha256=item["sha256"], size=item.get("size_bytes"),
                source={"url": item.get("source_url"), "expected_from": entry["manifest"]},
                **{k: v for k, v in common.items() if k != "license"}, license=license_info,
                extra={"module": entry["id"], "file": item["file"], **({"revision": item["revision"]} if "revision" in item else {})}))
        for companion in entry.get("companions", []):
            rows.append(Expected(
                id=f"model-companion:{entry['id']}:{companion['file']}", kind="model-companion",
                kit_path=f"models/{companion['file']}", level=level if companion.get("required") else
                ("optional:" + entry["id"] if level != "not-used" else "not-used"),
                source={"written_by": companion["written_by"]}, prepare=entry["prepare_online"], install=entry["install"],
                license={"license_status": "N/A", "note": companion.get("note", "")},
                extra={"module": entry["id"], "file": companion["file"], "validate": companion.get("validate")}))
    return rows


def expected_wheels(root: Path, spec: dict, scenario: str) -> list[Expected]:
    reqfile = spec["scenarios"][scenario].get("python_requirements")
    if not reqfile:
        return []
    env = marker_environment()
    locked = lock_wheels(root)
    rows = []
    for req in parse_requirements(root / reqfile):
        if req.marker and not evaluate_marker(req.marker, env):
            continue
        candidates = [w for w in locked.get(req.name, []) if w["version"] == req.version
                      and wheel_platform_verdict(parse_wheel_name(w["filename"])[2])[0] and w["sha256"] in req.hashes]
        # Prefer a platform-specific wheel over a pure one only when both exist; keep the order deterministic.
        candidates.sort(key=lambda w: ("-any.whl" in w["filename"], w["filename"]))
        prepare = spec["wheels"]["prepare_online"].replace("{requirements}", reqfile.replace("/", "\\"))
        if not candidates:
            rows.append(Expected(
                id=f"wheel:{req.name}", kind="python-wheel", kit_path=f"wheels/{req.name}-{req.version}-UNKNOWN.whl",
                level="required", blocked=f"uv.lock has no cp312/win_amd64 wheel of {req.name}=={req.version} "
                                          f"whose hash is pinned in {reqfile}", prepare=prepare,
                install=spec["wheels"]["install"], extra={"name": req.name, "version": req.version}))
            continue
        wheel = candidates[0]
        ok, tag = wheel_platform_verdict(parse_wheel_name(wheel["filename"])[2])
        rows.append(Expected(
            id=f"wheel:{req.name}", kind="python-wheel", kit_path=f"wheels/{wheel['filename']}", level="required",
            sha256=wheel["sha256"], size=wheel["size"],
            source={"url": wheel["url"], "expected_from": f"uv.lock + {reqfile} (--hash)"},
            license={"license_status": "PENDING", "note": "read from the wheel METADATA when the file is present"},
            compatibility={"target": TARGET["id"], "tag": tag, "python": "cp312"},
            prepare=prepare, install=spec["wheels"]["install"].replace("{requirements}", reqfile.replace("/", "\\")),
            extra={"name": req.name, "version": req.version, "accepted_sha256": sorted(req.hashes)}))
    return rows


def npm_expected(key: str, package: dict, level: str, entry: dict) -> Expected:
    name = key.rsplit("node_modules/", 1)[-1]
    integrity = package.get("integrity")
    algo, hexdigest = sri_to_hex(integrity) if integrity else ("sha512", "")
    return Expected(
        id=f"npm:{name}@{package.get('version')}", kind="npm-tarball",
        kit_path=f"npm-cache/_cacache/content-v2/{algo}/{hexdigest[:2]}/{hexdigest[2:4]}/{hexdigest[4:]}",
        level=level, integrity=integrity, source={"url": package.get("resolved"), "expected_from": "desktop/package-lock.json"},
        license={"license_status": "DECLARED" if package.get("license") else "UNCONFIRMED",
                 "id": package.get("license"), "evidence": "package-lock.json license field"},
        compatibility={"target": TARGET["id"], "os": package.get("os"), "cpu": package.get("cpu")},
        prepare=entry["prepare_online"], install=entry["install"],
        blocked=None if integrity else "package-lock.json has no integrity for this package",
        extra={"lock_key": key, "resolved": package.get("resolved"), "dev": bool(package.get("dev")),
               "optional": bool(package.get("optional"))})


def expected_electron(root: Path, spec: dict, scenario: str, npm_cache: Path | None) -> list[Expected]:
    entry = spec["electron"]
    level = entry["levels"].get(scenario, "not-used")
    if level == "not-used":
        return []
    lock_entry = npm_lock(root)["packages"]["node_modules/electron"]
    version = lock_entry["version"]
    filename = f"electron-v{version}-{TARGET['electron_platform']}-{TARGET['electron_arch']}.zip"
    checksums, evidence = electron_checksums(root, version, npm_cache)
    sha = checksums.get(filename) if checksums else None
    blocked = None if sha else (evidence if checksums is None else f"{filename} is absent from checksums.json")
    # The locked electron npm tarball is the offline trust anchor for the zip hash (lock integrity -> checksums.json).
    anchor = npm_expected("node_modules/electron", lock_entry, level, spec["npm_cache"])
    anchor.prepare = spec["electron"]["prepare_online"]
    anchor.extra["role"] = "checksums.json of the locked electron package (verifies the Electron zip offline)"
    return [anchor, Expected(
        id=f"electron:{version}:{TARGET['electron_platform']}-{TARGET['electron_arch']}", kind="electron-zip",
        kit_path=f"electron-cache/{electron_cache_relpath(version, filename)}", level=level, sha256=sha,
        source={"url": f"{ELECTRON_MIRROR}v{version}/{filename}", "expected_from": evidence},
        license={"license_status": entry["license_status"], "id": entry["license"],
                 "license_evidence": entry["license_evidence"], "distribution": entry["distribution"],
                 "distribution_note": entry["distribution_note"]},
        compatibility={"target": TARGET["id"], "pe_machine": "x64 (electron.exe inside the zip is checked)"},
        prepare=entry["prepare_online"], install=entry["install"], blocked=blocked,
        extra={"version": version, "filename": filename})]


def expected_npm(root: Path, spec: dict, scenario: str, features: set[str]) -> list[Expected]:
    entry = spec["npm_cache"]
    level = scenario_level(entry["levels"], scenario, features)
    if level == "not-used":
        return []
    rows = []
    for key, package in sorted(npm_lock(root)["packages"].items()):
        if not key or package.get("link"):
            continue
        if package.get("os") and TARGET["npm_os"] not in package["os"]:
            continue
        if package.get("cpu") and TARGET["npm_cpu"] not in package["cpu"]:
            continue
        rows.append(npm_expected(key, package, level, entry))
    return rows


def collect_expected(root: Path, spec: dict, scenario: str, features: set[str], npm_cache: Path | None = None) -> list[Expected]:
    if scenario not in spec["scenarios"]:
        raise UsageError(f"unknown scenario {scenario!r}; choose: {', '.join(spec['scenarios'])}")
    rows = expected_models(root, spec, scenario, features) + expected_wheels(root, spec, scenario)
    rows += expected_electron(root, spec, scenario, npm_cache) + expected_npm(root, spec, scenario, features)
    unique: dict[str, Expected] = {}
    rank = lambda e: 0 if e.level == "required" else (1 if e.level.startswith("optional") else 2)
    for exp in rows:  # the electron tarball can appear twice (trust anchor + full npm cache)
        if exp.id not in unique or rank(exp) < rank(unique[exp.id]):
            unique[exp.id] = exp
    return list(unique.values())


def descriptor_fingerprint(root: Path, spec: dict) -> dict[str, str]:
    """Hashes of the committed files that define the expectations; a kit built from older ones is stale."""
    result = {}
    for relative in spec["descriptors"]:
        path = root / relative
        result[relative] = sha256_file(path)[0] if path.is_file() else "missing"
    return result


# --------------------------------------------------------------------------------------- verification


@dataclass
class Row:
    id: str
    kind: str
    level: str
    status: str
    code: str
    message: str
    fix: str = ""
    path: str = ""
    size: int | None = None
    sha256: str | None = None
    details: dict = field(default_factory=dict)


def effective_status(level: str, problem: bool, blocked: bool = False) -> str:
    if level == "not-used":
        return "SKIP"
    if blocked:
        return "BLOCKED" if level == "required" else "WARN"
    if problem:
        return "FAIL" if level == "required" else "WARN"
    return "PASS"


def verify_artifact(exp: Expected, path: Path | None, display: str) -> Row:
    """Size, hash and platform of one artifact file; never trusts anything written by the input."""
    base = dict(id=exp.id, kind=exp.kind, level=exp.level, path=display)
    if exp.blocked:
        return Row(**base, status=effective_status(exp.level, True, blocked=True), code="unverifiable",
                   message=f"ожидаемое значение не установлено: {exp.blocked}", fix=exp.prepare)
    if path is None or not path.is_file():
        what = f"нет файла {display}" + (f" (ожидается {human(exp.size)}, sha256 {exp.sha256[:12]}…)" if exp.sha256 else "")
        return Row(**base, status=effective_status(exp.level, True), code="missing", message=what,
                   fix=f"Подготовить заранее (с интернетом): {exp.prepare}")
    if exp.kind == "model-companion":  # generated by the module's prepare; content is validated separately
        actual, size = sha256_file(path)
        return Row(**base, status=effective_status(exp.level, False), code="ok", size=size, sha256=actual,
                   message=f"{display}: служебный файл модуля на месте")
    if exp.kind == "npm-tarball":
        algo, expected_hex = sri_to_hex(exp.integrity)
        digests, size = multi_digest(path, (algo, "sha256"))
        if digests[algo] != expected_hex:
            return Row(**base, status=effective_status(exp.level, True), code="integrity_mismatch", size=size,
                       sha256=digests["sha256"], message=f"{display}: содержимое не совпадает с integrity из package-lock.json",
                       fix=f"Удалите файл и подготовьте кэш заново: {exp.prepare}")
        return Row(**base, status=effective_status(exp.level, False), code="ok", size=size, sha256=digests["sha256"],
                   message="integrity совпадает с package-lock.json")
    actual, size = sha256_file(path)
    details = {}
    if exp.size is not None and size != exp.size:
        return Row(**base, status=effective_status(exp.level, True), code="size_mismatch", size=size, sha256=actual,
                   message=f"{display}: размер {size} Б, ожидается {exp.size} Б (файл обрезан или чужой)",
                   fix=f"Замените файл заново подготовленным: {exp.prepare}")
    accepted = set(exp.extra.get("accepted_sha256", [])) | {exp.sha256}
    if exp.kind == "python-wheel":
        try:
            name, version, tags = parse_wheel_name(path.name)
        except ValueError:
            name, version, tags = "", "", set()
        ok, tag = wheel_platform_verdict(tags)
        if not ok:
            return Row(**base, status=effective_status(exp.level, True), code="wrong_platform", size=size, sha256=actual,
                       message=f"{path.name}: колесо для {tag or 'неизвестной платформы'}, нужно cp312 win_amd64",
                       fix=f"Скачайте колесо для Windows x64 / CPython 3.12: {exp.prepare}")
        if actual not in accepted:
            return Row(**base, status=effective_status(exp.level, True), code="sha256_mismatch", size=size, sha256=actual,
                       message=f"{path.name}: sha256 {actual[:12]}… не входит в --hash из requirements",
                       fix=f"Замените файл: {exp.prepare}")
        details["license"] = wheel_license(path)
        details["tag"] = tag
    elif actual != exp.sha256:
        return Row(**base, status=effective_status(exp.level, True), code="sha256_mismatch", size=size, sha256=actual,
                   message=f"{display}: sha256 {actual[:12]}…, ожидается {exp.sha256[:12]}… (повреждён или другая версия)",
                   fix=f"Замените файл заново подготовленным: {exp.prepare}")
    if exp.kind == "electron-zip":
        try:
            with zipfile.ZipFile(path) as archive:
                with archive.open("electron.exe") as exe:
                    machine = pe_machine(exe.read(4096))
                version = archive.read("version").decode("ascii", "replace").strip().lstrip("v")
                details["license_files"] = [n for n in ("LICENSE", "LICENSES.chromium.html") if n in archive.namelist()]
        except (KeyError, zipfile.BadZipFile, OSError) as exc:
            return Row(**base, status=effective_status(exp.level, True), code="wrong_platform", size=size, sha256=actual,
                       message=f"{display}: в архиве нет electron.exe/version ({type(exc).__name__})", fix=exp.prepare)
        details.update(electron_exe=machine, version=version)
        if machine != "x64" or version != exp.extra["version"]:
            return Row(**base, status=effective_status(exp.level, True), code="wrong_platform", size=size, sha256=actual,
                       message=f"{display}: electron.exe={machine}, version={version}; нужно x64 {exp.extra['version']}",
                       fix=exp.prepare)
    return Row(**base, status=effective_status(exp.level, False), code="ok", size=size, sha256=actual,
               message=f"{display}: {human(size)}, sha256 совпадает", details=details)


def validate_companion(exp: Expected, path: Path, root: Path) -> tuple[bool, str]:
    rule = exp.extra.get("validate")
    if not rule:
        return True, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"не читается как JSON ({type(exc).__name__})"
    if rule == "audio-manifest":
        pinned = _audio_constants(root)
        if data.get("sha256") != pinned["sha256"] or data.get("revision") != pinned["revision"]:
            return False, "sha256/revision не совпадают с backend/proctor/audio/assets.py"
    elif rule == "identity-manifest":
        committed = json.loads((root / "backend/proctor/identity/models.manifest.json").read_text(encoding="utf-8"))
        if data.get("models") != committed.get("models"):
            return False, "копия манифеста не совпадает с backend/proctor/identity/models.manifest.json"
    return True, ""


# ------------------------------------------------------------------------------------ input locators


def locate_inputs(expected: list[Expected], args) -> dict[str, tuple[Path | None, str]]:
    """Where each expected artifact is found among the operator-supplied local inputs."""
    found: dict[str, tuple[Path | None, str]] = {}
    for exp in expected:
        path, label = None, "—"
        if exp.kind in ("model", "model-companion"):
            relative = exp.extra["file"]
            module = exp.extra["module"]
            candidates = []
            if module == "audio" and args.audio_models_dir:
                candidates.append((Path(args.audio_models_dir) / PurePosixPath(relative).name, "--audio-models-dir"))
            if args.models_dir:
                candidates.append((Path(args.models_dir) / relative, "--models-dir"))
            for candidate, flag in candidates:
                if candidate.is_file():
                    path, label = candidate, f"{flag}/{relative}"
                    break
            else:
                label = f"{candidates[0][1] if candidates else '--models-dir'}/{relative}"
        elif exp.kind == "python-wheel":
            name = PurePosixPath(exp.kit_path).name
            if args.wheels_dir:
                candidate = Path(args.wheels_dir) / name
                path = candidate if candidate.is_file() else None
                if path is None:  # a same-name/version wheel for another platform is a useful diagnosis
                    for other in sorted(Path(args.wheels_dir).glob("*.whl")):
                        try:
                            other_name, other_version, _ = parse_wheel_name(other.name)
                        except ValueError:
                            continue
                        if other_name == exp.extra["name"] and other_version == exp.extra["version"]:
                            path = other
                            break
            label = f"--wheels-dir/{path.name if path else name}"
        elif exp.kind == "electron-zip":
            filename = exp.extra["filename"]
            if args.electron_zip:
                candidate = Path(args.electron_zip)
                path = candidate if candidate.is_file() else None
                label = "--electron-zip"
            elif args.electron_cache:
                cache = Path(args.electron_cache)
                direct = cache / PurePosixPath(exp.kit_path).relative_to("electron-cache")
                matches = [direct] if direct.is_file() else sorted(cache.rglob(filename))
                path = matches[0] if matches else None
                label = f"--electron-cache/…/{filename}"
            else:
                label = f"--electron-zip ({filename})"
        elif exp.kind == "npm-tarball":
            if args.npm_cache:
                candidate = Path(args.npm_cache) / PurePosixPath(exp.kit_path).relative_to("npm-cache")
                path = candidate if candidate.is_file() else None
            label = f"--npm-cache/{PurePosixPath(exp.kit_path).relative_to('npm-cache')}"
        found[exp.id] = (path, label)
    return found


# ------------------------------------------------------------------------------------------- reports


def summarize(rows: list[Row]) -> dict:
    counts = {status: sum(1 for r in rows if r.status == status) for status in STATUS_ORDER}
    return {"counts": counts, "ready": counts["FAIL"] == 0 and counts["BLOCKED"] == 0,
            "failed": [r.id for r in rows if r.status == "FAIL"], "blocked": [r.id for r in rows if r.status == "BLOCKED"]}


def print_rows(title: str, rows: list[Row], verbose: bool, out=sys.stdout) -> None:
    print(title, file=out)
    shown = 0
    for row in sorted(rows, key=lambda r: (STATUS_ORDER[r.status], r.kind, r.id)):
        if row.status in ("PASS", "SKIP") and not verbose:
            continue
        shown += 1
        print(f"  [{row.status}] {row.id}: {row.message}", file=out)
        if row.fix and row.status in ("FAIL", "BLOCKED", "WARN"):
            print(f"         как исправить: {row.fix}", file=out)
    passed = sum(1 for r in rows if r.status == "PASS")
    if not verbose and passed:
        print(f"  [PASS] ещё {passed} проверок без замечаний (подробно: --verbose)", file=out)
    if not rows:
        print("  (нет проверок для этого сценария)", file=out)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def report_out(args, payload: dict, forbidden: list[Path]) -> None:
    if not args.json_out:
        return
    target = Path(args.json_out)
    for root in forbidden:
        if inside(target, root):
            raise UsageError(f"--json-out не должен указывать внутрь {root} (проверка не пишет в комплект/исходники)")
    write_json(target, payload)


def final_line(kind: str, summary: dict) -> str:
    counts = summary["counts"]
    verdict = "READY" if summary["ready"] else "NOT_READY"
    return (f"ADAL_OFFLINE_{kind} {verdict} fail={counts['FAIL']} blocked={counts['BLOCKED']} "
            f"warn={counts['WARN']} pass={counts['PASS']}")


# ------------------------------------------------------------------------------------------ commands


def cmd_inventory(args, root: Path, spec: dict) -> int:
    features = set(args.feature or [])
    expected = collect_expected(root, spec, args.scenario, features, Path(args.npm_cache) if args.npm_cache else None)
    payload = {"schema": "adal.offline-inventory/1", "tool_version": TOOL_VERSION, "target": TARGET["id"],
               "scenario": args.scenario, "features": sorted(features), "source_commit": read_git_head(root),
               "descriptors": descriptor_fingerprint(root, spec),
               "artifacts": [asdict(e) for e in expected if e.level != "not-used"],
               "not_used": [e.id for e in expected if e.level == "not-used"],
               "external_prerequisites": spec["external_prerequisites"]}
    if args.out:
        out = Path(args.out)
        if inside(out, root):
            raise UsageError("--out внутри репозитория запрещён: отчёт держите вне исходников")
        write_json(out, payload)
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
    total = sum(e.size or 0 for e in expected if e.level != "not-used")
    blocked = [e.id for e in expected if e.blocked and e.level != "not-used"]
    print(f"inventory: {len([e for e in expected if e.level != 'not-used'])} артефактов, известный объём {human(total)}, "
          f"не установлено ожидание: {len(blocked)}", file=sys.stderr)
    return EXIT_READY


def cmd_plan(args, root: Path, spec: dict) -> int:
    """Print what must be prepared ONLINE for this scenario (nothing is executed)."""
    features = set(args.feature or [])
    expected = [e for e in collect_expected(root, spec, args.scenario, features,
                                            Path(args.npm_cache) if args.npm_cache else None) if e.level != "not-used"]
    present = {}
    if args.kit:
        kit = Path(args.kit)
        present = {e.id: (kit / e.kit_path).is_file() for e in expected}
    print(f"Сценарий {args.scenario}, цель {TARGET['id']}. Этот вывод ничего не скачивает и не запускает.")
    print("Стадия ПОДГОТОВКИ С ИНТЕРНЕТОМ (выполнить до отключения):")
    for prerequisite in spec["external_prerequisites"]:
        print(f"  • {prerequisite['name']}: {prerequisite['how']}")
    groups: dict[str, list[str]] = {}
    for exp in expected:
        if args.kit and present.get(exp.id):
            continue
        label = "обязательно" if exp.level == "required" else f"только для --feature {exp.level.split(':', 1)[1]}"
        commands = groups.setdefault(label, [])
        if exp.prepare not in commands:
            commands.append(exp.prepare)
    for label in sorted(groups, key=lambda x: (x != "обязательно", x)):
        print(f"  [{label}]")
        for command in groups[label]:
            print(f"    • {command}")
    if args.kit and not groups:
        print("  • в комплекте уже есть все файлы этого сценария; проверьте его командой check")
    blocked = [e for e in expected if e.license.get("distribution") == "BLOCKED_PENDING_DECISION"]
    for exp in blocked:
        print(f"  ! {exp.id}: распространение не решено — готовьте на каждом ПК (build --per-pc {exp.extra['module']}) "
              f"или после решения команды: build --accept-distribution {model_key(exp)}")
    print("Затем без интернета: build → check → установка по README-OFFLINE.txt → check-install.")
    return EXIT_READY


def guard_output(out: Path, root: Path, inputs: list[Path]) -> None:
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise UsageError(f"--out {out} уже существует и не пуст: укажите новый каталог (комплект не перезаписывается)")
    if inside(out, root):
        raise UsageError("--out внутри репозитория запрещён: веса и колёса не должны попадать в git")
    for source in inputs:
        if inside(out, source) or inside(source, out):
            raise UsageError(f"--out пересекается с входным каталогом {source}: входы только читаются")


def model_key(exp: Expected) -> str:
    """Name used by --accept-distribution / --per-pc: the manifest model_id or the module id."""
    return exp.id.split(":", 1)[1] if exp.kind == "model" else exp.extra.get("module", exp.id)


def apply_decisions(exp: Expected, row: Row, accepted: set[str], per_pc: set[str]) -> Row:
    """Distribution gate and explicit per-PC preparation; both are recorded, never implicit."""
    if exp.kind not in ("model", "model-companion") or row.status == "SKIP":
        return row
    module = exp.extra.get("module")
    if module in per_pc or model_key(exp) in per_pc:
        return Row(id=exp.id, kind=exp.kind, level=exp.level, status="WARN", code="per_pc",
                   message="не входит в комплект по решению оператора (--per-pc); готовится на каждом ПК",
                   fix=f"На каждом ПК до отключения: {exp.prepare}; затем check-install", path=row.path)
    if exp.license.get("distribution") == "BLOCKED_PENDING_DECISION" and exp.kind == "model" \
            and model_key(exp) not in accepted and row.status in ("PASS", "WARN"):
        return Row(id=exp.id, kind=exp.kind, level=exp.level, status=effective_status(exp.level, True, blocked=True),
                   code="distribution_blocked", path=row.path, size=row.size, sha256=row.sha256,
                   message=f"файл исправен, но распространение не решено. {exp.license.get('distribution_note')}",
                   fix=f"Либо --per-pc {module} (каждый ПК: {exp.prepare}), либо после решения команды "
                       f"--accept-distribution {model_key(exp)}")
    return row


def build_rows(expected: list[Expected], located: dict, root: Path, accepted: set[str], per_pc: set[str]) -> list[tuple[Expected, Path | None, Row]]:
    result = []
    for exp in expected:
        if exp.level == "not-used":
            continue
        path, label = located[exp.id]
        row = verify_artifact(exp, path, label)
        if exp.kind == "model-companion" and row.code == "ok":
            ok, why = validate_companion(exp, path, root)
            if not ok:
                row = Row(id=exp.id, kind=exp.kind, level=exp.level, path=label, status=effective_status(exp.level, True),
                          code="invalid_companion", message=f"{label}: {why}", fix=exp.prepare)
        row = apply_decisions(exp, row, accepted, per_pc)
        result.append((exp, path, row))
    return result


def cmd_build(args, root: Path, spec: dict) -> int:
    features = set(args.feature or [])
    accepted, per_pc = set(args.accept_distribution or []), set(args.per_pc or [])
    npm_cache = Path(args.npm_cache) if args.npm_cache else None
    expected = collect_expected(root, spec, args.scenario, features, npm_cache)
    out = Path(args.out)
    inputs = [Path(p) for p in (args.models_dir, args.audio_models_dir, args.wheels_dir, args.electron_cache,
                                args.npm_cache) if p]
    if args.electron_zip:
        inputs.append(Path(args.electron_zip))
    guard_output(out, root, inputs)
    triples = build_rows(expected, locate_inputs(expected, args), root, accepted, per_pc)
    rows = [row for _exp, _path, row in triples]
    summary = summarize(rows)
    if not summary["ready"] and not args.allow_incomplete:
        print_rows(f"Комплект НЕ собран ({args.scenario}, {TARGET['id']}): исправьте входные файлы.", rows, args.verbose)
        print(final_line("BUILD", summary))
        return EXIT_NOT_READY
    selected = [(exp, path, row) for exp, path, row in triples if row.code == "ok" and path is not None]
    staging = out.with_name(out.name + ".partial")
    if staging.exists():
        raise UsageError(f"найден незавершённый {staging}: удалите его вручную и повторите")
    staging.mkdir(parents=True)
    try:
        artifacts = []
        for exp, path, row in selected:
            target = staging / exp.kit_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            copied_sha, copied_size = sha256_file(target)
            if copied_sha != row.sha256:
                raise OSError(f"copy of {exp.id} changed while copying")
            license_info = dict(exp.license)
            if exp.kind == "python-wheel":
                license_info = row.details.get("license", license_info)
            if exp.kind == "electron-zip":
                license_info["license_files_in_zip"] = row.details.get("license_files", [])
            if exp.kind == "model" and model_key(exp) in accepted:
                license_info["distribution_decision"] = "accepted by operator: --accept-distribution " + model_key(exp)
            artifacts.append({
                "id": exp.id, "kind": exp.kind, "path": exp.kit_path, "level": exp.level,
                "size": copied_size, "sha256": copied_sha, "integrity": exp.integrity,
                "expected_sha256": exp.sha256, "expected_size": exp.size, "source": exp.source,
                "license": license_info,
                "compatibility": {**exp.compatibility, **{k: v for k, v in row.details.items()
                                                          if k in ("tag", "electron_exe", "version")}},
                "install": exp.install})
        status_of = lambda lic: lic.get("status", lic.get("license_status"))
        manifest = {
            "schema": MANIFEST_SCHEMA, "tool_version": TOOL_VERSION,
            "created_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "target": {k: (list(v) if isinstance(v, tuple) else v) for k, v in TARGET.items()},
            "scenario": args.scenario, "features": sorted(features), "source_commit": read_git_head(root),
            "descriptors": descriptor_fingerprint(root, spec),
            "technical_ready": summary["ready"],
            "per_pc": sorted(per_pc), "accepted_distribution": sorted(accepted),
            "build_findings": [asdict(r) for r in rows if r.status != "PASS"],
            "artifacts": artifacts,
            "distribution": {
                "conditional": sorted(a["id"] for a in artifacts
                                      if a["license"].get("distribution") in ("BLOCKED_PENDING_DECISION",)),
                "with_notice": sorted(a["id"] for a in artifacts if a["license"].get("distribution") == "PERMITTED_WITH_NOTICE"),
                "license_unconfirmed": sorted(a["id"] for a in artifacts
                                              if status_of(a["license"]) not in ("CONFIRMED", "DECLARED", "N/A")),
                "note": spec["distribution_note"]},
            "external_prerequisites": spec["external_prerequisites"],
            "excluded_by_design": spec["excluded_by_design"],
        }
        write_json(staging / MANIFEST_NAME, manifest)
        sums = [f"{a['sha256']}  {a['path']}" for a in artifacts] + \
               [f"{sha256_file(staging / MANIFEST_NAME)[0]}  {MANIFEST_NAME}"]
        (staging / SUMS_NAME).write_text("\n".join(sums) + "\n", encoding="utf-8")
        (staging / README_NAME).write_text(render_kit_readme(spec, manifest), encoding="utf-8")
        if out.exists():
            out.rmdir()  # guard_output allowed only an empty directory
        os.replace(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print_rows(f"Комплект собран: {out} ({args.scenario}, {TARGET['id']}, {len(artifacts)} файлов, "
               f"{human(sum(a['size'] for a in artifacts))}).", rows, args.verbose)
    if manifest["distribution"]["conditional"]:
        print(f"  [WARN] распространение по решению оператора: {', '.join(manifest['distribution']['conditional'])}")
    if manifest["distribution"]["license_unconfirmed"]:
        print(f"  [WARN] лицензия не подтверждена метаданными ({len(manifest['distribution']['license_unconfirmed'])}): "
              f"{', '.join(manifest['distribution']['license_unconfirmed'])} — для передачи третьим лицам BLOCKED")
    print(final_line("BUILD", summary))
    return EXIT_READY if summary["ready"] else EXIT_NOT_READY


def render_kit_readme(spec: dict, manifest: dict) -> str:
    lines = [
        "Adal: офлайн-комплект (" + manifest["scenario"] + ", " + manifest["target"]["id"] + ")",
        "",
        "Комплект содержит только заранее подготовленные файлы с проверенными SHA-256.",
        "Исходники, .venv, данные экзаменов, PIN и токены в комплект не входят.",
        "",
        "Проверка комплекта (без сети, ничего не записывает):",
        "  <python> proctoring\\acceptance\\offline\\adal_offline_kit.py check --kit \"<этот каталог>\"",
        "Без инструмента: сравните SHA256SUMS.txt с Get-FileHash -Algorithm SHA256.",
        "",
    ]
    if manifest["per_pc"]:
        lines += ["Не входит в комплект, готовится на каждом ПК до отключения интернета: " + ", ".join(manifest["per_pc"]), ""]
    lines += ["Установка на ПК без интернета:"]
    lines += [f"  - {step}" for step in spec["install_steps"]]
    lines += ["", "После установки, в том же окне PowerShell, где будет запускаться launcher:",
              "  <python> proctoring\\acceptance\\offline\\adal_offline_kit.py check-install --scenario " + manifest["scenario"], ""]
    return "\n".join(lines)


def cmd_check(args, root: Path, spec: dict) -> int:
    kit = Path(args.kit)
    rows: list[Row] = []
    manifest_path = kit / MANIFEST_NAME
    if not kit.is_dir() or not manifest_path.is_file():
        rows.append(Row(id="kit:manifest", kind="kit", level="required", status="FAIL", code="missing",
                        message=f"нет {MANIFEST_NAME} в {kit}", fix="Соберите комплект командой build"))
        return finish_check(args, rows, kit, None)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise ValueError(f"schema {manifest.get('schema')!r}")
    except (OSError, ValueError) as exc:
        rows.append(Row(id="kit:manifest", kind="kit", level="required", status="FAIL", code="invalid_manifest",
                        message=f"{MANIFEST_NAME} не читается: {exc}", fix="Соберите комплект заново командой build"))
        return finish_check(args, rows, kit, None)
    scenario = args.scenario or manifest.get("scenario")
    features = set(args.feature or manifest.get("features") or [])
    per_pc = set(manifest.get("per_pc") or [])
    accepted = set(manifest.get("accepted_distribution") or [])
    target_id = manifest.get("target", {}).get("id")
    rows.append(Row(id="kit:target", kind="kit", level="required",
                    status="PASS" if target_id == TARGET["id"] else "FAIL",
                    code="ok" if target_id == TARGET["id"] else "wrong_platform",
                    message=f"цель комплекта {target_id}, нужна {TARGET['id']}",
                    fix="" if target_id == TARGET["id"] else "Соберите комплект для win-x64"))
    current = descriptor_fingerprint(root, spec)
    recorded = manifest.get("descriptors", {})
    changed = sorted(k for k in set(current) | set(recorded) if current.get(k) != recorded.get(k))
    rows.append(Row(id="kit:freshness", kind="kit", level="required", status="FAIL" if changed else "PASS",
                    code="stale_kit" if changed else "ok",
                    message=("комплект собран для другой версии манифестов/lock-файлов: " + ", ".join(changed))
                    if changed else f"манифесты и lock-файлы совпадают с этой копией (комплект из {manifest.get('source_commit')})",
                    fix="Пересоберите комплект из этой версии репозитория (build)" if changed else ""))
    npm_cache = kit / "npm-cache" if (kit / "npm-cache").is_dir() else None
    expected = collect_expected(root, spec, scenario, features, npm_cache)
    for exp in expected:
        if exp.level == "not-used":
            continue
        target = kit / exp.kit_path
        row = verify_artifact(exp, target if target.is_file() else None, exp.kit_path)
        if exp.kind == "python-wheel" and row.code == "missing" and (kit / "wheels").is_dir():
            # A same-version wheel for another platform in the kit is a wrong-platform kit; report it as such.
            for other in sorted((kit / "wheels").glob("*.whl")):
                try:
                    other_name, other_version, _ = parse_wheel_name(other.name)
                except ValueError:
                    continue
                if other_name == exp.extra["name"] and other_version == exp.extra["version"]:
                    row = verify_artifact(exp, other, f"wheels/{other.name}")
                    break
        if exp.kind == "model-companion" and row.code == "ok":
            ok, why = validate_companion(exp, target, root)
            if not ok:
                row = Row(id=exp.id, kind=exp.kind, level=exp.level, status=effective_status(exp.level, True),
                          code="invalid_companion", message=f"{exp.kit_path}: {why}", fix=exp.prepare, path=exp.kit_path)
        if row.code in ("missing", "ok"):
            row = apply_decisions(exp, row, accepted, per_pc)
        rows.append(row)
    known = {e.kit_path for e in expected} | {a["path"] for a in manifest.get("artifacts", [])} | \
            {MANIFEST_NAME, SUMS_NAME, README_NAME}
    for dirpath, _dirs, files in os.walk(kit):
        for name in files:
            relative = (Path(dirpath) / name).relative_to(kit).as_posix()
            if relative not in known and not relative.startswith("npm-cache/_cacache/index-v5/"):
                rows.append(Row(id=f"kit:extra:{relative}", kind="kit", level="optional:extra", status="WARN",
                                code="extra_file", message=f"лишний файл в комплекте: {relative}",
                                fix="Проверьте происхождение; комплект не должен содержать данных/секретов"))
    return finish_check(args, rows, kit, manifest)


def finish_check(args, rows: list[Row], kit: Path, manifest: dict | None) -> int:
    summary = summarize(rows)
    payload = {"schema": "adal.offline-check/1", "tool_version": TOOL_VERSION, "mode": "kit", "target": TARGET["id"],
               "scenario": args.scenario or (manifest or {}).get("scenario"), "summary": summary,
               "network_used": False, "processes_started": False, "rows": [asdict(r) for r in rows]}
    report_out(args, payload, [kit])
    print_rows(f"Проверка офлайн-комплекта {kit} (без сети, только чтение):", rows, args.verbose)
    print(final_line("CHECK", summary))
    return EXIT_READY if summary["ready"] else EXIT_NOT_READY


# ---------------------------------------------------------------------------------- installed state


def installed_distributions(paths: list[str] | None = None) -> dict[str, tuple[str, list[str], object]]:
    import importlib.metadata as metadata

    result = {}
    for dist in metadata.distributions(path=paths) if paths is not None else metadata.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        wheel = dist.read_text("WHEEL") or ""
        tags = [line.split(":", 1)[1].strip() for line in wheel.splitlines() if line.startswith("Tag:")]
        result.setdefault(normalize_name(name), (dist.version, tags, dist))
    return result


def verify_record(dist) -> list[str]:
    """Files of an installed distribution whose sha256 differs from RECORD (deep check)."""
    bad = []
    for entry in dist.files or []:
        if not entry.hash or entry.hash.mode != "sha256":
            continue
        try:
            data = entry.locate()
            digest = base64.urlsafe_b64encode(digest_file(Path(data), "sha256")).rstrip(b"=").decode()
        except OSError:
            bad.append(str(entry))
            continue
        if digest != entry.hash.value:
            bad.append(str(entry))
    return bad


def models_dir_for_runtime(args, root: Path) -> tuple[Path, str]:
    if args.models_dir:
        return Path(args.models_dir), "--models-dir"
    env = os.environ.get("QORGAU_MODELS_DIR")
    if env:
        return Path(env).expanduser(), "QORGAU_MODELS_DIR"
    return root / "models", "по умолчанию backend (proctoring/models; QORGAU_MODELS_DIR не задан)"


def audio_dir_for_runtime(args) -> tuple[Path | None, str]:
    if args.audio_models_dir:
        return Path(args.audio_models_dir), "--audio-models-dir"
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None, "LOCALAPPDATA не задан"
    return Path(base) / "QorgauExam" / "models" / "audio", "%LOCALAPPDATA%\\QorgauExam\\models\\audio (жёстко в audio/assets.py)"


def desktop_staleness(desktop: Path, spec: dict) -> tuple[bool, str]:
    """Same rule as Start-Student.ps1: any build input newer than the oldest dist artifact = stale."""
    artifacts = [desktop / p for p in spec["desktop"]["build_outputs"]]
    missing = [p for p in artifacts if not p.is_file()]
    if missing:
        return False, "нет " + ", ".join(p.relative_to(desktop).as_posix() for p in missing)
    oldest = min(p.stat().st_mtime for p in artifacts)
    newer = []
    for relative in spec["desktop"]["build_input_dirs"]:
        base = (desktop / relative)
        if base.is_dir():
            newer += [p for p in base.rglob("*") if p.is_file() and p.stat().st_mtime > oldest]
    for relative in spec["desktop"]["build_input_files"]:
        path = desktop / relative
        if path.is_file() and path.stat().st_mtime > oldest:
            newer.append(path)
    if newer:
        return False, f"исходники новее сборки ({len(newer)} файлов, например {newer[0].name}); launcher откажется запускать"
    return True, "сборка актуальна по правилу Start-Student.ps1"


def cmd_check_install(args, root: Path, spec: dict) -> int:
    scenario, features = args.scenario, set(args.feature or [])
    sc = spec["scenarios"].get(scenario)
    if sc is None:
        raise UsageError(f"unknown scenario {scenario!r}")
    rows: list[Row] = []
    on_windows = sys.platform == "win32"
    platform_tag = sysconfig.get_platform()
    py_ok = sys.version_info[:2] == TARGET["python"] and sys.implementation.name == "cpython"
    rows.append(Row(id="python:interpreter", kind="python", level="required", status="PASS" if py_ok else "FAIL",
                    code="ok" if py_ok else "wrong_python",
                    message=f"{sys.implementation.name} {sys.version.split()[0]}",
                    fix="" if py_ok else "Запускайте проверку тем же Python 3.12 x64, что и launcher (proctoring\\.venv)."))
    plat_ok = platform_tag == TARGET["sysconfig_platform"]
    rows.append(Row(id="python:platform", kind="python", level="required", status="PASS" if plat_ok else "FAIL",
                    code="ok" if plat_ok else "wrong_platform", message=f"платформа интерпретатора {platform_tag}",
                    fix="" if plat_ok else "Нужен Windows x64 и CPython 3.12 x64 (не 32-бит, не ARM64, не WSL)."))
    # Python packages: version pins + platform tags of what is installed.
    reqfile = sc.get("python_requirements")
    if reqfile:
        env = marker_environment()
        installed = installed_distributions()
        install_hint = spec["wheels"]["install"].replace("{requirements}", reqfile.replace("/", "\\"))
        # Tags of the exact wheel uv.lock selects for win-x64: a pure-Python wheel of another platform
        # (e.g. sounddevice py3-none-any without the bundled PortAudio DLL) must not pass as the Windows one.
        lock_tags = {e.extra["name"]: parse_wheel_name(PurePosixPath(e.kit_path).name)[2]
                     for e in expected_wheels(root, spec, scenario) if not e.blocked}
        for req in parse_requirements(root / reqfile):
            if req.marker and not evaluate_marker(req.marker, env):
                continue
            have = installed.get(req.name)
            rid = f"python-package:{req.name}"
            if have is None:
                rows.append(Row(id=rid, kind="python-package", level="required", status="FAIL", code="missing",
                                message=f"{req.name}=={req.version} не установлен", fix=install_hint))
                continue
            version, tags, dist = have
            if version != req.version:
                rows.append(Row(id=rid, kind="python-package", level="required", status="FAIL", code="version_mismatch",
                                message=f"{req.name} {version}, закреплено {req.version}", fix=install_hint))
                continue
            parsed = set()
            for tag in tags:
                py, _, rest = tag.partition("-")
                abi, _, plat = rest.partition("-")
                parsed.add((py, abi, plat))
            ok, desc = wheel_platform_verdict(parsed) if parsed else (False, "нет Tag в WHEEL")
            wanted = lock_tags.get(req.name)
            if ok and wanted and not (parsed & wanted):
                ok, desc = False, f"{desc}; для win-x64 закреплено " + ", ".join(sorted("-".join(t) for t in wanted))
            if not ok:
                rows.append(Row(id=rid, kind="python-package", level="required", status="FAIL", code="wrong_platform",
                                message=f"{req.name} установлен из колеса {desc} (не для cp312 win_amd64)",
                                fix="Пересоздайте .venv на этом ПК из колёс win_amd64: " + install_hint))
                continue
            if args.deep:
                broken = verify_record(dist)
                if broken:
                    rows.append(Row(id=rid, kind="python-package", level="required", status="FAIL", code="corrupt_install",
                                    message=f"{req.name}: {len(broken)} файлов не совпадают с RECORD (например {broken[0]})",
                                    fix="Переустановите пакет из комплекта: " + install_hint))
                    continue
            rows.append(Row(id=rid, kind="python-package", level="required", status="PASS", code="ok",
                            message=f"{req.name} {version} ({desc})"))
    # Models at the locations the runtime actually reads.
    models_dir, models_origin = models_dir_for_runtime(args, root)
    audio_dir, audio_origin = audio_dir_for_runtime(args)
    model_rows = expected_models(root, spec, scenario, features)
    uses_models = any(e.level != "not-used" for e in model_rows)
    if uses_models and not args.models_dir and not os.environ.get("QORGAU_MODELS_DIR"):
        rows.append(Row(id="models:location", kind="model", level="optional:location", status="WARN", code="implicit_dir",
                        message="QORGAU_MODELS_DIR не задан: backend читает proctoring\\models, а identity.prepare по "
                                "умолчанию пишет в %LOCALAPPDATA%\\QorgauExam\\models",
                        fix="Задайте один каталог для всех: $env:QORGAU_MODELS_DIR = \"$env:LOCALAPPDATA\\QorgauExam\\models\""))
    for exp in model_rows:
        if exp.level == "not-used":
            continue
        if exp.extra["module"] == "audio":
            if audio_dir is None:
                rows.append(Row(id=exp.id, kind=exp.kind, level=exp.level, status=effective_status(exp.level, True),
                                code="missing", message=f"каталог аудио-модели не определён: {audio_origin}",
                                fix=exp.prepare))
                continue
            path = audio_dir / PurePosixPath(exp.extra["file"]).name
            display = f"{audio_origin}: {path.name}"
        else:
            path = models_dir / exp.extra["file"]
            display = f"{models_origin}: {exp.extra['file']}"
        row = verify_artifact(exp, path if path.is_file() else None, display)
        if exp.kind == "model-companion" and row.code == "ok":
            ok, why = validate_companion(exp, path, root)
            if not ok:
                row = Row(id=exp.id, kind=exp.kind, level=exp.level, status=effective_status(exp.level, True),
                          code="invalid_companion", message=f"{display}: {why}", fix=exp.prepare)
        if row.code == "missing":
            row.fix = f"Скопируйте из комплекта models/{exp.extra['file']} или подготовьте онлайн: {exp.prepare}"
        rows.append(row)
    # Electron runtime and desktop build.
    electron_level = spec["electron"]["levels"].get(scenario, "not-used")
    if electron_level != "not-used":
        desktop = root / "desktop"
        version = npm_lock(root)["packages"]["node_modules/electron"]["version"]
        electron_dir = desktop / "node_modules" / "electron"
        exe = electron_dir / "dist" / "electron.exe"
        problems = []
        try:
            installed_version = (electron_dir / "dist" / "version").read_text(encoding="utf-8").strip().lstrip("v")
            if installed_version != version:
                problems.append(f"dist/version={installed_version}, в package-lock {version}")
        except OSError:
            problems.append("нет node_modules/electron/dist/version")
        try:
            if (electron_dir / "path.txt").read_text(encoding="utf-8").strip() != "electron.exe":
                problems.append("path.txt указывает не на electron.exe (бинарник другой платформы)")
        except OSError:
            problems.append("нет node_modules/electron/path.txt")
        machine = pe_machine_of(exe) if exe.is_file() else "missing"
        if machine != "x64":
            problems.append(f"electron.exe: {machine}")
        rows.append(Row(id=f"electron:installed:{version}", kind="electron", level=electron_level,
                        status=effective_status(electron_level, bool(problems)),
                        code="ok" if not problems else ("missing" if machine == "missing" else "wrong_platform"),
                        message="; ".join(problems) if problems else f"Electron {version} x64 установлен",
                        fix="" if not problems else spec["electron"]["install"]))
        fresh, why = desktop_staleness(desktop, spec)
        rows.append(Row(id="desktop:build", kind="desktop", level=electron_level,
                        status=effective_status(electron_level, not fresh), code="ok" if fresh else "stale_or_missing",
                        message=why, fix="" if fresh else spec["desktop"]["fix"]))
    for relative in sc.get("required_files", []):
        path = root / relative
        rows.append(Row(id=f"file:{relative}", kind="source-file", level="required",
                        status="PASS" if path.is_file() else "FAIL", code="ok" if path.is_file() else "missing",
                        message=relative + (" на месте" if path.is_file() else " отсутствует в этой копии"),
                        fix="" if path.is_file() else "Используйте полную копию репозитория той же версии"))
    summary = summarize(rows)
    payload = {"schema": "adal.offline-check/1", "tool_version": TOOL_VERSION, "mode": "install", "target": TARGET["id"],
               "scenario": scenario, "features": sorted(features), "host_is_target": on_windows and plat_ok,
               "source_commit": read_git_head(root), "summary": summary, "network_used": False,
               "processes_started": False, "rows": [asdict(r) for r in rows],
               "not_verified_here": spec["not_verified_by_check"]}
    report_out(args, payload, [root])
    print_rows(f"Проверка готовности этого ПК к офлайн-запуску ({scenario}, без сети, только чтение):", rows, args.verbose)
    print(final_line("INSTALL", summary))
    return EXIT_READY if summary["ready"] else EXIT_NOT_READY


# ----------------------------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adal_offline_kit.py", description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="каталог proctoring/ (по умолчанию: этот репозиторий)")
    parser.add_argument("--spec", default=str(SPEC_PATH), help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, scenario_default="student"):
        p.add_argument("--scenario", default=scenario_default, help="teacher | student")
        p.add_argument("--feature", action="append", choices=("identity", "audio", "npm-offline"),
                       help="сделать необязательный компонент обязательным (можно несколько раз)")
        p.add_argument("--verbose", "-v", action="store_true")

    p = sub.add_parser("inventory", help="список ожидаемых артефактов из манифестов/lock-файлов (без чтения входов)")
    common(p)
    p.add_argument("--npm-cache")
    p.add_argument("--out")
    p = sub.add_parser("plan", help="что подготовить ДО отключения интернета (ничего не выполняет)")
    common(p)
    p.add_argument("--kit")
    p.add_argument("--npm-cache")
    p = sub.add_parser("build", help="проверить и скопировать готовые локальные артефакты в новый каталог")
    common(p)
    p.add_argument("--out", required=True)
    p.add_argument("--models-dir")
    p.add_argument("--audio-models-dir")
    p.add_argument("--wheels-dir")
    p.add_argument("--electron-zip")
    p.add_argument("--electron-cache")
    p.add_argument("--npm-cache")
    p.add_argument("--accept-distribution", action="append", metavar="MODEL_ID",
                   help="включить модель с нерешённым вопросом распространения (решение команды записывается в manifest)")
    p.add_argument("--per-pc", action="append", metavar="MODULE",
                   help="не класть модуль в комплект: каждый ПК готовит его сам до отключения (записывается в manifest)")
    p.add_argument("--allow-incomplete", action="store_true",
                   help="собрать частичный комплект для диагностики (manifest: technical_ready=false)")
    p = sub.add_parser("check", help="проверить готовый комплект (только чтение, без сети)")
    common(p, scenario_default=None)
    p.add_argument("--kit", required=True)
    p.add_argument("--json-out")
    p = sub.add_parser("check-install", help="проверить установленное состояние этого ПК (только чтение, без сети)")
    common(p)
    p.add_argument("--models-dir")
    p.add_argument("--audio-models-dir")
    p.add_argument("--deep", action="store_true", help="сверить файлы установленных пакетов с RECORD (медленнее)")
    p.add_argument("--json-out")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).resolve()
        if not (root / "pyproject.toml").is_file():
            raise UsageError(f"--root {root} не похож на каталог proctoring/ (нет pyproject.toml)")
        spec = load_spec(Path(args.spec))
        handler = {"inventory": cmd_inventory, "plan": cmd_plan, "build": cmd_build, "check": cmd_check,
                   "check-install": cmd_check_install}[args.command]
        return handler(args, root, spec)
    except UsageError as exc:
        print(f"ОШИБКА ПАРАМЕТРОВ: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
