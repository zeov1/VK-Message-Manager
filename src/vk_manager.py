"""High-level operations for VK messages and conversations."""

from __future__ import annotations

from typing import Any

from vk_client import VKClient

CHAT_PEER_OFFSET = 2_000_000_000
MAX_HISTORY_BATCH = 100


class VKMessageManager:
    """Domain layer on top of :class:`VKClient`."""

    def __init__(self, client: VKClient) -> None:
        """Store the API client.

        Args:
            client: Configured :class:`VKClient` instance.
        """
        self._client = client

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _peer_name(
        peer_id: int,
        conversation: dict[str, Any],
        profiles: dict[int, dict[str, Any]],
        groups: dict[int, dict[str, Any]],
    ) -> str:
        """Resolve a human-readable name for a conversation peer."""
        if 0 < peer_id < CHAT_PEER_OFFSET:
            user = profiles.get(peer_id)
            if user:
                return (
                    f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                )
        elif peer_id < 0:
            group = groups.get(-peer_id)
            if group:
                return group.get("name", str(peer_id))
        else:
            title = conversation.get("chat_settings", {}).get("title")
            if title:
                return title
        return str(peer_id)

    @staticmethod
    def _index_profiles(
        response: dict[str, Any],
    ) -> tuple[dict[int, dict], dict[int, dict]]:
        """Build id→object maps for profiles and groups."""
        profiles = {p["id"]: p for p in response.get("profiles", [])}
        groups = {g["id"]: g for g in response.get("groups", [])}
        return profiles, groups

    # ---------------------------------------------------------- conversations

    def get_conversations(self, count: int = 200) -> list[tuple[int, str]]:
        """Return ``(peer_id, name)`` pairs for the user's conversations.

        Args:
            count: Maximum number of conversations to fetch.

        Returns:
            A list of ``(peer_id, display_name)`` tuples.
        """
        response = self._client.call(
            "messages.getConversations", count=count, extended=1
        )
        profiles, groups = self._index_profiles(response)
        result: list[tuple[int, str]] = []
        for item in response.get("items", []):
            conv = item["conversation"]
            peer_id = conv["peer"]["id"]
            result.append((peer_id, self._peer_name(peer_id, conv, profiles, groups)))
        return result

    def get_conversation_name(self, peer_id: int) -> str:
        """Return the display name of a single conversation.

        Args:
            peer_id: Conversation peer identifier.

        Returns:
            The conversation's human-readable name.
        """
        response = self._client.call(
            "messages.getConversationsById", peer_ids=str(peer_id), extended=1
        )
        profiles, groups = self._index_profiles(response)
        for item in response.get("items", []):
            if item.get("peer", {}).get("id") == peer_id:
                return self._peer_name(peer_id, item, profiles, groups)
        return str(peer_id)

    # --------------------------------------------------------------- messages

    def get_last_messages(self, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch the last ``limit`` messages across all conversations.

        Args:
            limit: Number of recent messages to retrieve.

        Returns:
            A list of dicts with keys ``id``, ``peer_id``, ``conv_name``, ``text``.
        """
        response = self._client.call(
            "messages.getConversations", count=limit, extended=1
        )
        profiles, groups = self._index_profiles(response)
        result: list[dict[str, Any]] = []
        for item in response.get("items", []):
            conv = item["conversation"]
            last = item.get("last_message", {})
            peer_id = conv["peer"]["id"]
            result.append(
                {
                    "id": last.get("id"),
                    "peer_id": peer_id,
                    "conv_name": self._peer_name(peer_id, conv, profiles, groups),
                    "text": last.get("text", ""),
                }
            )
        return result

    def get_conversation_messages(
        self, peer_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return the last ``limit`` messages from a conversation.

        Args:
            peer_id: Conversation peer identifier.
            limit: Maximum number of messages to return.

        Returns:
            A list of dicts with keys ``id`` and ``text``.
        """
        response = self._client.call(
            "messages.getHistory", peer_id=peer_id, count=limit
        )
        return [
            {"id": m["id"], "text": m.get("text", "")}
            for m in response.get("items", [])
        ]

    # ----------------------------------------------------------------- delete

    def delete_message(self, message_id: int) -> None:
        """Delete a single message by its global ID.

        Args:
            message_id: Message identifier.

        Raises:
            ValueError: If the message could not be found.
        """
        response = self._client.call("messages.getById", message_ids=str(message_id))
        items = response.get("items", [])
        if not items:
            raise ValueError(f"Message {message_id} not found")
        peer_id = items[0]["peer_id"]
        self._delete_messages(peer_id, [message_id])

    def delete_last_message(self, peer_id: int) -> None:
        """Delete the most recent message in a conversation.

        Args:
            peer_id: Conversation peer identifier.

        Raises:
            ValueError: If the conversation has no messages.
        """
        messages = self.get_conversation_messages(peer_id, limit=1)
        if not messages:
            raise ValueError("Conversation has no messages")
        self._delete_messages(peer_id, [messages[0]["id"]])

    def delete_all_messages(self, peer_id: int) -> int:
        """Delete every message in a conversation.

        Args:
            peer_id: Conversation peer identifier.

        Returns:
            Number of messages that were deleted.
        """
        deleted = 0
        while True:
            messages = self.get_conversation_messages(peer_id, limit=MAX_HISTORY_BATCH)
            ids = [m["id"] for m in messages]
            if not ids:
                break
            self._delete_messages(peer_id, ids)
            deleted += len(ids)
            if len(ids) < MAX_HISTORY_BATCH:
                break
        return deleted

    def _delete_messages(
        self, peer_id: int, message_ids: list[int], for_all: bool = True
    ) -> None:
        """Delete a batch of messages in a single request."""
        if not message_ids:
            return
        self._client.call(
            "messages.delete",
            peer_id=peer_id,
            message_ids=",".join(str(i) for i in message_ids),
            delete_for_all=1 if for_all else 0,
        )
