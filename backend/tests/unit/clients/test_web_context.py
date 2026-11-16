"""Публичный веб-контекст: privacy, фактический pinning и честное покрытие."""

import asyncio
import hashlib
import json
from datetime import UTC

import httpcore
import httpx
import pytest
from pydantic import ValidationError

from src.clients.web_context import BRAVE_SEARCH_URL, WebContextClient
from src.exceptions.web_context import (
    UnsafeWebUrlError,
    WebContentTooLargeError,
    WebContextTimeoutError,
    WebQueryPrivacyError,
    WebResponseError,
    WebUnsupportedContentError,
)
from src.schemas.web_context import WebContextConfig, WebReadRequest, WebSearchRequest

PUBLIC_IP = "93.184.216.34"
PAGE_URL = "https://docs.example.com/article"


class Resolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = list(addresses or (PUBLIC_IP,))
        self.calls: list[str] = []

    async def resolve(self, hostname: str) -> list[str]:
        self.calls.append(hostname)
        return self.addresses


class BodyStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, blocker: asyncio.Event | None = None) -> None:
        self.chunks = chunks
        self.blocker = blocker
        self.closed = False
        self.reads = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.blocker is not None:
                await self.blocker.wait()
            self.reads += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def page(
    text: str = "Публичный текст", *, headers: dict | None = None, status: int = 200
) -> httpx.Response:
    return httpx.Response(
        status,
        content=text.encode(),
        headers={"Content-Type": "text/plain; charset=utf-8", **(headers or {})},
    )


def search_response(rows: list[dict] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"type": "search", "web": {"results": rows or []}})


def build_client(*, search=None, read=None, resolver=None, **limits) -> WebContextClient:
    return WebContextClient(
        config=WebContextConfig(search_api_key="test-brave-secret", **limits),
        search_transport=httpx.MockTransport(search or (lambda request: search_response())),
        read_transport=httpx.MockTransport(read or (lambda request: page())),
        resolver=resolver if resolver is not None else Resolver(),
    )


async def test_search_requires_server_gate_and_sends_only_public_query(caplog) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return search_response(
            [
                {
                    "url": PAGE_URL + "#section",
                    "title": "<b>Руководство</b>",
                    "description": "<b>Открытый</b> метод",
                    "page_age": "2026-10-12T00:00:00Z",
                },
                {"url": PAGE_URL, "title": "Повтор", "description": "Дубликат"},
                {
                    "url": "http://127.0.0.1/private",
                    "title": "Внутренний",
                    "description": "Запрещён",
                },
                {
                    "url": "https://other.example.com/",
                    "title": "Другая страница",
                    "description": None,
                },
            ]
        )

    client = build_client(search=handler)
    request = WebSearchRequest(query="  метод критического пути  ", limit=3)
    for allowed in (None, False, 1, "true"):
        with pytest.raises(WebQueryPrivacyError):
            await client.search(request, allow_external_query=allowed)
    for sensitive in (
        "api_key=private-value",
        "Bearer secret-token",
        "telos_token_private",
        "token=private",
        "https://example.com/?access_token=private",
        "https://name:password@example.com/",
        "https://127.0.0.1/private",
    ):
        with pytest.raises(WebQueryPrivacyError):
            await client.search(WebSearchRequest(query=sensitive), allow_external_query=True)
    assert not calls

    result = await client.search(request, allow_external_query=True)
    assert len(calls) == 1
    sent = calls[0]
    assert sent.method == "POST" and str(sent.url) == BRAVE_SEARCH_URL
    assert not sent.url.query
    assert json.loads(sent.content) == {
        "q": "метод критического пути",
        "count": 3,
        "spellcheck": False,
        "result_filter": ["web"],
        "text_decorations": False,
    }
    assert sent.headers["X-Subscription-Token"] == "test-brave-secret"
    assert sent.headers["Accept-Encoding"] == "identity"
    assert "cookie" not in sent.headers and "authorization" not in sent.headers
    assert len(result.items) == 2
    item = result.items[0]
    assert item.url == PAGE_URL and item.title == "Руководство" and item.text == "Открытый метод"
    assert item.content_hash == hashlib.sha256(item.text.encode()).hexdigest()
    assert item.coverage == "search_snippet" and item.source_kind == "web"
    assert item.reported_date == "2026-10-12T00:00:00Z" and item.fetched_at.tzinfo == UTC
    assert result.items[1].text == "" and result.items[1].coverage == "search_snippet"
    assert not caplog.text


