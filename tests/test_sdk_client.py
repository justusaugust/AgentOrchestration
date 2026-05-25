import json
from urllib.error import HTTPError
from unittest.mock import patch

from src.sdk.client import OrchestratorClient


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def http_error(code, reason):
    return HTTPError("https://example.test", code, reason, {}, None)


def test_get_transient_error_is_not_retried_by_default():
    client = OrchestratorClient(base_url="https://example.test")
    error = http_error(503, "Service Unavailable")

    with patch("src.sdk.client.urlopen", side_effect=error) as urlopen:
        result = client._request("GET", "/agents")

    assert result == {"error": 503, "message": "Service Unavailable"}
    assert urlopen.call_count == 1


def test_get_transient_error_retries_when_configured_and_returns_success():
    client = OrchestratorClient(
        base_url="https://example.test",
        retry_count=1,
        retry_backoff=0,
        retry_sleep=lambda delay: None,
    )

    with patch(
        "src.sdk.client.urlopen",
        side_effect=[
            http_error(502, "Bad Gateway"),
            Response({"agents": []}),
        ],
    ) as urlopen:
        result = client._request("GET", "/agents")

    assert result == {"agents": []}
    assert urlopen.call_count == 2


def test_request_level_retry_override_can_opt_in_single_get():
    client = OrchestratorClient(
        base_url="https://example.test",
        retry_count=0,
        retry_backoff=0,
        retry_sleep=lambda delay: None,
    )

    with patch(
        "src.sdk.client.urlopen",
        side_effect=[
            http_error(502, "Bad Gateway"),
            Response({"agents": []}),
        ],
    ) as urlopen:
        result = client._request("GET", "/agents", retry_count=1)

    assert result == {"agents": []}
    assert urlopen.call_count == 2


def test_get_transient_error_returns_last_error_after_retries_exhausted():
    client = OrchestratorClient(
        base_url="https://example.test",
        retry_count=2,
        retry_backoff=0,
        retry_sleep=lambda delay: None,
    )

    with patch(
        "src.sdk.client.urlopen",
        side_effect=[
            http_error(503, "Service Unavailable"),
            http_error(502, "Bad Gateway"),
            http_error(503, "Service Unavailable"),
        ],
    ) as urlopen:
        result = client._request("GET", "/agents")

    assert result == {"error": 503, "message": "Service Unavailable"}
    assert urlopen.call_count == 3


def test_retry_backoff_uses_injected_sleep_between_attempts():
    delays = []
    client = OrchestratorClient(
        base_url="https://example.test",
        retry_count=2,
        retry_backoff=0.25,
        retry_sleep=delays.append,
    )

    with patch(
        "src.sdk.client.urlopen",
        side_effect=[
            http_error(502, "Bad Gateway"),
            http_error(503, "Service Unavailable"),
            Response({"agents": []}),
        ],
    ):
        result = client._request("GET", "/agents")

    assert result == {"agents": []}
    assert delays == [0.25, 0.25]


def test_get_non_transient_error_is_not_retried_when_retry_is_configured():
    client = OrchestratorClient(
        base_url="https://example.test",
        retry_count=2,
        retry_backoff=0,
        retry_sleep=lambda delay: None,
    )
    error = http_error(500, "Internal Server Error")

    with patch("src.sdk.client.urlopen", side_effect=error) as urlopen:
        result = client._request("GET", "/agents")

    assert result == {"error": 500, "message": "Internal Server Error"}
    assert urlopen.call_count == 1


def test_non_get_transient_error_is_not_retried_when_retry_is_configured():
    client = OrchestratorClient(
        base_url="https://example.test",
        retry_count=2,
        retry_backoff=0,
        retry_sleep=lambda delay: None,
    )
    error = http_error(503, "Service Unavailable")

    with patch("src.sdk.client.urlopen", side_effect=error) as urlopen:
        result = client._request("POST", "/agents", {"name": "worker"})

    assert result == {"error": 503, "message": "Service Unavailable"}
    assert urlopen.call_count == 1
