#!/usr/bin/env python3
"""Adal offline kit: inventory, bundle and verify artifacts prepared BEFORE the internet is switched off.

Target: Windows x64 (CPython 3.12 x64 wheels, Electron win32-x64). Standard library only.
This tool never downloads anything, never starts pip/npm/uv/node/electron, never imports the application,
never opens a camera or microphone and never writes into its inputs (checkout, models, wheelhouse, kit).
Pins are read from the checkout: backend/proctor/*/models.manifest.json, backend/proctor/audio/assets.py,
requirements/*.txt, pyproject.toml, desktop/package-lock.json and acceptance/offline/kit_pins.json.

Commands:  plan | inventory | build | verify | check-pc        (python adal_offline_kit.py <command> -h)
Exit codes: 0 ready; 1 required artifact missing/corrupt/incompatible; 2 blocked (unconfirmed pin, source
or licence); 64 usage error. Optional artifacts never turn a failure into PASS and never fail the run.
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import email.parser
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tomllib
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

SCHEMA = "adal.offline-kit/1"
TOOL_VERSION = 1
MANIFEST_NAME = "adal-offline-kit.json"
SUMS_NAME = "SHA256SUMS.txt"
TOOL_PATH = "proctoring/acceptance/offline/adal_offline_kit.py"
DEFAULT_REPO = Path(__file__).resolve().parents[2]  # .../proctoring
SCENARIO = "classroom-cv"
ROLES = ("teacher", "student")

# Windows x64 target: values used to evaluate PEP 508 markers and wheel tags.
TARGET = {"name": "windows-x64", "os": "windows", "arch": "x64", "python": "3.12", "python_tag": "cp312",
          "wheel_platform": "win_amd64", "electron_platform": "win32-x64"}
MARKER_ENV = {"sys_platform": "win32", "platform_machine": "AMD64", "platform_system": "Windows", "os_name": "nt",
              "implementation_name": "cpython", "platform_python_implementation": "CPython",
              "python_version": "3.12", "python_full_version": "3.12.0"}

PASS, MISSING, CORRUPT, WRONG_PLATFORM, BLOCKED, WARN = "PASS", "MISSING", "CORRUPT", "WRONG_PLATFORM", "BLOCKED", "WARN"
FAILING = (MISSING, CORRUPT, WRONG_PLATFORM)
EXIT_READY, EXIT_NOT_READY, EXIT_BLOCKED, EXIT_USAGE = 0, 1, 2, 64

# Which model module matters for the classroom CV demo (phone in frame / looking away on a student PC).
# Evidence for each mark is in proctoring/handoffs/ADAL-OFFLINE-KIT/MODELS.md.
MODULE_POLICY = {
    "phone": ("required", "student", "обнаружение телефона (A03, YOLO11n)"),
    "attention": ("required", "student", "лицо и направление взгляда (A04, MediaPipe FaceLandmarker)"),
    "identity": ("optional", "student", "сверка личности (A13, YuNet + SFace)"),
    "audio": ("optional", "student", "детектор речи (A14, Silero VAD)"),
}
ONLINE = "до отключения интернета"
PREPARE_HINT = {
    "phone": ".\\.venv\\Scripts\\python.exe -m proctor.phone.prepare --download --models-dir <models>",
    "attention": ".\\.venv\\Scripts\\python.exe -m proctor.attention.model_tool fetch --models-dir <models>",
    "identity": ".\\.venv\\Scripts\\python.exe -m proctor.identity.prepare --download --models-dir <models>",
    "audio": ".\\.venv\\Scripts\\python.exe -m proctor.audio.prepare --download "
             "(runtime читает только %LOCALAPPDATA%\\QorgauExam\\models\\audio)",
}
NETWORK_TOKENS = re.compile(
    r"\b(npm(?:\.cmd)?\s+(?:ci|install|i|update)\b|npx\b|uv\s+(?:run|sync|pip|add|tool)\b|pip(?:3)?(?:\.exe)?\s+install\b|"
    r"-m\s+pip\s+install\b|install\.js\b|electron\.cmd\b|Invoke-WebRequest\b|Invoke-RestMethod\b|"
    r"Start-BitsTransfer\b|curl(?:\.exe)?\s|wget\s|git\s+(?:clone|pull|fetch)\b|--fetch|--download\b|FetchModels)",
    re.IGNORECASE)
MESSAGE_START = re.compile(r"\b(?:throw|Write-(?:Host|Warning|Error|Output|Verbose))\b", re.IGNORECASE)


class UsageError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- utilities

def sha256_file(path: Path) -> tuple[int, str]:
    with open(path, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return path.stat().st_size, digest


def human_size(size: int | None) -> str:
    if size is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return str(size)


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def resolved(path: Path) -> Path:
    return Path(os.path.abspath(os.path.realpath(path)))


def is_within(child: Path, parent: Path) -> bool:
    child, parent = resolved(child), resolved(parent)
    return child == parent or parent in child.parents


def safe_relative(rel: str) -> PurePosixPath:
    """A manifest path: relative, POSIX separators, no '.', '..' or drive."""
    if not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel:
        raise ValueError(f"unsafe relative path: {rel!r}")
    path = PurePosixPath(rel)
    if path.is_absolute() or any(part in ("", ".", "..") for part in rel.split("/")):
        raise ValueError(f"unsafe relative path: {rel!r}")
    return path


def binary_kind(head: bytes) -> str:
    """Executable format and machine from the first bytes of a file (PE/ELF/Mach-O)."""
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return "macho"
    if head[:2] != b"MZ" or len(head) < 0x40:
        return "unknown"
    offset = struct.unpack_from("<I", head, 0x3C)[0]
    if offset + 6 > len(head) or head[offset:offset + 4] != b"PE\0\0":
        return "pe-unknown"
    machine = struct.unpack_from("<H", head, offset + 4)[0]
    return {0x8664: "pe-x64", 0x14C: "pe-x86", 0xAA64: "pe-arm64"}.get(machine, f"pe-0x{machine:04x}")


def file_binary_kind(path: Path) -> str:
    with open(path, "rb") as stream:
        return binary_kind(stream.read(4096))


def git_head(repo: Path) -> str | None:
    """Commit of the checkout, read from .git without running git (worktrees supported)."""
    try:
        for candidate in (repo, *repo.parents):
            dotgit = candidate / ".git"
            if dotgit.exists():
                break
        else:
            return None
        gitdir = dotgit
        if dotgit.is_file():
            gitdir = Path(dotgit.read_text(encoding="utf-8").split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (dotgit.parent / gitdir).resolve()
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head if re.fullmatch(r"[0-9a-f]{40}", head) else None
        ref = head[4:].strip()
        common = gitdir
        if (gitdir / "commondir").is_file():
            common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
        for base in (gitdir, common):
            if (base / ref).is_file():
                return (base / ref).read_text(encoding="utf-8").strip()
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
    except (OSError, IndexError, ValueError):
        return None
    return None


# ---------------------------------------------------------------------------------------------- report items

@dataclass
class Item:
    id: str
    kind: str                      # model | model-aux | wheel | electron | python | npm | build | config | launcher
    requirement: str               # required | optional | info
    roles: list[str]
    feature: str
    status: str = PASS
    reason: str = ""
    detail: str = ""
    remedy: str = ""
    bundle_path: str | None = None
    install_to: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    expected_size: int | None = None
    expected_sha256: str | None = None
    pin_source: str | None = None
    source_url: str | None = None
    license: str | None = None
    license_evidence: str | None = None
    distribution: str | None = None
    compatibility: str | None = None
    included: bool | None = None
    src: Path | None = field(default=None, repr=False)

    def fail(self, status: str, reason: str, detail: str = "", remedy: str = "") -> "Item":
        self.status, self.reason = status, reason
        if detail:
            self.detail = detail
        if remedy:
            self.remedy = remedy
        return self

    def public(self) -> dict:
        data = asdict(self)
        data.pop("src", None)
        return {k: v for k, v in data.items() if v not in (None, "")}


def overall(items: list[Item]) -> tuple[str, int]:
    required = [i for i in items if i.requirement == "required"]
    if any(i.status in FAILING for i in required):
        return "NOT_READY", EXIT_NOT_READY
    if any(i.status == BLOCKED for i in required):
        return "BLOCKED", EXIT_BLOCKED
    return "READY", EXIT_READY


# ---------------------------------------------------------------------------------------------- pins: models

@dataclass
class ModelPin:
    module: str
    rel: str                     # path below the models directory, e.g. phone/yolo11n.onnx
    sha256: str | None
    size: int | None
    pin_source: str
    source_url: str | None
    license: str | None
    license_evidence: str | None
    version: str | None = None
    notes: str = ""
    problems: list[str] = field(default_factory=list)
    aux: bool = False


_SHA = re.compile(r"[0-9a-f]{64}")


def _pin_problems(pin: ModelPin) -> list[str]:
    problems = list(pin.problems)
    try:
        safe_relative(pin.rel)
        if not pin.rel.startswith(pin.module + "/"):
            problems.append(f"file {pin.rel!r} is outside the module folder {pin.module}/")
    except ValueError as exc:
        problems.append(str(exc))
    if not (pin.sha256 and _SHA.fullmatch(pin.sha256)):
        problems.append("SHA-256 is missing or malformed in the pin")
    if not pin.aux and not (pin.source_url and pin.source_url.startswith("https://")):
        problems.append("source URL is not recorded (https)")
    if not pin.license or pin.license.strip().upper() in ("", "UNKNOWN", "EXAMPLE", "NONE"):
        problems.append("licence is not recorded")
    return problems


def _literal_assignments(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            try:
                values[name] = ast.literal_eval(node.value)
            except ValueError:
                if isinstance(node.value, ast.JoinedStr):  # f"...{REVISION}..." with known names only
                    parts = []
                    for piece in node.value.values:
                        if isinstance(piece, ast.Constant):
                            parts.append(str(piece.value))
                        elif isinstance(piece, ast.FormattedValue) and isinstance(piece.value, ast.Name) \
                                and isinstance(values.get(piece.value.id), str):
                            parts.append(values[piece.value.id])
                        else:
                            break
                    else:
                        values[name] = "".join(parts)
    return values


def load_kit_pins(repo: Path) -> dict:
    path = repo / "acceptance" / "offline" / "kit_pins.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != "adal.offline-kit.pins/1":
            raise ValueError("unexpected schema")
        return data
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc


def load_model_pins(repo: Path, kit_pins: dict) -> list[ModelPin]:
    pins: list[ModelPin] = []
    backend = repo / "backend" / "proctor"
    for manifest in sorted(backend.glob("*/models.manifest.json")):
        module = manifest.parent.name
        rel_manifest = manifest.relative_to(repo).as_posix()
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            entries = data["models"] if isinstance(data.get("models"), list) else [data]
        except (OSError, ValueError, KeyError) as exc:
            pins.append(ModelPin(module, f"{module}/?", None, None, rel_manifest, None, None, None,
                                 problems=[f"manifest unreadable: {type(exc).__name__}: {exc}"]))
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                pins.append(ModelPin(module, f"{module}/?", None, None, rel_manifest, None, None, None,
                                     problems=["manifest entry is not an object"]))
                continue
            size = entry.get("size_bytes")
            pins.append(ModelPin(
                module=module, rel=str(entry.get("file", "")), sha256=entry.get("sha256"),
                size=size if isinstance(size, int) and size > 0 else None, pin_source=rel_manifest,
                source_url=entry.get("source_url"), license=entry.get("license"),
                license_evidence=f"{rel_manifest} (license{', ' + entry['license_url'] if entry.get('license_url') else ''})",
                version=entry.get("version"), notes=str(entry.get("notes", ""))[:400],
                problems=[] if isinstance(size, int) and size > 0 else ["size_bytes is missing in the pin"]))
    assets = backend / "audio" / "assets.py"
    if assets.is_file():
        values = _literal_assignments(assets)
        prepare = backend / "audio" / "prepare.py"
        licence = None
        if prepare.is_file():
            match = re.search(r"license=\"([A-Za-z0-9.\-]+)\"", prepare.read_text(encoding="utf-8"))
            licence = match.group(1) if match else None
        name = values.get("MODEL_NAME")
        audio_pins = kit_pins.get("audio", {})
        observed = audio_pins.get(str(name), {}).get("size_bytes_observed")
        rel_assets = assets.relative_to(repo).as_posix()
        pins.append(ModelPin(
            module="audio", rel=f"audio/{name}", sha256=values.get("MODEL_SHA256"), size=None,
            pin_source=f"{rel_assets} (MODEL_SHA256, REVISION {values.get('REVISION')})",
            source_url=values.get("MODEL_URL") if isinstance(values.get("MODEL_URL"), str) else None,
            license=licence, license_evidence="proctoring/backend/proctor/audio/prepare.py (license field) + "
                                              "LICENSE.silero.txt from the pinned revision",
            version=str(values.get("REVISION")),
            notes=f"size not pinned by the repo; observed {observed} B" if observed else "size not pinned by the repo"))
        lic = audio_pins.get("LICENSE.silero.txt", {})
        pins.append(ModelPin(
            module="audio", rel="audio/LICENSE.silero.txt", sha256=lic.get("sha256"), size=lic.get("size_bytes"),
            pin_source="proctoring/acceptance/offline/kit_pins.json (audio.LICENSE.silero.txt)",
            source_url=values.get("LICENSE_URL") if isinstance(values.get("LICENSE_URL"), str) else None,
            license=licence, license_evidence=lic.get("pin_evidence"), aux=True))
    for pin in pins:
        pin.problems = _pin_problems(pin)
    return pins


# ---------------------------------------------------------------------------------------------- pins: wheels

@dataclass
class Requirement:
    name: str
    version: str
    marker: str
    hashes: set[str]
    parents: list[str] = field(default_factory=list)
    applies: bool = True
    group: str = "runtime"         # runtime | cv | dev


_MARKER_TOKEN = re.compile(r"\s*(?:(?P<open>\()|(?P<close>\))|(?P<and>and)\b|(?P<or>or)\b|"
                           r"(?P<var>[a-z_]+)\s*(?P<op>==|!=)\s*(?P<q>['\"])(?P<val>[^'\"]*)(?P=q))")


def evaluate_marker(marker: str, env: dict[str, str] = MARKER_ENV) -> bool:
    """Small PEP 508 subset: == / != on known variables, and/or, parentheses. Anything else is an error."""
    tokens, pos, text = [], 0, marker.strip()
    while pos < len(text):
        match = _MARKER_TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise ValueError(f"unsupported marker syntax near {text[pos:pos + 30]!r}")
        pos = match.end()
        kind = match.lastgroup if match.lastgroup not in ("q", "val", "op") else "cmp"
        if match.group("var"):
            if match.group("var") not in env:
                raise ValueError(f"unknown marker variable {match.group('var')!r}")
            value = env[match.group("var")] == match.group("val")
            tokens.append(("bool", value if match.group("op") == "==" else not value))
        else:
            tokens.append((kind, None))
    position = 0

    def parse_or() -> bool:
        nonlocal position
        value = parse_and()
        while position < len(tokens) and tokens[position][0] == "or":
            position += 1
            right = parse_and()
            value = value or right
        return value

    def parse_and() -> bool:
        nonlocal position
        value = parse_atom()
        while position < len(tokens) and tokens[position][0] == "and":
            position += 1
            right = parse_atom()
            value = value and right
        return value

    def parse_atom() -> bool:
        nonlocal position
        if position >= len(tokens):
            raise ValueError("incomplete marker")
        kind, value = tokens[position]
        position += 1
        if kind == "bool":
            return value
        if kind == "open":
            inner = parse_or()
            if position >= len(tokens) or tokens[position][0] != "close":
                raise ValueError("unbalanced parentheses in marker")
            position += 1
            return inner
        raise ValueError(f"unexpected {kind!r} in marker")

    result = parse_or()
    if position != len(tokens):
        raise ValueError("trailing tokens in marker")
    return result


def parse_requirements(path: Path) -> list[Requirement]:
    reqs: list[Requirement] = []
    logical: list[str] = []
    via_open = False
    lines = path.read_text(encoding="utf-8").splitlines()

    def flush():
        if not logical:
            return
        line = " ".join(part.rstrip("\\").strip() for part in logical)
        logical.clear()
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", line))
        line = re.sub(r"--hash=\S+", "", line).strip()
        spec, _, marker = line.partition(";")
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!_-]+)", spec.strip())
        if not match:
            raise ValueError(f"{path.name}: only pinned 'name==version' lines are supported: {spec.strip()!r}")
        reqs.append(Requirement(match.group(1), match.group(2), marker.strip(), hashes))

    for raw in lines:
        stripped = raw.strip()
        if logical and logical[-1].rstrip().endswith("\\") and not stripped.startswith("#"):
            logical.append(stripped)
            continue
        flush()
        if not stripped:
            via_open = False
            continue
        if stripped.startswith("#"):
            comment = stripped.lstrip("#").strip()
            if comment.startswith("via"):
                rest = comment[3:].strip()
                via_open = not rest
                if rest and reqs:
                    reqs[-1].parents.append(norm_name(rest))
            elif via_open and reqs and comment:
                reqs[-1].parents.append(norm_name(comment))
            continue
        via_open = False
        logical.append(stripped)
    flush()
    return reqs


def classify_requirements(repo: Path, reqs: list[Requirement]) -> None:
    """Mark runtime / cv / dev using pyproject groups and the '# via' graph; evaluate target markers."""
    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    groups = {"runtime": project.get("dependencies", []),
              **{k: v for k, v in project.get("optional-dependencies", {}).items()}}
    direct = {g: {norm_name(re.split(r"[=<>!~;\[ ]", d, maxsplit=1)[0]) for d in deps} for g, deps in groups.items()}
    by_name = {norm_name(r.name): r for r in reqs}
    for req in reqs:
        req.applies = evaluate_marker(req.marker) if req.marker else True

    def closure(roots: set[str]) -> set[str]:
        found = set(roots)
        changed = True
        while changed:
            changed = False
            for name, req in by_name.items():
                if name not in found and any(p in found for p in req.parents):
                    found.add(name)
                    changed = True
        return found

    runtime = closure(direct.get("runtime", set()))
    cv = closure(direct.get("runtime", set()) | direct.get("cv", set()))
    for name, req in by_name.items():
        req.group = "runtime" if name in runtime else "cv" if name in cv else "dev"


def parse_wheel_name(filename: str) -> dict | None:
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) not in (5, 6):
        return None
    name, version = parts[0], parts[1]
    py, abi, plat = parts[-3], parts[-2], parts[-1]
    return {"name": norm_name(name), "version": version, "py": py.split("."), "abi": abi.split("."),
            "plat": plat.split("."), "tags": f"{py}-{abi}-{plat}"}


def wheel_compatible(info: dict) -> bool:
    def py_ok(py: str, abi: str) -> bool:
        if abi == "cp312":
            return py == "cp312"
        if abi == "abi3":
            return bool(re.fullmatch(r"cp3(\d+)", py)) and 2 <= int(py[3:]) <= 12
        if abi == "none":
            return py in ("py3", "cp312") or bool(re.fullmatch(r"py3(\d+)", py)) and int(py[3:]) <= 12
        return False
    return any(p in ("win_amd64", "any") for p in info["plat"]) and \
        any(py_ok(py, abi) for py in info["py"] for abi in info["abi"])


def wheel_specificity(info: dict) -> tuple[int, int]:
    return (1 if "win_amd64" in info["plat"] else 0, 2 if "cp312" in info["abi"] else 1 if "abi3" in info["abi"] else 0)


def wheel_license(path: Path) -> tuple[str | None, str | None]:
    """Licence from the wheel's own METADATA (zip read only, nothing executed)."""
    with zipfile.ZipFile(path) as archive:
        meta = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA") and n.count("/") == 1]
        if len(meta) != 1:
            return None, None
        message = email.parser.Parser().parsestr(archive.read(meta[0]).decode("utf-8", "replace"), headersonly=True)
    expression = message.get("License-Expression")
    if expression and expression.strip():
        return expression.strip(), "wheel METADATA License-Expression"
    text = (message.get("License") or "").strip()
    if text and "\n" not in text and len(text) <= 60 and not text.lower().startswith(("copyright", "=")):
        return text, "wheel METADATA License"
    classifiers = [c.split("::")[-1].strip() for c in message.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return " / ".join(classifiers), "wheel METADATA License classifier"
    return None, None


# ---------------------------------------------------------------------------------------------- pins: electron

@dataclass
class ElectronPin:
    version: str | None
    artifact: str | None
    sha256: str | None
    source_url: str | None
    pin_source: str | None
    license: str | None
    license_evidence: str | None
    distribution: str | None
    problems: list[str]


def load_electron_pin(repo: Path, kit_pins: dict) -> ElectronPin:
    problems: list[str] = []
    lock_path = repo / "desktop" / "package-lock.json"
    version = license_name = None
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        entry = lock["packages"]["node_modules/electron"]
        version, license_name = entry["version"], entry.get("license")
    except (OSError, ValueError, KeyError) as exc:
        problems.append(f"electron version is not readable from desktop/package-lock.json: {exc}")
    artifact = f"electron-v{version}-{TARGET['electron_platform']}.zip" if version else None
    pinned = kit_pins.get("electron", {}).get(version or "", {})
    sha = pinned.get("sha256") if pinned.get("artifact") == artifact else None
    source = "proctoring/acceptance/offline/kit_pins.json" if sha else None
    installed = repo / "desktop" / "node_modules" / "electron"
    try:
        package = json.loads((installed / "package.json").read_text(encoding="utf-8"))
        checksums = json.loads((installed / "checksums.json").read_text(encoding="utf-8"))
        if package.get("version") == version and artifact in checksums:
            if sha and checksums[artifact] != sha:
                problems.append("kit_pins.json and the installed electron checksums.json disagree")
            sha, source = checksums[artifact], "desktop/node_modules/electron/checksums.json (npm package pinned by package-lock)"
    except (OSError, ValueError):
        pass
    if version and not sha:
        problems.append(f"no SHA-256 pin for {artifact}: install electron@{version} with npm ci (online) or add a "
                        "verified entry to acceptance/offline/kit_pins.json")
    return ElectronPin(version, artifact, sha, pinned.get("source_url"), source,
                       license_name or pinned.get("license"),
                       "desktop/package-lock.json (license)" if license_name else pinned.get("license_evidence"),
                       pinned.get("distribution"), problems)


# ---------------------------------------------------------------------------------------------- inventory of inputs

@dataclass
class Inputs:
    repo: Path
    roles: tuple[str, ...]
    models_dir: Path | None
    audio_dir: Path | None
    wheelhouse: Path | None
    electron_zip: Path | None
    requirements: Path


def _model_remedy(pin: ModelPin, models_hint: str) -> str:
    command = PREPARE_HINT.get(pin.module, "")
    if pin.aux and pin.module == "audio":
        command = PREPARE_HINT["audio"]
    return (f"Подготовить {ONLINE}: {command.replace('<models>', models_hint)} "
            f"(или скопировать заранее проверенный файл {pin.rel}). Источник: {pin.source_url or 'не записан'}.")


def model_items(inputs: Inputs, pins: list[ModelPin]) -> list[Item]:
    items = []
    for pin in pins:
        policy = MODULE_POLICY.get(pin.module, ("optional", "student", f"модуль {pin.module} (не классифицирован)"))
        requirement, role, feature = policy
        if role not in inputs.roles:
            continue
        item = Item(id=f"model:{pin.rel}", kind="model-aux" if pin.aux else "model", requirement=requirement,
                    roles=[role], feature=feature, bundle_path=f"models/{pin.rel}",
                    install_to=f"%QORGAU_MODELS_DIR%\\{pin.rel.replace('/', chr(92))}" if pin.module != "audio"
                    else f"<audio dir>\\{pin.rel.split('/', 1)[1]}",
                    expected_sha256=pin.sha256, expected_size=pin.size, pin_source=pin.pin_source,
                    source_url=pin.source_url, license=pin.license, license_evidence=pin.license_evidence,
                    compatibility="platform-independent model file; runtime pinned by requirements/full.txt",
                    distribution="see pin notes" if pin.notes else None, detail=pin.notes[:300] if pin.notes else "")
        if pin.module == "phone" and pin.license and "AGPL" in pin.license.upper():
            item.distribution = ("AGPL-3.0: передача третьим лицам требует соблюдения AGPL (текст лицензии и источник); "
                                 "manifest модуля требует ревизии перед любым распространением")
        base = inputs.audio_dir if pin.module == "audio" else inputs.models_dir
        rel_in_base = pin.rel.split("/", 1)[1] if pin.module == "audio" else pin.rel
        hint = "<models>" if base is None else str(base if pin.module != "audio" else base.parent)
        if pin.problems:
            items.append(item.fail(BLOCKED, "pin_unconfirmed", "; ".join(pin.problems),
                                   "Источник/лицензия/хэш не подтверждены в репозитории: не включать, пока владелец "
                                   "модуля не запишет их в manifest."))
            continue
        if base is None:
            items.append(item.fail(MISSING, "input_not_given", "каталог не указан",
                                   f"Укажите {'--audio-dir' if pin.module == 'audio' else '--models-dir'}. "
                                   + _model_remedy(pin, hint)))
            continue
        path = base / Path(*PurePosixPath(rel_in_base).parts)
        item.src = path
        if not path.is_file():
            items.append(item.fail(MISSING, "file_missing", f"нет файла: {path}", _model_remedy(pin, hint)))
            continue
        try:
            size, digest = sha256_file(path)
        except OSError as exc:
            items.append(item.fail(CORRUPT, "unreadable", f"{path}: {type(exc).__name__}", _model_remedy(pin, hint)))
            continue
        item.size_bytes, item.sha256 = size, digest
        if pin.size is not None and size != pin.size:
            item.fail(CORRUPT, "size_mismatch", f"{path}: {size} B, ожидалось {pin.size} B (неполная/другая загрузка)",
                      _model_remedy(pin, hint))
        elif digest != pin.sha256:
            item.fail(CORRUPT, "hash_mismatch", f"{path}: sha256 {digest[:16]}…, ожидалось {pin.sha256[:16]}…",
                      _model_remedy(pin, hint))
        items.append(item)
    if "student" in inputs.roles:
        items.extend(audio_manifest_items(inputs, pins))
    return items


def audio_manifest_items(inputs: Inputs, pins: list[ModelPin]) -> list[Item]:
    """audio/manifest.json is written by proctor.audio.prepare; proctor.audio.assets.check requires it."""
    model = next((p for p in pins if p.module == "audio" and not p.aux), None)
    if model is None:
        return []
    requirement, role, feature = MODULE_POLICY["audio"]
    item = Item(id="model:audio/manifest.json", kind="model-aux", requirement=requirement, roles=[role], feature=feature,
                bundle_path="models/audio/manifest.json", install_to="<audio dir>\\manifest.json",
                pin_source=model.pin_source, compatibility="JSON written by proctor.audio.prepare")
    remedy = _model_remedy(model, "<models>")
    if model.problems:
        return [item.fail(BLOCKED, "pin_unconfirmed", "; ".join(model.problems))]
    if inputs.audio_dir is None:
        return [item.fail(MISSING, "input_not_given", "каталог не указан", "Укажите --audio-dir. " + remedy)]
    path = inputs.audio_dir / "manifest.json"
    item.src = path
    if not path.is_file():
        return [item.fail(MISSING, "file_missing", f"нет файла: {path}", remedy)]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        item.size_bytes, item.sha256 = sha256_file(path)
    except (OSError, ValueError) as exc:
        return [item.fail(CORRUPT, "invalid_json", f"{path}: {type(exc).__name__}", remedy)]
    revision = model.version
    if data.get("sha256") != model.sha256 or str(data.get("revision")) != revision:
        item.fail(CORRUPT, "manifest_mismatch", f"{path}: sha256/revision не совпадают с proctor/audio/assets.py", remedy)
    return [item]


def wheel_items(inputs: Inputs) -> list[Item]:
    reqs = parse_requirements(inputs.requirements)
    classify_requirements(inputs.repo, reqs)
    rel_req = inputs.requirements.relative_to(inputs.repo).as_posix() if is_within(inputs.requirements, inputs.repo) \
        else inputs.requirements.name
    found: dict[str, list[tuple[Path, dict]]] = {}
    ignored = 0
    if inputs.wheelhouse is not None and inputs.wheelhouse.is_dir():
        for path in sorted(inputs.wheelhouse.iterdir()):
            info = parse_wheel_name(path.name) if path.is_file() else None
            if info is None:
                ignored += path.is_file()
                continue
            found.setdefault(info["name"], []).append((path, info))
    remedy_all = (f"Подготовить {ONLINE} на Windows x64 (маркеры вычисляются для целевой ОС): py -3.12 -m pip download "
                  f"--only-binary=:all: --require-hashes --no-deps -r requirements\\{inputs.requirements.name} -d <wheelhouse>")
    items: list[Item] = []
    for req in reqs:
        if not req.applies:
            continue
        key = norm_name(req.name)
        item = Item(id=f"wheel:{key}=={req.version}", kind="wheel", requirement="required",
                    roles=list(inputs.roles),
                    feature=f"Python {req.group} (pip install --no-index -r {rel_req})",
                    pin_source=f"{rel_req} (--hash)", source_url="https://pypi.org/project/" + key + "/" + req.version + "/",
                    expected_sha256=None, remedy=remedy_all)
        if not req.hashes:
            items.append(item.fail(BLOCKED, "pin_unconfirmed", "в файле требований нет --hash для этого пакета"))
            continue
        if inputs.wheelhouse is None:
            items.append(item.fail(MISSING, "input_not_given", "--wheelhouse не указан"))
            continue
        candidates = [(p, i) for p, i in found.get(key, []) if i["version"] == req.version]
        if not candidates:
            items.append(item.fail(MISSING, "file_missing", f"нет колеса {req.name}=={req.version} в {inputs.wheelhouse}"))
            continue
        compatible = [(p, i) for p, i in candidates if wheel_compatible(i)]
        if not compatible:
            tags = ", ".join(i["tags"] for _, i in candidates)
            items.append(item.fail(WRONG_PLATFORM, "wrong_platform",
                                   f"{req.name}=={req.version}: есть только {tags}; нужен cp312/abi3/none и win_amd64/any"))
            continue
        compatible.sort(key=lambda c: wheel_specificity(c[1]), reverse=True)
        verdict = None
        for path, info in compatible:
            try:
                size, digest = sha256_file(path)
            except OSError as exc:
                verdict = (CORRUPT, "unreadable", f"{path.name}: {type(exc).__name__}")
                continue
            if digest in req.hashes:
                item.src, item.size_bytes, item.sha256 = path, size, digest
                item.expected_sha256 = digest
                item.bundle_path = f"wheels/{path.name}"
                item.compatibility = f"{info['tags']} (CPython 3.12 Windows x64)"
                item.install_to = "<venv> через pip --no-index --require-hashes"
                verdict = None
                break
            verdict = (CORRUPT, "hash_mismatch", f"{path.name}: sha256 {digest[:16]}… нет среди --hash в {rel_req}")
        if verdict:
            items.append(item.fail(*verdict))
            continue
        try:
            item.license, item.license_evidence = wheel_license(item.src)
        except (OSError, zipfile.BadZipFile) as exc:
            items.append(item.fail(CORRUPT, "bad_zip", f"{item.src.name}: {type(exc).__name__}"))
            continue
        if not item.license:
            item.fail(BLOCKED, "license_unconfirmed", f"{item.src.name}: в METADATA нет лицензии",
                      "Проверить лицензию пакета вручную и записать решение в handoff; до этого не распространять.")
        items.append(item)
    if ignored:
        items.append(Item(id="wheelhouse:other-files", kind="wheel", requirement="info", roles=list(inputs.roles),
                          feature="прочие файлы колёс", status=WARN, reason="ignored",
                          detail=f"{ignored} файл(ов) не являются колёсами и не копируются"))
    return items


def electron_items(inputs: Inputs, pin: ElectronPin) -> list[Item]:
    if "student" not in inputs.roles:
        return []
    item = Item(id=f"electron:{pin.artifact or 'unknown'}", kind="electron", requirement="optional", roles=["student"],
                feature="резервная копия Electron для восстановления без интернета", source_url=pin.source_url,
                bundle_path=f"electron/{pin.artifact}" if pin.artifact else None, expected_sha256=pin.sha256,
                pin_source=pin.pin_source, license=pin.license, license_evidence=pin.license_evidence,
                distribution=pin.distribution, compatibility=TARGET["electron_platform"],
                install_to="desktop\\node_modules\\electron\\dist (+ path.txt = electron.exe)")
    remedy = (f"Подготовить {ONLINE}: в proctoring\\desktop выполнить npm ci и node node_modules/electron/install.js; "
              f"файл {pin.artifact} остаётся в кэше %LOCALAPPDATA%\\electron\\Cache. Укажите его через --electron-zip.")
    if pin.problems:
        return [item.fail(BLOCKED, "pin_unconfirmed", "; ".join(pin.problems), remedy)]
    if inputs.electron_zip is None:
        return [item.fail(MISSING, "input_not_given", "--electron-zip не указан", remedy)]
    candidates = [inputs.electron_zip] if inputs.electron_zip.is_file() else \
        sorted(p for p in inputs.electron_zip.rglob(pin.artifact) if p.is_file()) if inputs.electron_zip.is_dir() else []
    if not candidates:
        return [item.fail(MISSING, "file_missing", f"нет {pin.artifact} в {inputs.electron_zip}", remedy)]
    for path in candidates:
        try:
            size, digest = sha256_file(path)
        except OSError as exc:
            item.fail(CORRUPT, "unreadable", f"{path}: {type(exc).__name__}", remedy)
            continue
        item.size_bytes, item.sha256, item.src = size, digest, path
        if digest != pin.sha256:
            item.fail(CORRUPT, "hash_mismatch", f"{path.name}: sha256 {digest[:16]}…, ожидалось {pin.sha256[:16]}…", remedy)
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                kind = binary_kind(archive.open("electron.exe").read(4096))
                version = archive.read("version").decode("ascii", "replace").strip()
        except (KeyError, zipfile.BadZipFile, OSError) as exc:
            item.fail(CORRUPT, "bad_zip", f"{path.name}: {type(exc).__name__}", remedy)
            continue
        if kind != "pe-x64" or version.lstrip("v") != pin.version:
            item.fail(WRONG_PLATFORM, "wrong_platform", f"electron.exe={kind}, version={version}", remedy)
            continue
        item.status, item.reason, item.detail = PASS, "", f"electron.exe PE x64, version {version}"
        return [item]
    return [item]


def inventory(inputs: Inputs) -> list[Item]:
    kit_pins = load_kit_pins(inputs.repo)
    items = model_items(inputs, load_model_pins(inputs.repo, kit_pins))
    items += wheel_items(inputs)
    items += electron_items(inputs, load_electron_pin(inputs.repo, kit_pins))
    return items


# ---------------------------------------------------------------------------------------------- build / verify

def not_in_kit() -> list[dict]:
    return [
        {"what": "исходники проекта (git checkout той же фиксации)", "why": "переносятся git, а не комплектом"},
        {"what": "Python 3.12 x64 (python.org installer)", "why": "в репозитории нет пина установщика; ставится до отключения"},
        {"what": "Node.js >=22.12 и npm", "why": "нужны только для npm ci/npm run build до отключения; Start-Student.ps1 запускает electron.exe напрямую"},
        {"what": "proctoring/.venv, desktop/node_modules, desktop/dist", "why": "создаются на каждом ПК; копирование venv/dist между ПК не поддерживается"},
        {"what": "данные %LOCALAPPDATA%\\QorgauExam, QorgauClassroom, PIN, токены, записи, лица/голоса", "why": "персональные данные; никогда не входят в комплект"},
    ]


def _copy_verified(src: Path, dst: Path, expected: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with open(src, "rb") as reader, open(dst, "xb") as writer:
        for chunk in iter(lambda: reader.read(1 << 20), b""):
            digest.update(chunk)
            writer.write(chunk)
    if digest.hexdigest() != expected:
        raise OSError(f"{src.name} changed while copying (sha256 {digest.hexdigest()[:16]}…)")
    shutil.copystat(src, dst)


def check_output_location(out: Path, inputs: Inputs) -> None:
    out = resolved(out)
    if is_within(out, inputs.repo) or is_within(inputs.repo, out):
        raise UsageError(f"--out {out} пересекается с исходниками {inputs.repo}: комплект с весами не должен попасть в git")
    for source in (inputs.models_dir, inputs.audio_dir, inputs.wheelhouse, inputs.electron_zip):
        if source is not None and (is_within(out, source) or is_within(source, out)):
            raise UsageError(f"--out {out} пересекается с входным каталогом {source}: запись в исходный комплект запрещена")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise UsageError(f"--out {out} уже существует и не пуст; выберите новый каталог")


def build(inputs: Inputs, out: Path, items: list[Item]) -> dict:
    check_output_location(out, inputs)
    status, code = overall(items)
    if code != EXIT_READY:
        raise RuntimeError(status)
    staging = out.with_name(out.name + ".incomplete")
    if staging.exists():
        raise UsageError(f"{staging} остался от прерванной сборки; удалите его вручную")
    staging.mkdir(parents=True)
    try:
        sums = []
        for item in items:
            item.included = item.status == PASS and item.src is not None and item.bundle_path is not None
            if not item.included:
                continue
            rel = safe_relative(item.bundle_path)
            _copy_verified(item.src, staging.joinpath(*rel.parts), item.sha256)
            sums.append(f"{item.sha256}  {rel.as_posix()}")
        manifest = manifest_document(inputs, items, status)
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / SUMS_NAME).write_text("\n".join(sorted(sums, key=lambda s: s[66:])) + "\n", encoding="utf-8")
        if out.exists():
            out.rmdir()
        os.replace(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def manifest_document(inputs: Inputs, items: list[Item], status: str) -> dict:
    included = [i for i in items if i.included]
    return {
        "schema": SCHEMA, "tool": TOOL_PATH, "tool_version": TOOL_VERSION, "created_utc": utc_now(),
        "repo_commit": git_head(inputs.repo), "target": TARGET, "scenario": SCENARIO, "roles": list(inputs.roles),
        "requirements": requirements_rel(inputs), "status": status,
        "summary": {"files": len(included), "bytes": sum(i.size_bytes or 0 for i in included),
                    "optional_not_included": sorted(i.id for i in items if i.requirement == "optional" and not i.included)},
        "items": [i.public() for i in items],
        "not_in_kit": not_in_kit(),
        "privacy": "Комплект содержит только перечисленные файлы. Нет исходников, venv, данных учеников, PIN, токенов, "
                   "записей, лиц и голосов. Абсолютные пути исходного ПК не записываются.",
        "claims": "Целостность и совместимость файлов. Не доказывает точность CV, работу камеры, LAN или физический пилот.",
    }


def requirements_rel(inputs: Inputs) -> str | None:
    if is_within(inputs.requirements, inputs.repo / "requirements"):
        return resolved(inputs.requirements).relative_to(resolved(inputs.repo)).as_posix()
    return None


def verify_kit(kit: Path, repo: Path, roles: tuple[str, ...]) -> tuple[list[Item], dict | None]:
    manifest_path = kit / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != SCHEMA:
            raise ValueError(f"schema {manifest.get('schema')!r}")
        if manifest.get("target", {}).get("name") != TARGET["name"]:
            raise ValueError(f"target {manifest.get('target')!r} != {TARGET['name']}")
        requirements = repo / Path(*safe_relative(manifest.get("requirements") or "requirements/full.txt").parts)
        if not is_within(requirements, repo / "requirements") or not requirements.is_file():
            raise ValueError(f"requirements {manifest.get('requirements')!r} is not a file of this checkout")
    except (OSError, ValueError, AttributeError) as exc:
        item = Item(id="kit:manifest", kind="config", requirement="required", roles=list(roles),
                    feature="манифест комплекта", remedy="Пересоберите комплект командой build.")
        return [item.fail(MISSING if isinstance(exc, FileNotFoundError) else CORRUPT, "manifest_unreadable",
                          f"{manifest_path}: {exc}")], None
    # Re-derive what this checkout expects: a kit built for another commit/pin must not pass.
    expected = {i.id: i for i in inventory(Inputs(repo, roles, None, None, None, None, requirements))}
    recorded = {entry.get("id"): entry for entry in manifest.get("items", []) if isinstance(entry, dict)}
    items: list[Item] = []
    listed: set[str] = {MANIFEST_NAME, SUMS_NAME}
    for item_id, template in expected.items():
        if template.requirement == "info":
            continue
        item = Item(id=item_id, kind=template.kind, requirement=template.requirement, roles=template.roles,
                    feature=template.feature, pin_source=template.pin_source, source_url=template.source_url,
                    license=template.license, license_evidence=template.license_evidence)
        entry = recorded.get(item_id)
        if template.status == BLOCKED:
            items.append(item.fail(BLOCKED, template.reason, template.detail, template.remedy))
            continue
        if not entry or not entry.get("included"):
            remedy = re.sub(r"^Укажите [^.]+\. ", "", template.remedy)
            items.append(item.fail(MISSING, "not_in_kit", "артефакт не включён в комплект",
                                   "Пересоберите комплект с этим артефактом. " + remedy))
            continue
        try:
            rel = safe_relative(entry.get("bundle_path", ""))
        except ValueError as exc:
            items.append(item.fail(CORRUPT, "unsafe_path", str(exc)))
            continue
        listed.add(rel.as_posix())
        pinned = template.expected_sha256 if template.kind != "wheel" else None
        recorded_sha = entry.get("sha256")
        if pinned and recorded_sha != pinned:
            items.append(item.fail(CORRUPT, "pin_changed", f"в комплекте sha256 {str(recorded_sha)[:16]}…, текущий пин "
                                   f"{pinned[:16]}… ({template.pin_source})", "Пересоберите комплект из этой фиксации."))
            continue
        if template.kind == "wheel" and not _wheel_pinned(requirements, entry):
            items.append(item.fail(CORRUPT, "pin_changed", f"{rel.name}: sha256 нет среди --hash текущего {requirements.name}",
                                   "Пересоберите комплект из этой фиксации."))
            continue
        path = kit.joinpath(*rel.parts)
        item.bundle_path, item.expected_sha256, item.expected_size = rel.as_posix(), recorded_sha, entry.get("size_bytes")
        if not path.is_file():
            items.append(item.fail(MISSING, "file_missing", f"нет файла в комплекте: {path}", "Пересоберите или "
                                   "скопируйте комплект заново с исходного носителя."))
            continue
        size, digest = sha256_file(path)
        item.size_bytes, item.sha256 = size, digest
        if size != entry.get("size_bytes") or digest != recorded_sha:
            item.fail(CORRUPT, "hash_mismatch" if size == entry.get("size_bytes") else "size_mismatch",
                      f"{path}: {size} B sha256 {digest[:16]}…; ожидалось {entry.get('size_bytes')} B {str(recorded_sha)[:16]}…",
                      "Файл повреждён при копировании: скопируйте комплект заново с исходного носителя.")
        items.append(item)
    for path in sorted(p for p in kit.rglob("*") if p.is_file()):
        rel = path.relative_to(kit).as_posix()
        if rel not in listed:
            items.append(Item(id=f"kit:extra:{rel}", kind="config", requirement="info", roles=list(roles),
                              feature="лишний файл", status=WARN, reason="unexpected_file",
                              detail=f"{rel} не описан в манифесте и не используется"))
    return items, manifest


def _wheel_pinned(requirements: Path, entry: dict) -> bool:
    reqs = parse_requirements(requirements)
    name, _, version = str(entry.get("id", "")).removeprefix("wheel:").partition("==")
    return any(norm_name(r.name) == name and r.version == version and entry.get("sha256") in r.hashes for r in reqs)


# ---------------------------------------------------------------------------------------------- check a prepared PC

def default_models_dir(repo: Path) -> tuple[Path, str]:
    env = os.environ.get("QORGAU_MODELS_DIR")
    if env:
        return Path(env).expanduser(), "QORGAU_MODELS_DIR"
    return repo / "models", "по умолчанию backend Settings (proctoring\\models)"


def default_audio_dir() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "QorgauExam" / "models" / "audio" if base else None


def site_packages(venv: Path) -> Path | None:
    for candidate in (venv / "Lib" / "site-packages", *sorted((venv / "lib").glob("python3*/site-packages"))):
        if candidate.is_dir():
            return candidate
    return None


def installed_distributions(site: Path) -> dict[str, tuple[str, Path]]:
    found: dict[str, tuple[str, Path]] = {}
    for info in site.glob("*.dist-info"):
        meta = info / "METADATA"
        if not meta.is_file():
            continue
        message = email.parser.Parser().parsestr(meta.read_text(encoding="utf-8", errors="replace"), headersonly=True)
        if message.get("Name") and message.get("Version"):
            found[norm_name(message["Name"])] = (message["Version"], info)
    return found


def verify_record(site: Path, info: Path) -> str | None:
    """Deep check: every file listed with a hash in RECORD must match. Returns a problem or None."""
    import base64
    import csv
    record = info / "RECORD"
    if not record.is_file():
        return "RECORD отсутствует"
    with open(record, encoding="utf-8", newline="") as stream:
        for row in csv.reader(stream):
            if len(row) < 2 or not row[1].startswith("sha256="):
                continue
            path = (site / row[0]).resolve()
            if not path.is_file():
                return f"нет файла {row[0]}"
            with open(path, "rb") as file:
                digest = base64.urlsafe_b64encode(hashlib.file_digest(file, "sha256").digest()).rstrip(b"=").decode()
            if digest != row[1][7:]:
                return f"изменён файл {row[0]}"
    return None


def check_pc(repo: Path, role: str, python: Path | None, models_dir: Path | None, audio_dir: Path | None,
             kit: Path | None, deep: bool, backend_only: bool = False) -> list[Item]:
    items: list[Item] = []
    kit_pins = load_kit_pins(repo)
    venv_hint = "py -3.12 -m venv .venv; .\\.venv\\Scripts\\python.exe -m pip install --no-index --find-links <kit>\\wheels " \
                "--require-hashes -r requirements\\full.txt"
    # --- Python interpreter / venv (static: nothing is executed) ---
    python = python or repo / ".venv" / "Scripts" / "python.exe"
    py_item = Item(id="python:interpreter", kind="python", requirement="required", roles=[role],
                   feature="Python 3.12 x64 (venv)", install_to=str(python), remedy=venv_hint)
    venv = python.parent.parent
    site = None
    if not python.is_file():
        items.append(py_item.fail(MISSING, "file_missing", f"нет {python}"))
    else:
        kind = file_binary_kind(python)
        cfg = venv / "pyvenv.cfg"
        values = {}
        if cfg.is_file():
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                key, sep, value = line.partition("=")
                if sep:
                    values[key.strip().lower()] = value.strip()
        version = values.get("version_info") or values.get("version") or ""
        home = Path(values["home"]) if values.get("home") else None
        if kind != "pe-x64":
            py_item.fail(WRONG_PLATFORM, "wrong_platform", f"{python.name}: {kind}, нужен Windows x64 (PE AMD64)")
        elif not cfg.is_file():
            py_item.fail(CORRUPT, "not_a_venv", f"нет {cfg}: ожидается venv, созданный на этом ПК")
        elif not version.startswith("3.12."):
            py_item.fail(WRONG_PLATFORM, "wrong_python", f"pyvenv.cfg version={version or '?'}, нужен 3.12.x")
        elif home is None or not home.is_dir():
            py_item.fail(CORRUPT, "venv_moved", f"базовый Python home={home} не найден: venv скопирован с другого ПК",
                         "Пересоздайте venv на этом ПК из колёс комплекта (без интернета): " + venv_hint)
        else:
            py_item.detail = f"Python {version}, PE x64, home существует"
        items.append(py_item)
        site = site_packages(venv)
    # --- installed distributions vs pins ---
    reqs = parse_requirements(repo / "requirements" / "full.txt")
    classify_requirements(repo, reqs)
    wanted_groups = {"teacher": ("runtime",), "student": ("runtime", "cv")}[role]
    installed = installed_distributions(site) if site else {}
    for req in reqs:
        if not req.applies:
            continue
        key = norm_name(req.name)
        item = Item(id=f"package:{key}", kind="python", requirement="required" if req.group in wanted_groups else "optional",
                    roles=[role], feature=f"Python {req.group}", expected_sha256=None,
                    pin_source="requirements/full.txt", remedy=venv_hint)
        if site is None:
            if item.requirement == "required" and py_item.status == PASS:
                items.append(item.fail(MISSING, "site_packages_missing", f"нет site-packages в {venv}"))
            elif item.requirement == "required":
                items.append(item.fail(MISSING, "python_not_ready", "интерпретатор не готов"))
            continue
        current = installed.get(key)
        if current is None:
            items.append(item.fail(MISSING, "not_installed", f"{req.name}=={req.version} не установлен"))
        elif current[0] != req.version:
            items.append(item.fail(CORRUPT, "version_mismatch", f"{req.name} {current[0]}, закреплено {req.version}"))
        else:
            problem = verify_record(site, current[1]) if deep and item.requirement == "required" else None
            if problem:
                item.fail(CORRUPT, "record_mismatch", f"{req.name}: {problem}")
            else:
                item.detail = f"{req.version}" + (" (RECORD проверен)" if deep and item.requirement == "required" else "")
            items.append(item)
    # --- models at their runtime locations ---
    if role == "student":
        resolved_models, origin = (models_dir, "--models-dir") if models_dir else default_models_dir(repo)
        audio = audio_dir or default_audio_dir()
        inputs = Inputs(repo, ("student",), resolved_models, audio, None, None, repo / "requirements" / "full.txt")
        pins = load_model_pins(repo, kit_pins)
        for item in model_items(inputs, pins):
            item.install_to = None
            if item.kind == "model" and item.status == PASS:
                item.detail = f"{item.detail + '; ' if item.detail else ''}каталог: {origin}"
            items.append(item)
        if any(ord(ch) > 127 for ch in str(resolve_or_self(resolved_models))):
            items.append(Item(id="models:path-ascii", kind="config", requirement="info", roles=[role],
                              feature="путь к моделям", status=WARN, reason="non_ascii_path",
                              detail=f"{resolved_models} содержит не-ASCII символы. FaceLandmarker загружается по пути "
                                     "(model_asset_path); поведение MediaPipe с такими путями в Windows этим инструментом не проверено.",
                              remedy="Для репетиции используйте ASCII-путь, например C:\\AdalModels, и QORGAU_MODELS_DIR."))
        if not backend_only:
            items.extend(electron_state_items(repo, kit_pins, kit, deep))
        for rel in () if backend_only else ("dist/main/main.cjs", "dist/preload/preload.cjs", "dist/renderer/index.html"):
            path = repo / "desktop" / Path(*rel.split("/"))
            item = Item(id=f"build:{rel}", kind="build", requirement="required", roles=[role], feature="сборка desktop",
                        remedy=f"{ONLINE}: в proctoring\\desktop выполнить npm run build (актуальность сборки проверяет Start-Student.ps1)")
            items.append(item if path.is_file() else item.fail(MISSING, "file_missing", f"нет {path}"))
    else:
        panel = repo / "class-panel" / "index.html"
        item = Item(id="build:class-panel/index.html", kind="build", requirement="required", roles=[role],
                    feature="панель преподавателя", remedy="Используйте полную копию репозитория той же фиксации.")
        items.append(item if panel.is_file() else item.fail(MISSING, "file_missing", f"нет {panel}"))
    items.extend(launcher_audit(repo, role))
    return items


def resolve_or_self(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def electron_state_items(repo: Path, kit_pins: dict, kit: Path | None, deep: bool) -> list[Item]:
    pin = load_electron_pin(repo, kit_pins)
    base = repo / "desktop" / "node_modules" / "electron"
    remedy = (f"{ONLINE}: в proctoring\\desktop выполнить npm ci и node node_modules/electron/install.js. Без интернета: "
              f"распаковать <kit>\\electron\\{pin.artifact} в desktop\\node_modules\\electron\\dist и записать path.txt "
              "с текстом electron.exe (см. REHEARSAL_3PC.md).")
    items = []
    item = Item(id="electron:binary", kind="electron", requirement="required", roles=["student"],
                feature=f"Electron {pin.version} win32-x64", remedy=remedy)
    exe = base / "dist" / "electron.exe"
    package_version = None
    try:
        package_version = json.loads((base / "package.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        pass
    if package_version is None:
        items.append(item.fail(MISSING, "npm_package_missing", f"нет {base / 'package.json'} (npm ci не выполнен)"))
    elif package_version != pin.version:
        items.append(item.fail(CORRUPT, "version_mismatch", f"node_modules electron {package_version}, lock {pin.version}"))
    elif not exe.is_file():
        items.append(item.fail(MISSING, "file_missing", f"нет {exe}"))
    else:
        kind = file_binary_kind(exe)
        dist_version = ""
        try:
            dist_version = (base / "dist" / "version").read_text(encoding="ascii").strip().lstrip("v")
        except OSError:
            pass
        if kind != "pe-x64":
            item.fail(WRONG_PLATFORM, "wrong_platform", f"electron.exe: {kind}, нужен PE x64")
        elif dist_version != pin.version:
            item.fail(CORRUPT, "dist_version_mismatch", f"dist/version={dist_version or 'нет'}, ожидалось {pin.version}")
        else:
            item.detail = f"electron.exe PE x64, dist/version {dist_version}"
        items.append(item)
    trap = Item(id="electron:path.txt", kind="electron", requirement="info", roles=["student"],
                feature="защита от неявной загрузки Electron", remedy=remedy)
    try:
        path_txt = (base / "path.txt").read_text(encoding="utf-8").strip()
    except OSError:
        path_txt = None
    if path_txt != "electron.exe":
        trap.fail(WARN, "implicit_download_trap",
                  f"path.txt={path_txt!r}: node_modules/electron/index.js при запуске через electron.cmd/npm start "
                  "вызовет install.js и попытается скачать Electron. Start-Student.ps1 запускает dist\\electron.exe "
                  "напрямую и этим путём не затронут; packaging\\launch-windows.ps1 затронут.")
    else:
        trap.detail = "path.txt = electron.exe: index.js не будет скачивать Electron"
    items.append(trap)
    if kit is not None and pin.artifact and exe.is_file():
        zip_path = kit / "electron" / pin.artifact
        cmp_item = Item(id="electron:dist-vs-kit", kind="electron", requirement="required", roles=["student"],
                        feature="Electron dist совпадает с архивом комплекта", remedy=remedy)
        if not zip_path.is_file():
            cmp_item.fail(MISSING, "file_missing", f"в комплекте нет {zip_path}")
        else:
            problem = compare_dist(zip_path, base / "dist", deep)
            if problem:
                cmp_item.fail(CORRUPT, "dist_mismatch", problem)
            else:
                cmp_item.detail = "размеры" + (" и CRC32" if deep else "") + " всех файлов совпадают с архивом"
        items.append(cmp_item)
    return items


def compare_dist(zip_path: Path, dist: Path, deep: bool) -> str | None:
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename == "electron.d.ts":
                continue
            target = dist.joinpath(*PurePosixPath(info.filename).parts)
            if not target.is_file():
                return f"нет файла dist/{info.filename}"
            if target.stat().st_size != info.file_size:
                return f"размер dist/{info.filename} {target.stat().st_size} != {info.file_size}"
            if deep:
                crc = 0
                with open(target, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        crc = zlib.crc32(chunk, crc)
                if crc != info.CRC:
                    return f"CRC32 dist/{info.filename} не совпадает"
    return None


def launcher_audit(repo: Path, role: str) -> list[Item]:
    """Static scan of the launchers an operator may use: commands that would need the network."""
    scripts = ["acceptance/classroom/Start-Teacher.ps1", "acceptance/classroom/Start-Student.ps1",
               "packaging/launch-windows.ps1", "packaging/prepare-windows.ps1"]
    items = []
    for rel in scripts:
        path = repo / Path(*rel.split("/"))
        if not path.is_file():
            continue
        hits = []
        for number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            message = MESSAGE_START.search(line)  # advice inside an error/help text is not a command
            for match in NETWORK_TOKENS.finditer(line):
                if message is None or match.start() < message.start():
                    hits.append(f"{rel}:{number}: {match.group(0).strip()}")
        item = Item(id=f"launcher:{rel}", kind="launcher", requirement="info", roles=[role],
                    feature="команды лаунчера, требующие сети")
        if hits:
            note = "этап подготовки (онлайн)" if "prepare" in rel else "может обратиться к сети"
            item.fail(WARN, "network_command", f"{note}: " + "; ".join(hits[:6]))
        else:
            item.detail = "сетевых/установочных команд не найдено"
        items.append(item)
    return items


# ---------------------------------------------------------------------------------------------- plan

def plan_text(repo: Path) -> str:
    pin = load_electron_pin(repo, load_kit_pins(repo))
    return f"""ЭТАП 0 — ПОДГОТОВКА С ИНТЕРНЕТОМ (выполняет оператор вручную, ДО отключения; этот инструмент ничего не скачивает)
Копия репозитория по ASCII-пути без пробелов, например C:\\Adal\\Qostanay_hub. PowerShell из proctoring\\:

  py -3.12 -m venv .venv
  .\\.venv\\Scripts\\python.exe -m pip install --require-hashes -r requirements\\full.txt
  .\\.venv\\Scripts\\python.exe -m pip download --only-binary=:all: --require-hashes --no-deps -r requirements\\full.txt -d D:\\AdalPrep\\wheels
  # только PC2/PC3: модели в proctoring\\models (значение backend по умолчанию, QORGAU_MODELS_DIR не нужен)
  $env:PYTHONPATH = "$PWD\\backend;$PWD\\contracts\\python"
  .\\.venv\\Scripts\\python.exe -m proctor.phone.prepare --download --models-dir "$PWD\\models"
  .\\.venv\\Scripts\\python.exe -m proctor.attention.model_tool fetch --models-dir "$PWD\\models"
  .\\.venv\\Scripts\\python.exe -m proctor.audio.prepare --download        # рекомендуется; %LOCALAPPDATA%\\QorgauExam\\models\\audio
  .\\.venv\\Scripts\\python.exe -m proctor.identity.prepare --download --models-dir "$PWD\\models"   # необязательно
  Remove-Item Env:PYTHONPATH
  Set-Location desktop; npm ci; node node_modules/electron/install.js; npm run build; Set-Location ..
  # архив Electron {pin.artifact} после install.js лежит в %LOCALAPPDATA%\\electron\\Cache

ЭТАП 1 — СБОРКА КОМПЛЕКТА (без сети, только копирование проверенных файлов; --out вне репозитория):
  .\\.venv\\Scripts\\python.exe acceptance\\offline\\adal_offline_kit.py build --models-dir "$PWD\\models" `
      --audio-dir "$env:LOCALAPPDATA\\QorgauExam\\models\\audio" --wheelhouse D:\\AdalPrep\\wheels `
      --electron-zip "$env:LOCALAPPDATA\\electron\\Cache" --out E:\\AdalKit

ЭТАП 2 — ПОСЛЕ ОТКЛЮЧЕНИЯ ИНТЕРНЕТА (ЛВС сохраняется) на каждом ПК:
  .\\.venv\\Scripts\\python.exe acceptance\\offline\\adal_offline_kit.py verify --kit E:\\AdalKit
  .\\.venv\\Scripts\\python.exe acceptance\\offline\\adal_offline_kit.py check-pc --role student --kit E:\\AdalKit   (PC2/PC3)
  .\\.venv\\Scripts\\python.exe acceptance\\offline\\adal_offline_kit.py check-pc --role teacher                    (PC1)
Подробно: proctoring/acceptance/offline/REHEARSAL_3PC.md
"""


# ---------------------------------------------------------------------------------------------- output

LABEL = {PASS: "OK", MISSING: "НЕТ", CORRUPT: "БИТЫЙ", WRONG_PLATFORM: "ПЛАТФОРМА", BLOCKED: "BLOCKED", WARN: "ВНИМАНИЕ"}


def render(title: str, items: list[Item], status: str, code: int) -> str:
    lines = [title]
    order = {"required": 0, "optional": 1, "info": 2}
    for item in sorted(items, key=lambda i: (order.get(i.requirement, 3), i.status == PASS, i.id)):
        if item.status == PASS and item.kind in ("wheel", "python") and item.requirement != "info":
            continue
        tag = LABEL.get(item.status, item.status)
        if item.requirement == "optional" and item.status != PASS:
            tag += ", необяз."
        size = f" {human_size(item.size_bytes)}" if item.size_bytes else ""
        sha = f" sha256 {item.sha256[:12]}…" if item.sha256 and item.status == PASS else ""
        lines.append(f"[{tag}] {item.id}{size}{sha} — {item.feature}")
        if item.detail and item.status != PASS or item.kind in ("electron", "launcher", "config") and item.detail:
            lines.append(f"       {item.detail}")
        if item.status != PASS and item.remedy:
            lines.append(f"       Как исправить: {item.remedy}")
    wheels = [i for i in items if i.kind in ("wheel", "python") and i.requirement != "info"]
    if wheels:
        ok = sum(i.status == PASS for i in wheels)
        lines.append(f"Python-пакеты/колёса: {ok}/{len(wheels)} OK (показаны только проблемные)")
    off = sorted(i.feature for i in items if i.requirement == "optional" and i.status != PASS)
    if off:
        lines.append("Необязательные функции без артефактов (будут недоступны, это не PASS): " + "; ".join(sorted(set(off))))
    verdict = {"READY": "ГОТОВО", "NOT_READY": "НЕ ГОТОВО", "BLOCKED": "ЗАБЛОКИРОВАНО"}[status]
    counts = {s: sum(i.status == s and i.requirement == "required" for i in items) for s in (MISSING, CORRUPT, WRONG_PLATFORM, BLOCKED)}
    detail = ", ".join(f"{LABEL[s]}: {n}" for s, n in counts.items() if n)
    lines.append(f"Итог: {verdict}{' (' + detail + ')' if detail else ''}. Код выхода {code}. "
                 "Проверены файлы, хэши и платформа; камера, точность CV и LAN не проверялись.")
    return "\n".join(lines)


def emit(args, title: str, items: list[Item], extra: dict | None = None) -> int:
    status, code = overall(items)
    report = {"schema": SCHEMA + "#report", "command": args.command, "created_utc": utc_now(), "target": TARGET["name"],
              "status": status, "exit_code": code, "items": [i.public() for i in items], **(extra or {})}
    if getattr(args, "report", None):
        write_report(args, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(title, items, status, code))
    return code


def write_report(args, report: dict) -> None:
    path = resolved(args.report)
    for forbidden in (getattr(args, "kit", None), getattr(args, "models_dir", None), getattr(args, "wheelhouse", None),
                      getattr(args, "audio_dir", None)):
        if forbidden is not None and is_within(path, forbidden):
            raise UsageError(f"--report {path} внутри входного каталога {forbidden}: запись туда запрещена")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------- CLI

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="adal_offline_kit.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="proctoring/ of the checkout (default: this one)")
    commands = parser.add_subparsers(dest="command", required=True)

    def common(sub, outputs=True):
        sub.add_argument("--target", default=TARGET["name"], help="only windows-x64 is supported")
        sub.add_argument("--json", action="store_true", help="machine-readable report on stdout")
        if outputs:
            sub.add_argument("--report", type=Path, help="also write the JSON report to this file")

    plan = commands.add_parser("plan", help="print the explicit online preparation steps (runs nothing)")
    common(plan, outputs=False)
    for name, text in (("inventory", "verify prepared inputs against repo pins (no copy)"),
                       ("build", "inventory + copy verified files into a new --out directory")):
        sub = commands.add_parser(name, help=text)
        common(sub)
        sub.add_argument("--role", choices=("all", *ROLES), default="all")
        sub.add_argument("--models-dir", type=Path, help="prepared models root (contains phone/, attention/, …)")
        sub.add_argument("--audio-dir", type=Path, help="prepared Silero dir (silero_vad.onnx, manifest.json, LICENSE.silero.txt)")
        sub.add_argument("--wheelhouse", type=Path, help="directory with Windows x64 cp312 wheels")
        sub.add_argument("--electron-zip", type=Path, help="electron-v<ver>-win32-x64.zip or a cache directory to search")
        sub.add_argument("--requirements", type=Path, help="default: requirements/full.txt of the checkout")
        if name == "build":
            sub.add_argument("--out", type=Path, required=True, help="new or empty directory outside the checkout")
    verify = commands.add_parser("verify", help="verify a built kit against its manifest and this checkout's pins")
    common(verify)
    verify.add_argument("--kit", type=Path, required=True)
    verify.add_argument("--role", choices=("all", *ROLES), default="all")
    check = commands.add_parser("check-pc", help="read-only offline readiness of this prepared PC")
    common(check)
    check.add_argument("--role", choices=ROLES, required=True)
    check.add_argument("--python", type=Path, help="venv python.exe (default: proctoring/.venv/Scripts/python.exe)")
    check.add_argument("--models-dir", type=Path, help="default: QORGAU_MODELS_DIR, else proctoring/models (as the backend)")
    check.add_argument("--audio-dir", type=Path, help="default: %%LOCALAPPDATA%%/QorgauExam/models/audio")
    check.add_argument("--kit", type=Path, help="also compare Electron dist with the kit archive")
    check.add_argument("--deep", action="store_true", help="also verify installed package RECORD hashes and Electron CRC32")
    check.add_argument("--backend-only", action="store_true", help="student backend/C2 diagnostics: skip Electron and desktop build")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code not in (0, None) else 0
    try:
        if args.target != TARGET["name"]:
            raise UsageError(f"--target {args.target}: поддерживается только {TARGET['name']}")
        repo = resolved(args.repo)
        if not (repo / "pyproject.toml").is_file() or not (repo / "backend" / "proctor").is_dir():
            raise UsageError(f"--repo {repo} не похож на proctoring/ (нет pyproject.toml или backend/proctor)")
        if args.command == "plan":
            print(plan_text(repo))
            return 0
        if args.command in ("inventory", "build"):
            roles = ROLES if args.role == "all" else (args.role,)
            inputs = Inputs(repo, roles, args.models_dir, args.audio_dir, args.wheelhouse, args.electron_zip,
                            args.requirements or repo / "requirements" / "full.txt")
            if not inputs.requirements.is_file():
                raise UsageError(f"нет файла требований {inputs.requirements}")
            items = inventory(inputs)
            if args.command == "build":
                check_output_location(args.out, inputs)
                status, code = overall(items)
                if code != EXIT_READY:
                    emit(args, f"Adal offline kit — сборка НЕ выполнена ({TARGET['name']}): исправьте пункты ниже", items)
                    return code
                manifest = build(inputs, args.out, items)
                return emit(args, f"Adal offline kit — собран в {resolved(args.out)} ({manifest['summary']['files']} файлов, "
                                  f"{human_size(manifest['summary']['bytes'])})", items, {"kit": str(resolved(args.out))})
            return emit(args, f"Adal offline kit — инвентаризация ({TARGET['name']}, роли: {', '.join(roles)})", items)
        if args.command == "verify":
            roles = ROLES if args.role == "all" else (args.role,)
            items, manifest = verify_kit(resolved(args.kit), repo, roles)
            extra = {"kit_commit": manifest.get("repo_commit"), "repo_commit": git_head(repo)} if manifest else {}
            return emit(args, f"Adal offline kit — проверка комплекта {resolved(args.kit)}", items, extra)
        if args.command == "check-pc":
            if args.backend_only and args.role != "student":
                raise UsageError("--backend-only применяется только с --role student")
            items = check_pc(repo, args.role, args.python, args.models_dir, args.audio_dir,
                             resolved(args.kit) if args.kit else None, args.deep, args.backend_only)
            return emit(args, f"Adal offline kit — готовность этого ПК без интернета (роль {args.role})", items)
    except UsageError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:  # malformed pins/requirements: never guess
        print(f"Ошибка входных данных: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
