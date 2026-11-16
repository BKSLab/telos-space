"""Публичный поиск и чтение веба без доступа к проектам, БД и настройкам.

Brave: https://api-dashboard.search.brave.com/api-reference/web/search/post
Pinning: https://www.encode.io/httpcore/extensions/#sni_hostname

Владелец composition layer передаёт два отдельных транспорта и закрывает их.
Read transport должен быть выделенным HTTP/1 transport с проверкой TLS,
без proxy/UDS, env-настроек и keepalive (max_keepalive_connections=0).
Он обязан сохранять переданные IP, Host и sni_hostname. Произвольный сторонний
transport с иной семантикой не считается безопасным. Клиент не создаёт transport,
не наследует AsyncClient auth/cookies и не закрывает переданные ресурсы.
"""

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

import httpx

from src.exceptions.web_context import (
    UnsafeWebUrlError,
    WebContentTooLargeError,
    WebContextTimeoutError,
    WebQueryPrivacyError,
    WebResponseError,
    WebUnsupportedContentError,
)
from src.schemas.web_context import (
    WebContextConfig,
    WebReadRequest,
    WebReadResult,
    WebSearchRequest,
    WebSearchResult,
    WebSource,
)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
READ_MEDIA_TYPES = {"text/html", "text/plain"}
FORBIDDEN_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan", ".onion", ".arpa")
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
CREDENTIAL_PATTERN = re.compile(
    r"telos_(?:key|token)_|bearer\s+\S+|"
    r"(?:api[_-]?key|access[_-]?token|token|password|secret|authorization|session|cookie)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
SENSITIVE_QUERY_KEYS = {
    "key",
    "apikey",
    "api_key",
    "api-key",
    "token",
    "access_token",
    "auth",
    "authorization",
    "password",
    "secret",
    "signature",
    "sig",
    "session",
    "cookie",
    "credential",
    "jwt",
}
IPV6_TRANSITION_NETWORKS = (
    ipaddress.ip_network("::/96"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)
RAW_CHUNK_BYTES = 65_536


class WebDnsResolver(Protocol):
    """Явно передаваемый резолвер всех адресов назначения."""

    async def resolve(self, hostname: str) -> Sequence[str]:
        """Возвращает все доступные A/AAAA адреса или поднимает OSError."""
        ...


class SystemWebDnsResolver:
    """Системный DNS; используется только при явной передаче в конструктор."""

    async def resolve(self, hostname: str) -> Sequence[str]:
        """Читает IPv4 и IPv6 без блокировки event loop.

        Args:
            hostname: Уже проверенное каноническое имя публичного сайта.
        Returns:
            Уникальные адреса, ни один из которых ещё не считается разрешённым.
        Raises:
            OSError: DNS-ответ получить не удалось.
        """
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
        return list(dict.fromkeys(str(record[4][0]) for record in records))


@dataclass(frozen=True, slots=True)
class _WebResponse:
    status: int
    headers: httpx.Headers
    body: bytes


class _TextProjection(HTMLParser):
    """Только видимый текст HTML, без исполнения и загрузки вложенных ресурсов."""

    SKIPPED_TAGS = {"head", "script", "style", "template", "noscript", "svg", "canvas"}
    BLOCK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "main",
        "aside",
        "nav",
        "header",
        "footer",
        "li",
        "ul",
        "ol",
        "tr",
        "td",
        "th",
        "table",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "blockquote",
    }
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.hidden_tags: list[str] = []
        self.hidden_counts: dict[str, int] = {}
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
        hidden = tag in self.SKIPPED_TAGS or any(
            key == "hidden" or key == "aria-hidden" and value == "true" for key, value in attrs
        )
        if self.hidden_tags or hidden:
            if tag not in self.VOID_TAGS:
                self.hidden_tags.append(tag)
                self.hidden_counts[tag] = self.hidden_counts.get(tag, 0) + 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if self.hidden_tags:
            if self.hidden_counts.get(tag, 0):
                # Каждый элемент удаляется один раз, даже при глубокой разметке.
                while self.hidden_tags:
                    closed = self.hidden_tags.pop()
                    self.hidden_counts[closed] -= 1
                    if closed == tag:
                        break
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID_TAGS:
            self.handle_starttag(tag, attrs)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if not self.hidden_tags and not self.in_title:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(
            line for raw in "".join(self.parts).splitlines() if (line := " ".join(raw.split()))
        )


class WebContextClient:
    """Изолированный клиент; разрешение публичной темы остаётся у сервиса."""

    def __init__(
        self,
        *,
        config: WebContextConfig,
        search_transport: httpx.AsyncBaseTransport,
        read_transport: httpx.AsyncBaseTransport,
        resolver: WebDnsResolver,
    ) -> None:
        """Принимает обязательные зависимости без скрытых сетевых ресурсов.

        Args:
            config: Лимиты и ключ только поискового провайдера.
            search_transport: Отдельный transport для фиксированного API Brave.
            read_transport: Выделенный TLS-верифицирующий HTTP/1 transport без
                proxy/UDS/env/keepalive; соблюдает pinned IP и SNI, см. модуль.
            resolver: Резолвер A/AAAA; итоговые адреса проверяет сам клиент.
        Raises:
            ValueError: Транспорты поиска и произвольных сайтов совпадают.
        """
        if search_transport is read_transport:
            raise ValueError("Поиск и чтение требуют отдельных транспортов.")
        self.config = config
        self.search_transport = search_transport
        self.read_transport = read_transport
        self.resolver = resolver

    @property
    def worst_case_seconds(self) -> float:
        """Общий deadline одного вызова без повторов, включая DNS и переходы."""
        return self.config.deadline_seconds

    async def search(
        self,
        request: WebSearchRequest,
        *,
        allow_external_query: bool = False,
    ) -> WebSearchResult:
        """Ищет только по публичной теме, отдельно разрешённой сервером.

        Args:
            request: Публичная тема без проектного или личного контекста.
            allow_external_query: Серверное разрешение; не аргумент модели/инструмента.
        Returns:
            Поисковые фрагменты с явным покрытием и версией; страницы не читаются.
        Raises:
            WebQueryPrivacyError: Сервер не разрешил тему или найдены credentials.
            WebContextTimeoutError: Исчерпан ограниченный бюджет.
            WebResponseError: Ошибка API либо неверная структура ответа.
            WebContentTooLargeError: Тело API слишком велико.
            WebUnsupportedContentError: API вернул сжатое или неподдерживаемое тело.
        """
        if allow_external_query is not True or _contains_credentials(request.query):
            raise WebQueryPrivacyError("Требуется отдельное разрешение публичной темы.")
        payload = {
            "q": request.query,
            "count": request.limit,
            "spellcheck": False,
            "result_filter": ["web"],
            "text_decorations": False,
        }
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "Connection": "close",
            "X-Subscription-Token": self.config.search_api_key.get_secret_value(),
        }
        api_request = self._request(
            "POST", BRAVE_SEARCH_URL, headers=headers, content=json.dumps(payload).encode()
        )
        try:
            async with asyncio.timeout(self.config.deadline_seconds):
                response = await self._exchange(
                    self.search_transport, api_request, {"application/json"}
                )
                if response.status != 200:
                    raise WebResponseError("Поисковый API не должен перенаправлять запрос.")
                return self._search_result(response.body, request.limit)
        except (TimeoutError, httpx.TimeoutException):
            raise WebContextTimeoutError("Исчерпан бюджет внешнего поиска.") from None
        except (httpx.HTTPError, OSError):
            # Диагностика HTTPX может включать query/URL; не копируем её в ошибку.
            raise WebResponseError("Поисковый API временно недоступен.") from None

    async def read(self, request: WebReadRequest) -> WebReadResult:
        """Читает публичный текст с проверкой каждого назначения до соединения.

        Args:
            request: Публичный адрес и лимит текстовой проекции.
        Returns:
            Текст, адреса, фактическое время, hash и честное покрытие.
        Raises:
            UnsafeWebUrlError: Непубличный адрес, DNS, credential или редирект.
            WebContextTimeoutError: Исчерпан бюджет DNS/чтения/переходов.
            WebResponseError: Сайт недоступен или вернул непригодный ответ.
            WebUnsupportedContentError: Требуются JS, распаковка или иной extractor.
            WebContentTooLargeError: Тело превысило предел до извлечения текста.
        """
        try:
            async with asyncio.timeout(self.config.deadline_seconds):
                url = _public_url(request.url)
                requested_url = url
                visited = set()
                for redirect in range(self.config.max_redirects + 1):
                    if url in visited:
                        raise UnsafeWebUrlError("Циклическое перенаправление запрещено.")
                    visited.add(url)
                    original = httpx.URL(url)
                    hostname = original.raw_host.decode("ascii")
                    address = await self._resolve(hostname)
                    headers = {
                        "Accept": "text/html, text/plain",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                        "Host": original.netloc.decode("ascii"),
                    }
                    fetch = self._request("GET", original.copy_with(host=address), headers=headers)
                    fetch.extensions["sni_hostname"] = hostname
                    response = await self._exchange(self.read_transport, fetch, READ_MEDIA_TYPES)
                    if response.status in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location or redirect >= self.config.max_redirects:
                            raise UnsafeWebUrlError(
                                "Превышен предел переходов или отсутствует адрес."
                            )
                        if any(ord(char) <= 32 or ord(char) == 127 for char in location):
                            raise UnsafeWebUrlError("Управляющие символы в переходе запрещены.")
                        url = _public_url(urljoin(url, location))
                        continue
                    return self._read_result(
                        response,
                        url,
                        requested_url,
                        min(request.max_chars, self.config.max_text_chars),
                    )
        except (TimeoutError, httpx.TimeoutException):
            raise WebContextTimeoutError("Исчерпан бюджет чтения веб-источника.") from None
        except (httpx.HTTPError, OSError):
            raise WebResponseError("Веб-источник временно недоступен.") from None
        raise WebResponseError("Веб-источник не вернул текст.")

    def _request(
        self, method: str, url: str | httpx.URL, *, headers: dict[str, str], content: bytes = b""
    ) -> httpx.Request:
        return httpx.Request(
            method,
            url,
            headers=headers,
            content=content,
            extensions={
                "timeout": dict.fromkeys(
                    ("connect", "read", "write", "pool"), self.config.io_timeout_seconds
                ),
            },
        )

    async def _resolve(self, hostname: str) -> str:
        try:
            return str(_public_ip(hostname))
        except ValueError:
            pass
        async with asyncio.timeout(self.config.io_timeout_seconds):
            addresses = await self.resolver.resolve(hostname)
        if not addresses or len(addresses) > 32:
            raise UnsafeWebUrlError("DNS не вернул ограниченный набор публичных адресов.")
        try:
            checked = [_public_ip(address) for address in addresses]
        except ValueError:
            raise UnsafeWebUrlError(
                "DNS содержит непубличное или некорректное назначение."
            ) from None
        return str(checked[0])

    async def _exchange(
        self, transport: httpx.AsyncBaseTransport, request: httpx.Request, media_types: set[str]
    ) -> _WebResponse:
        async with asyncio.timeout(self.config.io_timeout_seconds):
            response = await transport.handle_async_request(request)
            try:
                if response.status_code in REDIRECT_STATUSES:
                    return _WebResponse(response.status_code, response.headers, b"")
                if response.status_code != 200:
                    raise WebResponseError("Внешний источник вернул ошибку HTTP.")
                if (
                    response.headers.get("content-encoding", "identity").lower().strip()
                    != "identity"
                ):
                    raise WebUnsupportedContentError("Сжатые веб-ответы не поддерживаются.")
                media_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if media_type not in media_types:
                    raise WebUnsupportedContentError("Внешнее представление не поддерживается.")
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        length = int(content_length)
                    except ValueError:
                        raise WebResponseError("Неверный размер внешнего ответа.") from None
                    if length < 0:
                        raise WebResponseError("Неверный размер внешнего ответа.")
                    if length > self.config.max_response_bytes:
                        raise WebContentTooLargeError(
                            "Объявленный размер внешнего тела превышает предел."
                        )
                body = bytearray()
                if response.is_stream_consumed:
                    if len(response.content) > self.config.max_response_bytes:
                        raise WebContentTooLargeError("Внешнее тело превышает предел.")
                    body.extend(response.content)
                else:
                    async for chunk in response.aiter_raw(
                        chunk_size=min(RAW_CHUNK_BYTES, self.config.max_response_bytes + 1)
                    ):
                        if len(body) + len(chunk) > self.config.max_response_bytes:
                            raise WebContentTooLargeError("Внешнее тело превышает предел.")
                        body.extend(chunk)
                return _WebResponse(response.status_code, response.headers, bytes(body))
            finally:
                await response.aclose()

    def _search_result(self, body: bytes, limit: int) -> WebSearchResult:
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or data.get("type") != "search":
                raise ValueError
            web = data.get("web", {})
            if not isinstance(web, dict) or not isinstance(web.get("results", []), list):
                raise ValueError
            rows = web.get("results", [])
            now = datetime.now(UTC)
            items = []
            seen = set()
            for row in rows[:20]:
                if (
                    not isinstance(row, dict)
                    or not all(isinstance(row.get(key), str) for key in ("url", "title"))
                    or row.get("description") is not None
                    and not isinstance(row["description"], str)
                ):
                    raise ValueError
                try:
                    url = _public_url(row["url"])
                except UnsafeWebUrlError:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                text = _plain_text(row.get("description") or "")
                reported_date = row.get("page_age")
                if reported_date is not None and not isinstance(reported_date, str):
                    raise ValueError
                items.append(
                    WebSource(
                        source_id=_source_id(url),
                        url=url,
                        title=_plain_text(row["title"])[:512],
                        text=text[: self.config.max_text_chars],
                        fetched_at=now,
                        content_hash=_hash(text),
                        coverage="search_snippet",
                        total_chars=len(text),
                        reported_date=reported_date[:128] if reported_date else None,
                    )
                )
                if len(items) == limit:
                    break
            return WebSearchResult(fetched_at=now, items=items)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise WebResponseError("Неверная структура поискового ответа.") from None

    @staticmethod
    def _read_result(
        response: _WebResponse, url: str, requested_url: str, max_chars: int
    ) -> WebReadResult:
        media_type = response.headers["content-type"].split(";", 1)[0].strip().lower()
        charset_match = re.search(
            r"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)",
            response.headers["content-type"],
            re.IGNORECASE,
        )
        charset = charset_match[1] if charset_match else "utf-8"
        try:
            decoded = response.body.decode(charset)
        except (LookupError, UnicodeError):
            raise WebUnsupportedContentError("Кодировка веб-источника не поддерживается.") from None
        if "\x00" in decoded:
            raise WebUnsupportedContentError("Бинарное тело не является веб-текстом.")
        title = ""
        if media_type == "text/html":
            projection = _TextProjection()
            try:
                projection.feed(decoded)
                projection.close()
            except (ValueError, AssertionError):
                raise WebUnsupportedContentError("HTML источника не разобран.") from None
            title = " ".join("".join(projection.title_parts).split())[:512]
            text = projection.text()
        else:
            text = decoded.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            raise WebUnsupportedContentError("Источник не содержит доступной текстовой проекции.")
        return WebReadResult(
            source_id=_source_id(url),
            url=url,
            requested_url=requested_url,
            title=title,
            text=text[:max_chars],
            fetched_at=datetime.now(UTC),
            content_hash=_hash(text),
            coverage="truncated_text" if len(text) > max_chars else "extracted_text",
            total_chars=len(text),
            media_type=media_type,
            downloaded_bytes=len(response.body),
            reported_date=response.headers.get("last-modified", "")[:128] or None,
            version_hint=response.headers.get("etag", "")[:256] or None,
        )


