"""Канонические потоковые маршруты должны попадать в нужные location шлюза."""

import re
from pathlib import Path


def test_nginx_preserves_websocket_upgrade_and_unbuffered_document_uploads():
    config = (Path(__file__).resolve().parents[3] / "nginx/nginx.conf").read_text()
    locations = re.findall(r"location\s+~\s+(\S+)\s*\{([^{}]*)\}", config, re.DOTALL)
    cases = [
        (
            "/api/v1/projects/42/chat/ws",
            [
                r"proxy_set_header\s+Upgrade\s+\$http_upgrade\s*;",
                r"proxy_set_header\s+Connection\s+\$chat_connection_upgrade\s*;",
            ],
        ),
        (
            "/api/v1/users/me/task-attention/ws",
            [
                r"proxy_set_header\s+Upgrade\s+\$http_upgrade\s*;",
                r"proxy_set_header\s+Connection\s+\$chat_connection_upgrade\s*;",
            ],
        ),
        (
            "/api/v1/documents/upload",
            [r"proxy_request_buffering\s+off\s*;", r"proxy_buffering\s+off\s*;"],
        ),
        ("/api/v1/documents/d1_YXR0YWNobWVudDo4/content", [r"proxy_buffering\s+off\s*;"]),
        ("/api/v1/documents/d1_YXR0YWNobWVudDo4/preview", [r"proxy_buffering\s+off\s*;"]),
        (
            "/api/v1/tasks/42/comments/with-files",
            [
                r"proxy_request_buffering\s+off\s*;",
                r"proxy_buffering\s+off\s*;",
                r"client_max_body_size\s+101m\s*;",
            ],
        ),
    ]
    problems = []
    for path, directives in cases:
        matches = [body for pattern, body in locations if re.search(pattern, path)]
        if len(matches) != 1:
            problems.append(f"{path}: ожидается один потоковый location, найдено {len(matches)}")
            continue
        for directive in [r"proxy_http_version\s+1\.1\s*;", *directives]:
            if not re.search(directive, matches[0]):
                problems.append(f"{path}: не настроена директива {directive}")
    assert not problems, "\n".join(problems)
    assert not any(
        re.search(pattern, "/api/v1/tasks/42/attachments/8/content") for pattern, _ in locations
    )
