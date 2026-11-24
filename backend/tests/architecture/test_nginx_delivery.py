"""Real two-proxy delivery: compression, validators, streams and file bytes."""

import gzip
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import docker
import pytest
from docker.errors import DockerException


def test_nginx_delivery_through_both_proxies(tmp_path):
    try:
        client = docker.from_env()
        client.ping()
    except DockerException as error:
        if os.getenv("CI"):
            raise
        pytest.skip(f"Docker unavailable: {error}")
    root = Path(__file__).resolve().parents[3]
    html = tmp_path / "html"
    (html / "assets").mkdir(parents=True)
    js = b"const task = 'TELOS';\n" * 2000
    (html / "assets/app-abcdefgh.js").write_bytes(js)
    (html / "index.html").write_text("<!doctype html>" + "<!-- TELOS -->" * 200)
    backend = tmp_path / "upstream.py"
    backend.write_text("""
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith('/ws'):
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.end_headers()
            return
        stream = self.path in ('/api/events', '/mcp/')
        body = b'data: first\\n\\n' if stream else b'{"task":"TELOS"}' * 2000
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if stream else 'application/json')
        if stream:
            self.send_header('X-Accel-Buffering', 'no')
        else:
            self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        if stream:
            time.sleep(1)
            self.wfile.write(b'data: second\\n\\n')
    def log_message(self, *args): pass
ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
""")
    network = client.networks.create(f"telos-delivery-{uuid4().hex[:12]}")
    containers = []

    def start(image, alias, volumes, command=None, ports=None):
        container = client.containers.run(
            image,
            command=command,
            detach=True,
            volumes=volumes,
            ports=ports,
            network=network.name,
            networking_config={
                network.name: client.api.create_endpoint_config(aliases=[alias]),
            },
        )
        containers.append(container)
        return container

    def mount(path, target):
        return {str(path.resolve()): {"bind": target, "mode": "ro"}}

    def base_url(container):
        container.reload()
        port = container.attrs["NetworkSettings"]["Ports"]["80/tcp"][0]["HostPort"]
        return f"http://127.0.0.1:{port}"

    def request(base, path, **headers):
        return urlopen(Request(base + path, headers=headers), timeout=5)

    try:
        start(
            "python:3.12.12-slim-bookworm",
            "backend",
            mount(backend, "/upstream.py"),
            ["python", "/upstream.py"],
        )
        frontend = start(
            "nginx:1.28-alpine",
            "frontend",
            {
                **mount(root / "frontend/nginx.conf", "/etc/nginx/conf.d/default.conf"),
                **mount(html, "/usr/share/nginx/html"),
            },
            ports={"80/tcp": ("127.0.0.1", None)},
        )
        gateway = start(
            "nginx:1.28-alpine",
            "gateway",
            mount(root / "nginx/nginx.conf", "/etc/nginx/conf.d/default.conf"),
            ports={"80/tcp": ("127.0.0.1", None)},
        )
        base = base_url(gateway)
        for _ in range(40):
            try:
                with request(base, "/") as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("nginx did not start: " + "\n".join(c.logs().decode() for c in containers))

        for target in (base, base_url(frontend)):
            with request(
                target, "/assets/app-abcdefgh.js", **{"Accept-Encoding": "gzip"}
            ) as response:
                assert response.headers["Content-Encoding"] == "gzip"
                assert "Accept-Encoding" in response.headers["Vary"]
                assert "immutable" in response.headers["Cache-Control"]
                assert gzip.decompress(response.read()) == js  # Exactly one encoding.
            with request(
                target, "/assets/app-abcdefgh.js", **{"Accept-Encoding": "identity"}
            ) as response:
                assert response.headers.get("Content-Encoding") is None
                assert response.read() == js
            with request(target, "/projects/TELOS/tasks") as response:
                assert response.headers["Cache-Control"] == "no-cache"
                etag = response.headers["ETag"]
            # urllib exposes a valid 304 as HTTPError.
            with pytest.raises(HTTPError) as unchanged:
                request(target, "/projects/TELOS/tasks", **{"If-None-Match": etag})
            assert unchanged.value.code == 304
            with pytest.raises(HTTPError) as missing:
                request(target, "/assets/missing-abcdefgh.js")
            assert missing.value.code == 404

        with request(base, "/api/data", **{"Accept-Encoding": "gzip"}) as response:
            assert response.headers["Content-Encoding"] == "gzip"
            assert "Accept-Encoding" in response.headers["Vary"]
            data = response.read()
            assert len(data) < 1000
            assert gzip.decompress(data) == b'{"task":"TELOS"}' * 2000
        for path in ("/api/events", "/mcp"):
            started = time.monotonic()
            with request(base, path, **{"Accept-Encoding": "gzip"}) as response:
                assert response.headers.get("Content-Encoding") is None
                assert response.readline() == b"data: first\n"
                assert time.monotonic() - started < 0.8
                assert b"data: second" in response.read()
        with request(
            base, "/api/v1/documents/d1_test/content", **{"Accept-Encoding": "gzip"}
        ) as response:
            assert response.headers.get("Content-Encoding") is None
            assert response.read() == b'{"task":"TELOS"}' * 2000
        with pytest.raises(HTTPError) as upgraded:
            request(base, "/api/v1/projects/2/chat/ws", Upgrade="websocket", Connection="Upgrade")
        assert upgraded.value.code == 101
        assert upgraded.value.headers["Upgrade"] == "websocket"
    finally:
        for container in reversed(containers):
            container.remove(force=True)
        network.remove()
        client.close()