def test_contract_forbids_context_and_limits_external_inputs() -> None:
    for data in (
        {"query": " "},
        {"query": "a" * 601},
        {"query": "a " * 76},
        {"query": "one\ntwo"},
        {"query": "a", "limit": 21},
        {"query": "a", "project_id": 1},
        {"query": "a", "allow_external_query": True},
    ):
        with pytest.raises(ValidationError):
            WebSearchRequest(**data)
    with pytest.raises(ValidationError):
        WebReadRequest(url=PAGE_URL, headers={"Authorization": "secret"})
    with pytest.raises(ValidationError):
        WebContextConfig(search_api_key="test", deadline_seconds=21)
    same = httpx.MockTransport(lambda request: page())
    with pytest.raises(ValueError, match="отдельных"):
        WebContextClient(
            config=WebContextConfig(search_api_key="test"),
            search_transport=same,
            read_transport=same,
            resolver=Resolver(),
        )


async def test_unsafe_and_ambiguous_urls_never_resolve_or_fetch() -> None:
    calls = []
    resolver = Resolver()
    client = build_client(read=lambda request: calls.append(request) or page(), resolver=resolver)
    unsafe = (
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://localhost/",
        "http://host/",
        "http://service.internal/",
        "http://host.localhost/",
        "http://host.local/",
        "http://host.lan/",
        "http://host.onion/",
        "https://example.com:8443/",
        "http://example.com:443/",
        "https://user:pass@example.com/",
        "https://example.com\\@internal.local/",
        "https://example.com\n/",
        "https://example.com./",
        "https://bad_host.example.com/",
        "https://example.com/?token=sensitive",
        "https://example.com/?X-Amz-Signature=sensitive",
        "https://example.com/telos_key_private_value",
        "http://%31%32%37.0.0.1/",
        "http://127.1/",
        "http://127.0.0.0x1/",
        "http://0177.0.0.1/",
        "http://2130706433/",
        "http://0x7f000001/",
        "https://example.com:/",
        "http://0x7f.0.0.1/",
        "http://0.0.0.0/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.0.1/",
        "http://100.64.0.1/",
        "http://169.254.169.254/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
        "http://[::1]/",
        "http://[fe80::1%25eth0]/",
        "http://[fc00::1]/",
        "http://[ff02::1]/",
        "http://[::ffff:8.8.8.8]/",
        "http://[2002:0808:0808::]/",
        "http://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]/",
        "http://[64:ff9b::808:808]/",
        "http://[64:ff9b:1::808:808]/",
    )
    for url in unsafe:
        with pytest.raises(UnsafeWebUrlError):
            await client.read(WebReadRequest(url=url))
    assert not calls and not resolver.calls


async def test_dns_requires_every_address_public_and_pins_only_one_checked_address() -> None:
    calls = []
    for addresses in (
        [PUBLIC_IP, "127.0.0.1"],
        ["::ffff:127.0.0.1"],
        ["169.254.169.254"],
        [PUBLIC_IP, "fc00::1"],
        ["nonsense"],
        ["64:ff9b::808:808"],
    ):
        client = build_client(
            read=lambda request: calls.append(request) or page(), resolver=Resolver(*addresses)
        )
        with pytest.raises(UnsafeWebUrlError):
            await client.read(WebReadRequest(url=PAGE_URL))
    assert not calls

    empty_resolver = Resolver()
    empty_resolver.addresses = []
    with pytest.raises(UnsafeWebUrlError):
        await build_client(resolver=empty_resolver).read(WebReadRequest(url=PAGE_URL))

    class RebindingResolver(Resolver):
        async def resolve(self, hostname: str) -> list[str]:
            self.calls.append(hostname)
            return [PUBLIC_IP, "2606:4700:4700::1111"] if len(self.calls) == 1 else ["127.0.0.1"]

    resolver = RebindingResolver()
    client = build_client(
        read=lambda request: calls.append(request) or page(headers={"Set-Cookie": "secret=value"}),
        resolver=resolver,
    )
    result = await client.read(WebReadRequest(url=PAGE_URL))
    assert result.url == PAGE_URL
    assert resolver.calls == ["docs.example.com"]
    sent = calls[0]
    assert sent.url.host == PUBLIC_IP and sent.url.path == "/article"
    assert sent.headers["Host"] == "docs.example.com"
    assert sent.extensions["sni_hostname"] == "docs.example.com"
    assert sent.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 10)
    assert sent.headers["Connection"] == "close"
    assert sent.headers["Accept-Encoding"] == "identity"
    assert not any(
        name in sent.headers
        for name in ("authorization", "cookie", "x-subscription-token", "proxy-authorization")
    )
    direct_resolver = Resolver()
    await build_client(resolver=direct_resolver).read(WebReadRequest(url=f"https://{PUBLIC_IP}/"))
    assert not direct_resolver.calls


