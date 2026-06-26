"""Slack webhook notifier for approval gates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlackWebhookConfig:
    """Configuration for the Slack webhook notifier.

    Attributes:
        webhook_url: The incoming-webhook URL supplied by Slack.
            When *None* or empty the notifier is considered unconfigured
            and :meth:`SlackWebhookNotifier.notify_approval_raised` will
            return ``False`` without attempting any network call.
        timeout: HTTP request timeout in seconds (default 10).
    """

    webhook_url: str | None = field(default=None)
    timeout: float = field(default=10.0)

    def __post_init__(self) -> None:
        if self.timeout <= 0:
            raise ValueError("timeout must be a positive number")


@dataclass
class ApprovalRequest:
    """Minimal approval-request value object used by the notifier.

    Attributes:
        approval_id: Unique identifier for the approval request.
        title: Human-readable title describing what needs approval.
        risk_classification: Risk level string, e.g. ``"HIGH"``.
    """

    approval_id: str
    title: str
    risk_classification: str


class SlackWebhookNotifier:
    """Sends :class:`ApprovalRequest` notifications to a Slack channel.

    Parameters
    ----------
    config:
        A :class:`SlackWebhookConfig` instance.  When *webhook_url* is
        ``None`` or empty all notification attempts return ``False``.
    transport:
        An optional :class:`httpx.BaseTransport` used instead of the
        default network transport.  Pass a mock transport in tests to
        avoid real HTTP calls.
    """

    def __init__(
        self,
        config: SlackWebhookConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_approval_raised(self, request: ApprovalRequest) -> bool:
        """Post an approval-raised notification to Slack.

        Parameters
        ----------
        request:
            The approval request to announce.

        Returns
        -------
        bool
            ``True`` when Slack responded with a 2xx status code,
            ``False`` in every other situation (unconfigured, non-2xx
            response, network error, …).  This method **never** raises.
        """
        if not self._config.webhook_url:
            logger.debug("SlackWebhookNotifier is unconfigured – skipping")
            return False

        payload = self._build_payload(request)

        try:
            with self._build_client() as client:
                response = client.post(
                    self._config.webhook_url,
                    json=payload,
                    timeout=self._config.timeout,
                )
            if response.is_success:
                logger.info(
                    "Slack notification sent for approval %s",
                    request.approval_id,
                )
                return True

            logger.warning(
                "Slack webhook returned non-2xx status %s for approval %s",
                response.status_code,
                request.approval_id,
            )
            return False

        except httpx.HTTPError as exc:
            logger.error(
                "Network error notifying Slack for approval %s: %s",
                request.approval_id,
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unexpected error notifying Slack for approval %s: %s",
                request.approval_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(request: ApprovalRequest) -> dict[str, str]:
        """Build the JSON payload sent to Slack."""
        text = (
            f"Approval required\n"
            f"*ID:* {request.approval_id}\n"
            f"*Title:* {request.title}\n"
            f"*Risk:* {request.risk_classification}"
        )
        return {"text": text}

    def _build_client(self) -> httpx.Client:
        """Return an :class:`httpx.Client`, injecting the test transport when set."""
        if self._transport is not None:
            return httpx.Client(transport=self._transport)
        return httpx.Client()
