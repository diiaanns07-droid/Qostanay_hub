"""Exam policy content: mode ("url" = external site, "app" = separate program) and allow-lists.

URL pattern grammar (normalized form sent to the student client in `allowed_urls`):

    <scheme>://<host>[:<port>]<path-prefix>*

  * scheme: https (http is accepted with a warning);
  * host: lower-case, IDNA (punycode); a leading "*." means "this domain AND any subdomain"
    ("*.example.kz" matches example.kz, a.example.kz, b.a.example.kz); at least two labels after "*.";
  * path-prefix: starts with "/", ends with "/" or is a full path; the trailing "*" means "any path
    starting with this prefix". "https://exam.kz/*" allows the whole site;
  * no user:password@, no query or fragment, no wildcard anywhere else, no loopback hosts.

App rules are Windows executable basenames ("name.exe", case-insensitive), never paths.
`match_url()` is the reference matcher the student client must follow (handoffs/T04/STUDENT_CLIENT.md).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

MAX_RULES = 50
MAX_RULE_LEN = 300
MAX_INSTRUCTIONS = 2000

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,62}\.exe$", re.IGNORECASE)
_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "iexplore.exe", "browser.exe"}
_SHELLS = {"cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe", "explorer.exe", "regedit.exe", "taskmgr.exe"}


class ExamMode(StrEnum):
    URL = "url"  # «Внешний сайт»
    APP = "app"  # «Отдельная программа»


MODE_LABEL_RU = {ExamMode.URL: "Внешний сайт", ExamMode.APP: "Отдельная программа"}

AUTH_DOMAINS_NOTE_RU = (
    "Вход на сайт (через Google, Microsoft, портал вуза и т. п.) часто перенаправляет на другие домены. "
    "Если их нет в списке разрешённых, студент не сможет войти. Перед экзаменом откройте сайт в режиме "
    "проверки, пройдите вход и добавьте домены, на которые он перенаправляет. Список ниже — подсказка, "
    "он не полный и не проверен на вашем сайте."
)
TIMER_NOTE_RU = (
    "Блокировка нашим клиентом закрывает экран студента. Таймер внешнего сайта при этом НЕ "
    "останавливается: интеграции с сайтом нет. Учитывайте время блокировки сами."
)
# Commonly seen sign-in hosts of large identity providers. Suggestions only: never added
# automatically, and the teacher must verify them on the actual site.
SSO_SUGGESTIONS: dict[str, list[str]] = {
    "Google": ["https://accounts.google.com/*", "https://*.gstatic.com/*"],
    "Microsoft": [
        "https://login.microsoftonline.com/*",
        "https://login.live.com/*",
        "https://*.msauth.net/*",
        "https://*.msftauth.net/*",
    ],
}


class PolicyError(ValueError):
    def __init__(self, field_name: str, code: str, message_ru: str):
        super().__init__(message_ru)
        self.field = field_name
        self.code = code
        self.message_ru = message_ru


@dataclass(frozen=True)
class UrlPattern:
    scheme: str
    host: str  # without "*."
    wildcard: bool  # "*." prefix: domain and subdomains
    port: int | None
    path_prefix: str  # starts with "/"

    def __str__(self) -> str:
        host = ("*." if self.wildcard else "") + self.host
        port = f":{self.port}" if self.port is not None else ""
        return f"{self.scheme}://{host}{port}{self.path_prefix}*"


@dataclass
class NormalizedRules:
    rules: list[str] = field(default_factory=list)
    warnings_ru: list[str] = field(default_factory=list)


def _idna_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise PolicyError("allowed_urls", "invalid_host", f"Недопустимое имя сайта: {host}") from exc


def _is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def parse_url_pattern(raw: str, field_name: str = "allowed_urls") -> tuple[UrlPattern, list[str]]:
    """Teacher input -> UrlPattern (+ warnings). Accepts 'exam.kz', '*.exam.kz', 'https://exam.kz/path'."""
    text = (raw or "").strip()
    if not text:
        raise PolicyError(field_name, "empty", "Пустой адрес")
    if len(text) > MAX_RULE_LEN:
        raise PolicyError(field_name, "too_long", f"Адрес длиннее {MAX_RULE_LEN} символов")
    if any(ch.isspace() for ch in text):
        raise PolicyError(field_name, "invalid", f"Пробелы в адресе недопустимы: {text}")
    warnings: list[str] = []
    if "://" not in text:
        text = "https://" + text
    if text.endswith("*") and not text.endswith("/*"):
        raise PolicyError(field_name, "invalid_wildcard", f"«*» допустим только в начале имени (*.site.kz) или в конце пути (/…/*): {raw}")
    trailing_star = text.endswith("/*")
    if trailing_star:
        text = text[:-1]
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http"):
        raise PolicyError(field_name, "invalid_scheme", f"Разрешены только адреса http(s): {raw}")
    if scheme == "http":
        warnings.append(f"{raw}: незащищённое соединение (http)")
    if parts.username or parts.password or "@" in parts.netloc:
        raise PolicyError(field_name, "invalid", f"Логин и пароль в адресе недопустимы: {raw}")
    if parts.query or parts.fragment:
        raise PolicyError(field_name, "invalid", f"Уберите из адреса «?…» и «#…»: {raw}")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise PolicyError(field_name, "invalid_host", f"Нет имени сайта: {raw}")
    wildcard = False
    if host.startswith("*."):
        wildcard, host = True, host[2:]
    if "*" in host or "*" in parts.path:
        raise PolicyError(field_name, "invalid_wildcard", f"«*» допустим только в начале имени (*.site.kz) или в конце пути (/…/*): {raw}")
    try:
        port = parts.port
    except ValueError as exc:
        raise PolicyError(field_name, "invalid_port", f"Неверный порт: {raw}") from exc
    is_ip = False
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_ip = True
    except ValueError:
        host = _idna_host(host)
        labels = host.split(".")
        if not all(_LABEL_RE.match(label) for label in labels):
            raise PolicyError(field_name, "invalid_host", f"Недопустимое имя сайта: {raw}")
        if len(labels) < 2:
            raise PolicyError(field_name, "invalid_host", f"Нужно полное имя сайта (например, exam.example.kz): {raw}")
    if _is_loopback(host):
        raise PolicyError(field_name, "loopback", f"Локальные адреса компьютера студента недопустимы: {raw}")
    if is_ip and wildcard:
        raise PolicyError(field_name, "invalid_wildcard", f"«*.» нельзя применять к IP-адресу: {raw}")
    if is_ip:
        warnings.append(f"{raw}: IP-адрес вместо имени сайта; убедитесь, что адрес не меняется")
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"  # "/test/42" means the page and everything below it, never "/test/420"
    return UrlPattern(scheme, host, wildcard, port, path), warnings


def normalize_start_url(raw: str) -> str:
    """The exact exam address opened first (query kept, fragment dropped, host normalized)."""
    pattern, _ = parse_url_pattern((raw or "").split("#", 1)[0].split("?", 1)[0], "start_url")
    if pattern.wildcard:
        raise PolicyError("start_url", "invalid_wildcard", "Адрес экзамена должен быть конкретным, без «*»")
    text = (raw or "").strip().split("#", 1)[0]
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    port = f":{pattern.port}" if pattern.port is not None else ""
    query = f"?{parts.query}" if parts.query else ""
    return f"{pattern.scheme}://{pattern.host}{port}{parts.path or '/'}{query}"


def normalize_urls(raw_rules: list[str], field_name: str = "allowed_urls") -> NormalizedRules:
    if len(raw_rules) > MAX_RULES:
        raise PolicyError(field_name, "too_many", f"Не больше {MAX_RULES} адресов")
    out = NormalizedRules()
    for raw in raw_rules:
        pattern, warnings = parse_url_pattern(raw, field_name)
        text = str(pattern)
        if text not in out.rules:
            out.rules.append(text)
        out.warnings_ru.extend(warnings)
    return out


def normalize_apps(raw_apps: list[str]) -> NormalizedRules:
    if len(raw_apps) > MAX_RULES:
        raise PolicyError("allowed_apps", "too_many", f"Не больше {MAX_RULES} программ")
    out = NormalizedRules()
    for raw in raw_apps:
        name = (raw or "").strip()
        if "/" in name or "\\" in name or ":" in name:
            raise PolicyError("allowed_apps", "path_not_allowed", f"Укажите только имя файла программы, без пути: {raw}")
        if not _APP_RE.match(name):
            raise PolicyError("allowed_apps", "invalid_app", f"Ожидается имя программы вида name.exe: {raw}")
        low = name.lower()
        if low in _SHELLS:
            raise PolicyError("allowed_apps", "system_tool", f"{name}: системная программа не может быть разрешена в экзамене")
        if low in _BROWSERS:
            out.warnings_ru.append(f"{name}: разрешённый браузер открывает любые сайты — ограничение адресов не действует")
        if low not in out.rules:
            out.rules.append(low)
    return out


def _host_matches(pattern: UrlPattern, host: str) -> bool:
    if host == pattern.host:
        return True
    return pattern.wildcard and host.endswith("." + pattern.host)


def match_url(patterns: list[str], url: str) -> bool:
    """Reference matcher (the student client must implement exactly this, see STUDENT_CLIENT.md)."""
    try:
        parts = urlsplit(url)
        host = _idna_host((parts.hostname or "").rstrip("."))
        port = parts.port
    except (PolicyError, ValueError):
        return False
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        return False
    default_port = 443 if scheme == "https" else 80
    path = parts.path or "/"
    for text in patterns:
        try:
            pattern, _ = parse_url_pattern(text)
        except PolicyError:
            continue
        if pattern.scheme != scheme or not _host_matches(pattern, host):
            continue
        if (pattern.port or default_port) != (port or default_port):
            continue
        if path.startswith(pattern.path_prefix) or path + "/" == pattern.path_prefix:
            return True
    return False


@dataclass(frozen=True)
class PolicyContent:
    """Validated, normalized policy content (what is versioned and sent to students)."""

    mode: ExamMode
    start_url: str | None
    allowed_urls: tuple[str, ...]
    auth_domains: tuple[str, ...]  # extra sign-in domains, kept apart so the UI can explain them
    allowed_apps: tuple[str, ...]
    instructions_ru: str
    warnings_ru: tuple[str, ...] = ()

    def client_payload(self) -> dict:
        """The exam block of `welcome` / `apply_policy` (v1 fields + additive start_url/auth_domains)."""
        return {
            "mode": self.mode.value,
            "start_url": self.start_url,
            "allowed_urls": list(dict.fromkeys([*self.allowed_urls, *self.auth_domains])),
            "auth_domains": list(self.auth_domains),
            "allowed_apps": list(self.allowed_apps),
            "instructions_ru": self.instructions_ru,
            "site_timer_control": "none",
        }


def build_policy_content(
    *,
    mode: str,
    start_url: str | None = None,
    allowed_urls: list[str] | None = None,
    auth_domains: list[str] | None = None,
    allowed_apps: list[str] | None = None,
    instructions_ru: str = "",
) -> PolicyContent:
    try:
        exam_mode = ExamMode(mode)
    except ValueError as exc:
        raise PolicyError("mode", "invalid_mode", "Выберите «Внешний сайт» или «Отдельная программа»") from exc
    if len(instructions_ru) > MAX_INSTRUCTIONS:
        raise PolicyError("instructions_ru", "too_long", f"Инструкция длиннее {MAX_INSTRUCTIONS} символов")
    warnings: list[str] = []
    urls = normalize_urls(allowed_urls or [])
    auth = normalize_urls(auth_domains or [], "auth_domains")
    apps = normalize_apps(allowed_apps or [])
    warnings += urls.warnings_ru + auth.warnings_ru + apps.warnings_ru
    start: str | None = None
    if exam_mode == ExamMode.URL:
        if not start_url:
            raise PolicyError("start_url", "required", "Укажите адрес экзамена — он откроется у студента первым")
        start_pattern, w = parse_url_pattern(start_url.split("#", 1)[0].split("?", 1)[0], "start_url")
        warnings += w
        start = normalize_start_url(start_url)
        port = f":{start_pattern.port}" if start_pattern.port is not None else ""
        rules = list(urls.rules)
        if not match_url(rules + list(auth.rules), start):
            site = f"{start_pattern.scheme}://{start_pattern.host}{port}/*"
            rules.insert(0, site)
            warnings.append(f"Сайт экзамена {site} добавлен в разрешённые адреса автоматически")
        if apps.rules:
            raise PolicyError("allowed_apps", "mode_mismatch", "Для режима «Внешний сайт» список программ не используется")
        return PolicyContent(exam_mode, start, tuple(rules), tuple(auth.rules), (), instructions_ru, tuple(warnings))
    if not apps.rules:
        raise PolicyError("allowed_apps", "required", "Укажите хотя бы одну программу (name.exe)")
    if urls.rules or auth.rules or start_url:
        raise PolicyError("allowed_urls", "mode_mismatch", "Для режима «Отдельная программа» адреса сайтов не используются")
    return PolicyContent(exam_mode, None, (), (), tuple(apps.rules), instructions_ru, tuple(warnings))