async def test_real_http_transport_connects_to_pinned_ip_and_verifies_original_sni() -> None:
    connections = []
    tls = []
    writes = []

    class Stream(httpcore.AsyncNetworkStream):
        async def read(self, max_bytes, **kwargs):
            return b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"

        async def write(self, buffer, **kwargs):
            writes.append(buffer)

        async def aclose(self):
            pass

        async def start_tls(self, ssl_context, server_hostname=None, **kwargs):
            tls.append((server_hostname, ssl_context.check_hostname))
            return self

        def get_extra_info(self, info):
            return None

    class Backend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, **kwargs):
            connections.append((host, port))
            return Stream()

        async def connect_unix_socket(self, *args, **kwargs):
            pytest.fail("UDS запрещён")

    transport = httpx.AsyncHTTPTransport(
        trust_env=False, http2=False, limits=httpx.Limits(max_keepalive_connections=0)
    )
    transport._pool._network_backend = Backend()
    resolver = Resolver()
    client = WebContextClient(
        config=WebContextConfig(search_api_key="test"),
        search_transport=httpx.MockTransport(lambda request: search_response()),
        read_transport=transport,
        resolver=resolver,
    )
    try:
        result = await client.read(WebReadRequest(url=PAGE_URL))
        assert result.text == "ok"
        assert connections == [(PUBLIC_IP, 443)]
        assert tls == [("docs.example.com", True)]
        sent = b"".join(writes).lower()
        assert b"host: docs.example.com" in sent and b"connection: close" in sent
        assert b"authorization:" not in sent and b"cookie:" not in sent
    finally:
        await transport.aclose()


async def test_redirects_revalidate_url_dns_and_never_forward_credentials() -> None:
    calls = []

    def allowed(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/article":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://other.example.com/final",
                    "Set-Cookie": "hidden=value",
                },
            )
        return page()

    resolver = Resolver()
    result = await build_client(read=allowed, resolver=resolver).read(WebReadRequest(url=PAGE_URL))
    assert result.requested_url == PAGE_URL and result.url == "https://other.example.com/final"
    assert resolver.calls == ["docs.example.com", "other.example.com"]
    assert [call.headers["Host"] for call in calls] == ["docs.example.com", "other.example.com"]
    assert all(
        call.url.host == PUBLIC_IP
        and "cookie" not in call.headers
        and "authorization" not in call.headers
        for call in calls
    )

    for location in (
        "http://169.254.169.254/latest/meta-data",
        "https://user:password@example.com/",
        "https://other.example.com/?token=private",
        "file:///private",
        "/article",
        "https://example.com/\narticle",
    ):
        blocked_calls = []

        def redirect(request, blocked_calls=blocked_calls, location=location):
            blocked_calls.append(request)
            return httpx.Response(302, headers={"Location": location})

        with pytest.raises(UnsafeWebUrlError):
            await build_client(read=redirect).read(WebReadRequest(url=PAGE_URL))
        assert len(blocked_calls) == 1

    resolver = Resolver()

    async def changing_dns(hostname):
        resolver.calls.append(hostname)
        return [PUBLIC_IP] if len(resolver.calls) == 1 else ["10.0.0.1"]

    resolver.resolve = changing_dns
    with pytest.raises(UnsafeWebUrlError):
        await build_client(
            read=lambda request: httpx.Response(302, headers={"Location": "/new"}),
            resolver=resolver,
        ).read(WebReadRequest(url=PAGE_URL))
    assert resolver.calls == ["docs.example.com", "docs.example.com"]
    for headers in ({}, {"Location": "/new"}):
        with pytest.raises(UnsafeWebUrlError):
            await build_client(
                read=lambda request, headers=headers: httpx.Response(307, headers=headers),
                max_redirects=0,
            ).read(WebReadRequest(url=PAGE_URL))


