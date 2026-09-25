"""Thin transport layer for the VK API."""

from __future__ import annotations

import time
from typing import Any

import requests

from config import REQUEST_DELAY_SECONDS, VK_ACCESS_TOKEN, VK_API_VERSION

API_BASE_URL = "https://api.vk.com/method"


class VKAPIError(RuntimeError):
    """Raised when the VK API responds with an error object."""


class VKClient:
    """Minimal VK API client that enforces a delay between requests."""

    def __init__(
        self,
        token: str = VK_ACCESS_TOKEN,
        api_version: str = VK_API_VERSION,
        delay: float = REQUEST_DELAY_SECONDS,
    ) -> None:
        """Initialize the client.

        Args:
            token: VK access token.
            api_version: VK API version string.
            delay: Seconds to sleep before every request.
        """
        self._token = token
        self._api_version = api_version
        self._delay = delay

    def call(self, method: str, **params: Any) -> Any:
        """Call a VK API method.

        Args:
            method: Method name, e.g. ``"messages.getHistory"``.
            **params: Method parameters.

        Returns:
            The ``response`` field of the API reply.

        Raises:
            VKAPIError: If the API returns an error object.
            requests.HTTPError: If the HTTP request fails.
        """
        time.sleep(self._delay)
        payload = {**params, "access_token": self._token, "v": self._api_version}
        response = requests.post(f"{API_BASE_URL}/{method}", data=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            error = data["error"]
            raise VKAPIError(f"[{error.get('error_code')}] {error.get('error_msg')}")
        return data["response"]
