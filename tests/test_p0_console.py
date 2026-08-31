from __future__ import annotations

import json
import re
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from tests.harness.console_server import LocalConsole
from tests.harness.redact import REDACTED_SECRET


def _open_console_page(console: LocalConsole):
    cookies = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))
    html = opener.open(console.url, timeout=3).read().decode("utf-8")
    csrf = re.search(r'name="xtw-csrf" content="([^"]+)"', html)
    assert csrf is not None
    return opener, html, csrf.group(1)


def _json_request(opener, url: str, *, method: str = "GET", body: dict | None = None, csrf: str | None = None):
    headers = {}
    encoded = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Origin"] = url.rsplit("/api/", 1)[0]
        headers["X-Test-Console-CSRF"] = csrf or ""
        encoded = json.dumps(body).encode("utf-8")
    request = Request(url, data=encoded, headers=headers, method=method)
    return json.loads(opener.open(request, timeout=3).read().decode("utf-8"))


def test_console_is_loopback_only_and_exposes_input_requests_logs_and_output() -> None:
    with LocalConsole() as console:
        assert console.url.startswith("http://127.0.0.1:")
        opener, html, csrf = _open_console_page(console)
        assert "Input Composer" in html
        assert "Request Explorer" in html
        assert "AstrBot / Harness Logs" in html
        assert "Output Inspector" in html

        run = _json_request(
            opener,
            console.url + "api/runs",
            method="POST",
            csrf=csrf,
            body={"text": "本地 UI 合成输入", "route": "chat", "stream": True},
        )

        assert run["passed"] is True
        assert run["inputs"][0]["text"] == "本地 UI 合成输入"
        assert run["requests"][0]["role"] == "main_reply"
        assert run["logs"][0]["source"] == "HARNESS"
        assert any(output["stage"] == "delivery" for output in run["outputs"])
        assert any(event["kind"] == "request.chunk" for event in run["trace"])

        listed = _json_request(opener, console.url + "api/runs")
        assert listed["capture_mode"] == "COMPLETE"
        assert listed["runs"][0]["run_id"] == run["run_id"]
        stream = opener.open(console.url + f"api/runs/{run['run_id']}/stream", timeout=3).read().decode("utf-8")
        assert "event: trace" in stream
        assert "event: complete" in stream


def test_console_redacts_secret_shaped_input_and_rejects_cross_origin_post() -> None:
    with LocalConsole() as console:
        opener, _, csrf = _open_console_page(console)
        run = _json_request(
            opener,
            console.url + "api/runs",
            method="POST",
            csrf=csrf,
            body={"text": "Authorization: token-must-be-redacted", "route": "chat"},
        )
        rendered = json.dumps(run, ensure_ascii=False)
        assert REDACTED_SECRET in rendered
        assert "token-must-be-redacted" not in rendered

        request = Request(
            console.url + "api/runs",
            data=b'{"text":"x","route":"chat"}',
            headers={"Content-Type": "application/json", "Origin": "http://evil.invalid", "X-Test-Console-CSRF": csrf},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            opener.open(request, timeout=3)
        assert error.value.code == 403


def test_console_refuses_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="loopback"):
        LocalConsole(host="0.0.0.0")