async def test_text_projection_preserves_unicode_hash_and_honest_coverage() -> None:
    html = """<html><head><title>Заголовок &amp; тема</title><style>invisible</style></head>
    <body><h1>Привет 👋</h1><script>secret script</script><p>Текст <b>страницы</b>.</p>
    <div hidden><br></br>невидимый текст</div><template>template text</template></body></html>"""
    client = build_client(
        read=lambda request: page(
            html,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Last-Modified": "Mon, 16 Nov 2026 08:00:00 GMT",
                "ETag": '"v3"',
            },
        )
    )
    full = await client.read(WebReadRequest(url=PAGE_URL + "#part"))
    assert full.text == "Привет 👋\nТекст страницы."
    assert full.title == "Заголовок & тема"
    assert full.coverage == "extracted_text" and full.total_chars == len(full.text)
    assert full.fetched_at.tzinfo == UTC and full.media_type == "text/html"
    assert full.content_hash == hashlib.sha256(full.text.encode()).hexdigest()
    assert full.downloaded_bytes == len(html.encode())
    assert full.version_hint == '"v3"' and full.reported_date == "Mon, 16 Nov 2026 08:00:00 GMT"
    short = await client.read(WebReadRequest(url=PAGE_URL, max_chars=8))
    assert short.text == "Привет 👋" and short.coverage == "truncated_text"
    assert short.content_hash == full.content_hash and short.source_id == full.source_id
    changed = await build_client(read=lambda request: page("Другая версия")).read(
        WebReadRequest(url=PAGE_URL)
    )
    assert changed.source_id == full.source_id and changed.content_hash != full.content_hash

    idna_calls = []
    resolver = Resolver("2606:4700:4700::1111")
    await build_client(
        read=lambda request: idna_calls.append(request) or page(), resolver=resolver
    ).read(WebReadRequest(url="https://пример.рф/page"))
    assert resolver.calls == ["xn--e1afmkfd.xn--p1ai"]
    assert idna_calls[0].url.host == "2606:4700:4700::1111"
    assert idna_calls[0].headers["Host"] == "xn--e1afmkfd.xn--p1ai"
    assert idna_calls[0].extensions["sni_hostname"] == "xn--e1afmkfd.xn--p1ai"


async def test_unsupported_body_is_error_and_compressed_body_is_never_consumed() -> None:
    for content, headers in (
        ("PDF", {"Content-Type": "application/pdf"}),
        ("", {"Content-Type": "text/plain"}),
        ("<script>render()</script>", {"Content-Type": "text/html"}),
        ("<![bad]>", {"Content-Type": "text/html"}),
        ("a\x00b", {"Content-Type": "text/plain"}),
        ("text", {"Content-Type": "text/plain; charset=unknown-codec"}),
    ):
        with pytest.raises(WebUnsupportedContentError):
            await build_client(
                read=lambda request, content=content, headers=headers: page(
                    content, headers=headers
                )
            ).read(WebReadRequest(url=PAGE_URL))
    for encoding in ("gzip", "br", "deflate", "identity,gzip"):
        stream = BodyStream([b"potential-compressed-bomb"])
        with pytest.raises(WebUnsupportedContentError):
            await build_client(
                read=lambda request, encoding=encoding, stream=stream: httpx.Response(
                    200,
                    headers={"Content-Type": "text/plain", "Content-Encoding": encoding},
                    stream=stream,
                )
            ).read(WebReadRequest(url=PAGE_URL))
        assert stream.closed and stream.reads == 0


async def test_raw_body_cap_applies_to_declared_and_actual_size_and_closes_stream() -> None:
    for declared in (None, "2", "100"):
        stream = BodyStream([b"abcdef", b"abcdef"])
        headers = {"Content-Type": "text/plain"}
        if declared:
            headers["Content-Length"] = declared
        with pytest.raises(WebContentTooLargeError):
            await build_client(
                read=lambda request, headers=headers, stream=stream: httpx.Response(
                    200, headers=headers, stream=stream
                ),
                max_response_bytes=10,
            ).read(WebReadRequest(url=PAGE_URL))
        assert stream.closed
        if declared == "100":
            assert stream.reads == 0
    for declared in ("-1", "not-an-integer"):
        with pytest.raises(WebResponseError):
            await build_client(
                read=lambda request, declared=declared: page(headers={"Content-Length": declared})
            ).read(WebReadRequest(url=PAGE_URL))
    with pytest.raises(WebContentTooLargeError):
        await build_client(read=lambda request: page("abcdef"), max_response_bytes=5).read(
            WebReadRequest(url=PAGE_URL)
        )


