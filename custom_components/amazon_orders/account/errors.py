"""Error types shared by the Amazon account modules."""
from __future__ import annotations


class AmazonApiError(Exception):
    """Raised when an Amazon request fails for a non-auth reason."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Amazon request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class AmazonAuthError(AmazonApiError):
    """Raised when Amazon no longer accepts the stored sign-in.

    Distinct from :class:`AmazonApiError` on purpose: only this one may
    trigger Home Assistant's reauth flow, so an outage never pushes a user
    into a sign-in they cannot complete.
    """
