"""Tests for the Slack webhook notifier (SDLC-1)."""

from __future__ import annotations

import json

import httpx
import pytest

from orchestrator.notify import SlackWebhookConfig as PublicSlackWebhookConfig
from orchestrator.notify import SlackWebhookNotifier as PublicSlackWebhookNotifier
from orchestrator.notify.slack import ApprovalRequest, SlackWebhookConfig, SlackWebhookNotifier

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SAMPLE_REQUEST = ApprovalRequest(
    approval_id="APR-001",
    title="Deploy to production",
    risk_classification="HIGH",
)


class MockTransport(httpx.BaseTransport):
    """Configurable in-process transport for testing."""

    def __init__(self, status_code: int = 200, response_text: str = "ok") -> None:
        self.status_code = status_code
        self.response_text = response_text
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, text=self.response_text)


class ErrorTransport(httpx.BaseTransport):
    """Transport that raises an httpx.HTTPError."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")


class UnexpectedErrorTransport(httpx.BaseTransport):
    """Transport that raises an unexpected non-httpx exception."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise RuntimeError("something exploded")


# ---------------------------------------------------------------------------
# SlackWebhookConfig tests
# ---------------------------------------------------------------------------


class TestSlackWebhookConfig:
    def test_default_values(self) -> None:
        cfg = SlackWebhookConfig()
        assert cfg.webhook_url is None
        assert cfg.timeout == 10.0

    def test_custom_values(self) -> None:
        cfg = SlackWebhookConfig(webhook_url="https://hooks.slack.com/x", timeout=5.0)
        assert cfg.webhook_url == "https://hooks.slack.com/x"
        assert cfg.timeout == 5.0

    def test_frozen(self) -> None:
        cfg = SlackWebhookConfig(webhook_url="https://hooks.slack.com/x")
        with pytest.raises((AttributeError, TypeError)):
            cfg.webhook_url = "other"  # type: ignore[misc]

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            SlackWebhookConfig(timeout=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            SlackWebhookConfig(timeout=-1.0)

    def test_positive_timeout_accepted(self) -> None:
        cfg = SlackWebhookConfig(timeout=0.1)
        assert cfg.timeout == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# ApprovalRequest tests
# ---------------------------------------------------------------------------


class TestApprovalRequest:
    def test_fields_stored(self) -> None:
        req = ApprovalRequest(
            approval_id="APR-42",
            title="Some title",
            risk_classification="MEDIUM",
        )
        assert req.approval_id == "APR-42"
        assert req.title == "Some title"
        assert req.risk_classification == "MEDIUM"


# ---------------------------------------------------------------------------
# SlackWebhookNotifier – unconfigured
# ---------------------------------------------------------------------------


class TestSlackWebhookNotifierUnconfigured:
    def test_returns_false_when_webhook_url_is_none(self) -> None:
        notifier = SlackWebhookNotifier(config=SlackWebhookConfig())
        result = notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert result is False

    def test_returns_false_when_webhook_url_is_empty_string(self) -> None:
        notifier = SlackWebhookNotifier(config=SlackWebhookConfig(webhook_url=""))
        result = notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert result is False

    def test_no_http_call_when_unconfigured(self) -> None:
        transport = MockTransport()
        notifier = SlackWebhookNotifier(config=SlackWebhookConfig(), transport=transport)
        notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert len(transport.requests) == 0


# ---------------------------------------------------------------------------
# SlackWebhookNotifier – successful notification
# ---------------------------------------------------------------------------


class TestSlackWebhookNotifierSuccess:
    def _make_notifier(self, status_code: int = 200) -> tuple[SlackWebhookNotifier, MockTransport]:
        transport = MockTransport(status_code=status_code)
        config = SlackWebhookConfig(webhook_url="https://hooks.slack.com/test", timeout=5.0)
        notifier = SlackWebhookNotifier(config=config, transport=transport)
        return notifier, transport

    def test_returns_true_on_200(self) -> None:
        notifier, _ = self._make_notifier(200)
        assert notifier.notify_approval_raised(SAMPLE_REQUEST) is True

    def test_returns_true_on_204(self) -> None:
        notifier, _ = self._make_notifier(204)
        assert notifier.notify_approval_raised(SAMPLE_REQUEST) is True

    def test_posts_to_correct_url(self) -> None:
        notifier, transport = self._make_notifier()
        notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert len(transport.requests) == 1
        assert str(transport.requests[0].url) == "https://hooks.slack.com/test"

    def test_uses_post_method(self) -> None:
        notifier, transport = self._make_notifier()
        notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert transport.requests[0].method == "POST"

    def test_payload_contains_approval_id(self) -> None:
        notifier, transport = self._make_notifier()
        notifier.notify_approval_raised(SAMPLE_REQUEST)
        body = json.loads(transport.requests[0].content)
        assert SAMPLE_REQUEST.approval_id in body["text"]

    def test_payload_contains_title(self) -> None:
        notifier, transport = self._make_notifier()
        notifier.notify_approval_raised(SAMPLE_REQUEST)
        body = json.loads(transport.requests[0].content)
        assert SAMPLE_REQUEST.title in body["text"]

    def test_payload_contains_risk_classification(self) -> None:
        notifier, transport = self._make_notifier()
        notifier.notify_approval_raised(SAMPLE_REQUEST)
        body = json.loads(transport.requests[0].content)
        assert SAMPLE_REQUEST.risk_classification in body["text"]

    def test_payload_has_text_key(self) -> None:
        notifier, transport = self._make_notifier()
        notifier.notify_approval_raised(SAMPLE_REQUEST)
        body = json.loads(transport.requests[0].content)
        assert "text" in body


# ---------------------------------------------------------------------------
# SlackWebhookNotifier – non-2xx responses
# ---------------------------------------------------------------------------


class TestSlackWebhookNotifierNon2xx:
    @pytest.mark.parametrize("status_code", [400, 403, 404, 500, 503])
    def test_returns_false_on_non_2xx(self, status_code: int) -> None:
        transport = MockTransport(status_code=status_code)
        config = SlackWebhookConfig(webhook_url="https://hooks.slack.com/test")
        notifier = SlackWebhookNotifier(config=config, transport=transport)
        result = notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert result is False

    def test_does_not_raise_on_non_2xx(self) -> None:
        transport = MockTransport(status_code=500)
        config = SlackWebhookConfig(webhook_url="https://hooks.slack.com/test")
        notifier = SlackWebhookNotifier(config=config, transport=transport)
        # Should not raise
        result = notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert result is False


# ---------------------------------------------------------------------------
# SlackWebhookNotifier – network / unexpected errors
# ---------------------------------------------------------------------------


class TestSlackWebhookNotifierErrors:
    def test_returns_false_on_network_error(self) -> None:
        config = SlackWebhookConfig(webhook_url="https://hooks.slack.com/test")
        notifier = SlackWebhookNotifier(config=config, transport=ErrorTransport())
        result = notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert result is False

    def test_does_not_raise_on_network_error(self) -> None:
        config = SlackWebhookConfig(webhook_url="https://hooks.slack.com/test")
        notifier = SlackWebhookNotifier(config=config, transport=ErrorTransport())
        # Should not raise
        notifier.notify_approval_raised(SAMPLE_REQUEST)

    def test_returns_false_on_unexpected_error(self) -> None:
        config = SlackWebhookConfig(webhook_url="https://hooks.slack.com/test")
        notifier = SlackWebhookNotifier(config=config, transport=UnexpectedErrorTransport())
        result = notifier.notify_approval_raised(SAMPLE_REQUEST)
        assert result is False

    def test_does_not_raise_on_unexpected_error(self) -> None:
        config = SlackWebhookConfig(webhook_url="https://hooks.slack.com/test")
        notifier = SlackWebhookNotifier(config=config, transport=UnexpectedErrorTransport())
        notifier.notify_approval_raised(SAMPLE_REQUEST)


# ---------------------------------------------------------------------------
# Public package API
# ---------------------------------------------------------------------------


class TestPublicPackageExports:
    def test_slack_webhook_config_exported(self) -> None:
        assert PublicSlackWebhookConfig is SlackWebhookConfig

    def test_slack_webhook_notifier_exported(self) -> None:
        assert PublicSlackWebhookNotifier is SlackWebhookNotifier


# ---------------------------------------------------------------------------
# SlackWebhookNotifier._build_payload (unit)
# ---------------------------------------------------------------------------


class TestBuildPayload:
    def test_returns_dict_with_text_key(self) -> None:
        payload = SlackWebhookNotifier._build_payload(SAMPLE_REQUEST)
        assert isinstance(payload, dict)
        assert "text" in payload

    def test_text_contains_all_fields(self) -> None:
        payload = SlackWebhookNotifier._build_payload(SAMPLE_REQUEST)
        assert SAMPLE_REQUEST.approval_id in payload["text"]
        assert SAMPLE_REQUEST.title in payload["text"]
        assert SAMPLE_REQUEST.risk_classification in payload["text"]

    def test_different_requests_produce_different_payloads(self) -> None:
        req1 = ApprovalRequest("ID-1", "Title One", "LOW")
        req2 = ApprovalRequest("ID-2", "Title Two", "HIGH")
        assert SlackWebhookNotifier._build_payload(req1) != SlackWebhookNotifier._build_payload(req2)