async def test_deadline_covers_dns_headers_and_body_without_retries() -> None:
    blocker = asyncio.Event()

    async def slow_resolve(hostname):
        await blocker.wait()
        return [PUBLIC_IP]

    resolver = Resolver()
    resolver.resolve = slow_resolve
    with pytest.raises(WebContextTimeoutError):
        await asyncio.wait_for(
            build_client(resolver=resolver, deadline_seconds=0.02).read(
                WebReadRequest(url=PAGE_URL)
            ),
            timeout=2,
        )

    calls = []

    async def slow_headers(request):
        calls.append(request)
        await blocker.wait()
        return page()

    with pytest.raises(WebContextTimeoutError):
        await asyncio.wait_for(
            build_client(read=slow_headers, deadline_seconds=0.02).read(
                WebReadRequest(url=PAGE_URL)
            ),
            timeout=2,
        )
    assert len(calls) == 1
    stream = BodyStream([b"body"], blocker=blocker)
    with pytest.raises(WebContextTimeoutError):
        await asyncio.wait_for(
            build_client(
                read=lambda request: httpx.Response(
                    200, headers={"Content-Type": "text/plain"}, stream=stream
                ),
                deadline_seconds=0.02,
            ).read(WebReadRequest(url=PAGE_URL)),
            timeout=2,
        )
    assert stream.closed

    # Реальный таймер остаётся действующим на DNS после HTTP-перехода.
    cumulative_calls = []
    dns_calls = []

    async def delayed_resolve(hostname):
        dns_calls.append(hostname)
        if len(dns_calls) > 1:
            await blocker.wait()
        return [PUBLIC_IP]

    cumulative_resolver = Resolver()
    cumulative_resolver.resolve = delayed_resolve
    with pytest.raises(WebContextTimeoutError):
        await asyncio.wait_for(
            build_client(
                resolver=cumulative_resolver,
                read=lambda request: cumulative_calls.append(request)
                or (
                    httpx.Response(302, headers={"Location": "/next"})
                    if len(cumulative_calls) == 1
                    else page()
                ),
                deadline_seconds=0.02,
            ).read(WebReadRequest(url=PAGE_URL)),
            timeout=2,
        )
    assert len(cumulative_calls) == 1
    assert len(dns_calls) == 2

    for mode in ("read", "search"):

        def timeout(request):
            raise httpx.ReadTimeout("sensitive-request-data", request=request)

        client = build_client(search=timeout, read=timeout)
        with pytest.raises(WebContextTimeoutError) as caught:
            if mode == "read":
                await client.read(WebReadRequest(url=PAGE_URL))
            else:
                await client.search(WebSearchRequest(query="public"), allow_external_query=True)
        assert "sensitive-request-data" not in str(caught.value)


async def test_search_and_site_protocol_failures_are_sanitized_and_not_empty_success() -> None:
    calls = []
    for status in (401, 429, 500, 503):

        def fail(request, status=status):
            calls.append(request)
            return httpx.Response(status)

        with pytest.raises(WebResponseError):
            await build_client(search=fail).search(
                WebSearchRequest(query="public"), allow_external_query=True
            )
        with pytest.raises(WebResponseError):
            await build_client(read=fail).read(WebReadRequest(url=PAGE_URL))
    assert len(calls) == 8
    for data in (
        {},
        [],
        {"type": "error"},
        {"type": "search", "web": []},
        {"type": "search", "web": {"results": [{}]}},
        {
            "type": "search",
            "web": {"results": [{"url": PAGE_URL, "title": "x", "description": []}]},
        },
    ):
        with pytest.raises(WebResponseError):
            await build_client(
                search=lambda request, data=data: httpx.Response(200, json=data)
            ).search(WebSearchRequest(query="public"), allow_external_query=True)
    with pytest.raises(WebResponseError):
        await build_client(
            search=lambda request: httpx.Response(
                302, headers={"Location": "https://other.example.com/"}
            )
        ).search(WebSearchRequest(query="public"), allow_external_query=True)
    with pytest.raises(WebResponseError):
        await build_client(
            search=lambda request: httpx.Response(
                200, content=b"bad-json", headers={"Content-Type": "application/json"}
            )
        ).search(WebSearchRequest(query="public"), allow_external_query=True)

    def sensitive_error(request):
        raise httpx.ConnectError(
            "Do not expose test-brave-secret, full-query or private path", request=request
        )

    with pytest.raises(WebResponseError) as caught:
        await build_client(search=sensitive_error).search(
            WebSearchRequest(query="public"), allow_external_query=True
        )
    assert "test-brave-secret" not in str(caught.value) and "full-query" not in str(caught.value)
    assert caught.value.__suppress_context__
