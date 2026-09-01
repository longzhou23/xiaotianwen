"""HTTP-level core-flow tests for the loopback Local Test Console."""

from __future__ import annotations

import json
import re
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from tests.ui.server.app import ConsoleRequestError, LocalTestConsole, create_console_server


def _request(opener: object, url: str, *, method: str = "GET", payload: object | None = None, csrf: str | None = None) -> tuple[int, str]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Origin"] = url.split("/api/", 1)[0]
        if csrf:
            headers["X-XTW-CSRF"] = csrf
    request = Request(url, data=data, headers=headers, method=method)
    with opener.open(request, timeout=3) as response:  # type: ignore[attr-defined]
        return response.status, response.read().decode("utf-8")


@pytest.fixture()
def console(tmp_path: Path):
    server, state = create_console_server(tmp_path, host="127.0.0.1", port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def test_console_is_loopback_only_and_serves_local_assets(console: tuple[str, object]) -> None:
    base, _ = console
    cookies = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))
    status, html = _request(opener, f"{base}/")
    assert status == 200
    assert "小天文本地测试台" in html
    assert "/static/app.js" in html
    assert "https://" not in html and "http://" not in html
    _, script = _request(opener, f"{base}/static/app.js")
    assert "innerHTML" not in script
    with pytest.raises(ConsoleRequestError):
        create_console_server(Path.cwd(), host="0.0.0.0", port=0)


def test_console_replays_input_and_exposes_correlated_views(console: tuple[str, object]) -> None:
    base, _ = console
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    _, html = _request(opener, f"{base}/")
    csrf = re.search(r'<meta name="xtw-csrf" content="([^"]+)">', html).group(1)  # type: ignore[union-attr]
    _, created_raw = _request(opener, f"{base}/api/runs", method="POST", payload={}, csrf=csrf)
    created = json.loads(created_raw)["run"]
    run_id = created["run_id"]
    assert created["sandbox"] == "artifacts/test-runs/<run-id>"
    payload = {
        "route": "group_passive",
        "provider": "tool",
        "template": "plain",
        "messages": [{"text": "<script>synthetic only</script>", "at_ms": 0}, {"text": "第二条合成消息", "at_ms": 1000}],
        "images": [{"id": "synthetic-ui-image-test", "mime": "image/jpeg"}],
    }
    _, submitted_raw = _request(opener, f"{base}/api/runs/{run_id}/inputs", method="POST", payload=payload, csrf=csrf)
    submitted = json.loads(submitted_raw)
    assert submitted["capture_mode"] == "COMPLETE"
    assert submitted["astrbot_capture_mode"] == "NOT_CONNECTED"
    assert submitted["result"]["summary"]["main_reply_requests"] == 1
    for endpoint in ("timeline", "logs", "outputs", "compare"):
        _, body = _request(opener, f"{base}/api/runs/{run_id}/{endpoint}")
        assert json.loads(body) is not None
    _, requests_raw = _request(opener, f"{base}/api/runs/{run_id}/requests")
    requests = json.loads(requests_raw)
    assert requests[0]["request_id"].startswith("turn-")
    assert requests[1]["parent_request_id"] == requests[0]["request_id"]
    assert isinstance(requests[0]["response"]["usage"]["input_tokens"], int)
    _, stream = _request(opener, f"{base}/api/runs/{run_id}/stream")
    assert "event: trace" in stream and "event: complete" in stream


def test_console_assigns_distinct_run_ids_for_rapid_local_creates(console: tuple[str, object]) -> None:
    base, _ = console
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    _, html = _request(opener, f"{base}/")
    csrf = re.search(r'<meta name="xtw-csrf" content="([^"]+)">', html).group(1)  # type: ignore[union-attr]

    _, first_raw = _request(opener, f"{base}/api/runs", method="POST", payload={}, csrf=csrf)
    _, second_raw = _request(opener, f"{base}/api/runs", method="POST", payload={}, csrf=csrf)

    assert json.loads(first_raw)["run"]["run_id"] != json.loads(second_raw)["run"]["run_id"]


def test_console_rejects_missing_csrf(console: tuple[str, object]) -> None:
    base, _ = console
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    _request(opener, f"{base}/")
    request = Request(
        f"{base}/api/runs",
        data=b"{}",
        headers={"Content-Type": "application/json", "Origin": base},
        method="POST",
    )
    with pytest.raises(HTTPError) as caught:
        opener.open(request, timeout=3)
    assert caught.value.code == 403


def test_live_error_action_is_not_reported_as_pass() -> None:
    output = {
        "payload": {
            "message": [
                {"type": "text", "data": {"text": "LLM 响应错误: All chat models failed: synthetic"}},
            ]
        }
    }
    assert LocalTestConsole._live_output_error(output) is not None
    assert LocalTestConsole._live_output_error(
        {"payload": {"message": [{"type": "text", "data": {"text": "正常测试回复"}}]}}
    ) is None
