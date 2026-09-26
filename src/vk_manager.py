"""High-level operations for VK messages and conversations.

All destructive operations in this module are restricted to messages
authored by the current user (the owner of the access token).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vk_client import VKClient

CHAT_PEER_OFFSET = 2_000_000_000
MAX_HISTORY_BATCH = 100
DELETE_BATCH_SIZE = 100

ProgressCallback = Callable[[int], None]


class VKMessageManager:
    """Domain layer on top of :class:`VKClient`.

    Every method that deletes messages only ever touches messages whose
    ``from_id`` equals the current user's ID. Messages written by other
    participants are never deleted.
    """

    def __init__(self, client: VKClient) -> None:
        """Store the API client and prepare the identity cache.

        Args:
            client: Configured :class:`VKClient` instance.
        """
        self._client = client
        self._user_id: int | None = None

    # ------------------------------------------------------------------ identity

    def get_current_user_id(self) -> int:
        """Return the ID of the token owner (cached after the first call).

        Returns:
            The numeric VK user ID associated with the access token.
        """
        if self._user_id is None:
            response = self._client.call("users.get")
            self._user_id = response[0]["id"]
        return self._user_id

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _peer_name(
        peer_id: int,
        conversation: dict[str, Any],
        profiles: dict[int, dict[str, Any]],
        groups: dict[int, dict[str, Any]],
    ) -> str:
        """Resolve a human-readable name for a conversation peer.

        Args:
            peer_id: Conversation peer identifier.
            conversation: Conversation object from the API response.
            profiles: Map of user id to user object.
            groups: Map of community id to community object.

        Returns:
            Display name for the peer.
        """
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
    ) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        """Build id to object maps for profiles and groups.

        Args:
            response: Raw API response containing ``profiles``/``groups``.

        Returns:
            Tuple of ``(profiles_map, groups_map)``.
        """
        profiles = {p["id"]: p for p in response.get("profiles", [])}
        groups = {g["id"]: g for g in response.get("groups", [])}
        return profiles, groups

    @staticmethod
    def _describe_attachments(message: dict[str, Any]) -> list[str]:
        """Return a list of short human-readable labels for attachments.

        Args:
            message: Raw message object from the API.

        Returns:
            Labels such as ``["photo", "doc:notes.pdf", "wall"]``.
        """
        labels: list[str] = []
        for att in message.get("attachments", []):
            kind = att.get("type")
            if kind == "audio_message":
                labels.append("voice")
            elif kind == "doc":
                title = att.get("doc", {}).get("title", "")
                labels.append(f"doc:{title}" if title else "doc")
            elif kind == "link":
                title = att.get("link", {}).get("title", "")
                labels.append(f"link:{title}" if title else "link")
            elif kind:
                labels.append(kind)
            else:
                labels.append("unknown")
        return labels

    @classmethod
    def _message_view(cls, message: dict[str, Any]) -> dict[str, Any]:
        """Convert a raw message into the shape used by the CLI.

        Args:
            message: Raw message object from the API.

        Returns:
            Dict with ``id``, ``from_id``, ``text`` and ``attachments``.
        """
        return {
            "id": message["id"],
            "from_id": message["from_id"],
            "text": message.get("text", ""),
            "attachments": cls._describe_attachments(message),
        }

    def _fetch_history(
        self, peer_id: int, offset: int, count: int
    ) -> list[dict[str, Any]]:
        """Return raw message objects from a conversation.

        Args:
            peer_id: Conversation peer identifier.
            offset: Pagination offset into the (current) history.
            count: Maximum number of messages to return.

        Returns:
            List of raw message dicts as returned by ``messages.getHistory``.
        """
        response = self._client.call(
            "messages.getHistory", peer_id=peer_id, offset=offset, count=count
        )
        return response.get("items", [])

    def _delete_messages(self, peer_id: int, message_ids: list[int]) -> None:
        """Delete a batch of messages in a single request.

        Args:
            peer_id: Conversation peer identifier.
            message_ids: IDs of the messages to delete (max ``DELETE_BATCH_SIZE``).
        """
        if not message_ids:
            return
        self._client.call(
            "messages.delete",
            peer_id=peer_id,
            message_ids=",".join(str(i) for i in message_ids),
            delete_for_all=1,
        )

    @staticmethod
    def _chunks(items: list[int], size: int) -> list[list[int]]:
        """Split a list into consecutive chunks of at most ``size`` items."""
        return [items[i : i + size] for i in range(0, len(items), size)]

    # -------------------------------------------------------------- conversations

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

    # -------------------------------------------------------------- read messages

    def get_conversation_messages(
        self, peer_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return the last ``limit`` messages from a conversation.

        Args:
            peer_id: Conversation peer identifier.
            limit: Maximum number of messages to return.

        Returns:
            A list of dicts with keys ``id``, ``from_id``, ``text`` and
            ``attachments`` (list of short labels).
        """
        limit = min(limit, MAX_HISTORY_BATCH)
        items = self._fetch_history(peer_id, offset=0, count=limit)
        return [self._message_view(m) for m in items]

    # -------------------------------------------------------------- delete messages

    def delete_message(self, message_id: int) -> None:
        """Delete a single message authored by the current user.

        Args:
            message_id: Global message identifier.

        Raises:
            ValueError: If the message is missing or was not authored
                by the current user.
        """
        response = self._client.call("messages.getById", message_ids=str(message_id))
        items = response.get("items", [])
        if not items:
            raise ValueError(f"Message {message_id} not found")

        message = items[0]
        if message["from_id"] != self.get_current_user_id():
            raise ValueError(
                f"Message {message_id} was not authored by you; refusing to delete"
            )

        self._delete_messages(message["peer_id"], [message_id])

    def delete_last_n_messages(
        self,
        peer_id: int,
        n: int,
        progress: ProgressCallback | None = None,
    ) -> int:
        """Delete the last ``n`` messages authored by the current user.

        Walks the history from newest to oldest and stops as soon as ``n``
        of the current user's messages have been deleted.

        Args:
            peer_id: Conversation peer identifier.
            n: Number of own messages to delete.
            progress: Optional callback invoked with the running total of
                deleted messages after each delete batch.

        Returns:
            The number of messages actually deleted (may be less than ``n``
            if the conversation runs out of your messages).
        """
        if n <= 0:
            return 0

        my_id = self.get_current_user_id()
        deleted = 0
        offset = 0

        while deleted < n:
            batch = self._fetch_history(peer_id, offset, MAX_HISTORY_BATCH)
            if not batch:
                break

            mine = [m["id"] for m in batch if m["from_id"] == my_id]
            remaining = n - deleted
            to_delete = mine[:remaining]

            if to_delete:
                for chunk in self._chunks(to_delete, DELETE_BATCH_SIZE):
                    self._delete_messages(peer_id, chunk)
                    deleted += len(chunk)
                    if progress:
                        progress(deleted)

            if len(batch) < MAX_HISTORY_BATCH:
                break

            # Deleted messages shift the remaining history back, so advance
            # the offset only by messages that were *not* deleted.
            offset += len(batch) - len(to_delete)

        return deleted

    def delete_all_messages(
        self,
        peer_id: int,
        progress: ProgressCallback | None = None,
    ) -> int:
        """Delete every message authored by the current user in a conversation.

        Messages written by other participants are left untouched.

        Args:
            peer_id: Conversation peer identifier.
            progress: Optional callback invoked with the running total of
                deleted messages after each delete batch.

        Returns:
            Number of messages that were deleted.
        """
        my_id = self.get_current_user_id()
        deleted = 0
        offset = 0

        while True:
            batch = self._fetch_history(peer_id, offset, MAX_HISTORY_BATCH)
            if not batch:
                break

            mine = [m["id"] for m in batch if m["from_id"] == my_id]
            if mine:
                for chunk in self._chunks(mine, DELETE_BATCH_SIZE):
                    self._delete_messages(peer_id, chunk)
                    deleted += len(chunk)
                    if progress:
                        progress(deleted)

            if len(batch) < MAX_HISTORY_BATCH:
                break

            offset += len(batch) - len(mine)

        return deleted

    def call_raw(self, method: str, **params: Any) -> Any:
        """Call an arbitrary VK API method and return the raw response.

        Args:
            method: Full VK method name, e.g. ``"messages.search"``.
            **params: Method parameters, passed as form fields.

        Returns:
            The ``response`` payload from the API.
        """
        return self._client.call(method, **params)