def _contains_credentials(value: str) -> bool:
    decoded = unquote(value)
    if CREDENTIAL_PATTERN.search(decoded):
        return True
    for match in re.finditer(r"https?://[^\s]+", decoded, re.IGNORECASE):
        try:
            _public_url(match[0])
        except UnsafeWebUrlError:
            return True
    return False


def _public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value)
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_link_local
        or address.is_loopback
    ):
        raise ValueError("Непубличный IP.")
    if isinstance(address, ipaddress.IPv6Address) and (
        address.scope_id
        or address.ipv4_mapped
        or address.sixtofour
        or address.teredo
        or any(address in network for network in IPV6_TRANSITION_NETWORKS)
    ):
        raise ValueError("Переходный IPv6 запрещён.")
    return address


def _public_url(value: str) -> str:
    try:
        if (
            not value
            or len(value) > 4096
            or "\\" in value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or "@" in parsed.netloc
            or not parsed.hostname
            or parsed.netloc.endswith(":")
        ):
            raise ValueError
        scheme = parsed.scheme.lower()
        if parsed.port not in {None, 443 if scheme == "https" else 80}:
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if "%" in host or host.endswith(".") or len(host) > 253:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if (
                len(labels) < 2
                or labels[-1][0].isdigit()
                or not all(HOST_LABEL.fullmatch(label) for label in labels)
                or host.endswith(FORBIDDEN_HOST_SUFFIXES)
            ):
                raise ValueError from None
            authority = host
        else:
            address = _public_ip(str(address))
            authority = f"[{address}]" if address.version == 6 else str(address)
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            if unquote(key).lower() in SENSITIVE_QUERY_KEYS or unquote(key).lower().startswith(
                ("x-amz-", "x-goog-")
            ):
                raise ValueError
        if CREDENTIAL_PATTERN.search(unquote(parsed.path + "?" + parsed.query)):
            raise ValueError
        return str(httpx.URL(urlunsplit((scheme, authority, parsed.path or "/", parsed.query, ""))))
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise UnsafeWebUrlError(
            "Адрес не является разрешённым публичным HTTP(S)-источником."
        ) from None


def _plain_text(value: str) -> str:
    projection = _TextProjection()
    try:
        projection.feed(value)
        projection.close()
    except (ValueError, AssertionError):
        raise WebUnsupportedContentError("HTML фрагмента не разобран.") from None
    return projection.text()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_id(url: str) -> str:
    return f"web:{_hash(url)}"
