"""High-level operations for VK messages and conversations.

All destructive operations in this module are restricted to messages
authored by the current user (the owner of the access token).
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from vk_client import VKClient

CHAT_PEER_OFFSET = 2_000_000_000
MAX_HISTORY_BATCH = 100
DELETE_BATCH_SIZE = 100

EXPORT_DIR = Path("exports")
MAX_FILENAME_LENGTH = 100

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

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

    # ------------------------------------------------------------ export to HTML

    @staticmethod
    def _text_html(text: str) -> str:
        """Escape plain text for HTML and keep line breaks."""
        return html.escape(text, quote=True).replace("\n", "<br>\n")

    @staticmethod
    def _link_html(url: str | None, label: str) -> str:
        """Return an ``<a>`` tag, or just the escaped label if the URL is unusable.

        Args:
            url: Target URL; only ``http(s)`` URLs are turned into links.
            label: Visible link text.
        """
        label_html = html.escape(label)
        if url and url.startswith(("http://", "https://")):
            return f'<a href="{html.escape(url, quote=True)}">{label_html}</a>'
        return label_html

    @staticmethod
    def _largest_image_url(images: list[dict[str, Any]]) -> str | None:
        """Return the URL of the biggest image from a ``sizes``/``images`` list."""
        if not images:
            return None
        best = max(images, key=lambda i: i.get("width", 0) * i.get("height", 0))
        return best.get("url")

    @staticmethod
    def _vk_object_url(kind: str, obj: dict[str, Any]) -> str | None:
        """Build a ``vk.com`` URL like ``/video-1_2_key`` for an attachment."""
        owner_id, obj_id = obj.get("owner_id"), obj.get("id")
        if owner_id is None or obj_id is None:
            return None
        key = f"_{obj['access_key']}" if obj.get("access_key") else ""
        return f"https://vk.com/{kind}{owner_id}_{obj_id}{key}"

    @classmethod
    def _attachment_html(cls, att: dict[str, Any]) -> str:
        """Render one attachment as a link (files are not downloaded).

        Args:
            att: Attachment object from ``message["attachments"]``.

        Returns:
            HTML fragment with a link or, if no URL is known, a text label.
        """
        kind = att.get("type") or "unknown"
        obj = att.get(kind) or {}

        if kind == "video":
            title = obj.get("title")
            return cls._link_html(
                cls._vk_object_url("video", obj),
                f"video: {title}" if title else "video",
            )

        if kind == "audio":
            name = " — ".join(p for p in (obj.get("artist"), obj.get("title")) if p)
            url = obj.get("url") or cls._vk_object_url("audio", obj)
            return cls._link_html(url, f"audio: {name}" if name else "audio")

        if kind == "audio_message":
            url = obj.get("link_mp3") or obj.get("link_ogg")
            return cls._link_html(url, "voice message")

        if kind == "doc":
            title = obj.get("title")
            prefix = "GIF" if str(obj.get("ext", "")).lower() == "gif" else "doc"
            return cls._link_html(
                obj.get("url"), f"{prefix}: {title}" if title else prefix
            )

        # Not required by the spec, but cheap and useful:
        if kind == "photo":
            return cls._link_html(cls._largest_image_url(obj.get("sizes", [])), "photo")
        if kind == "sticker":
            return cls._link_html(
                cls._largest_image_url(obj.get("images", [])), "sticker"
            )
        if kind == "link":
            title = obj.get("title")
            return cls._link_html(obj.get("url"), f"link: {title}" if title else "link")
        if kind == "wall":
            return cls._link_html(cls._vk_object_url("wall", obj), "wall post")

        return html.escape(f"[{kind}]")

    @staticmethod
    def _author_name(
        from_id: int | None,
        profiles: dict[int, dict[str, Any]],
        groups: dict[int, dict[str, Any]],
    ) -> str:
        """Resolve a display name for a message author."""
        if from_id is None:
            return "unknown"
        if from_id > 0:
            user = profiles.get(from_id)
            if user:
                name = f"{user.get('first_name', '')} {user.get('last_name', '')}"
                return name.strip() or f"id{from_id}"
            return f"id{from_id}"
        group = groups.get(-from_id)
        return group.get("name", f"club{-from_id}") if group else f"club{-from_id}"

    @classmethod
    def _message_html(
        cls,
        message: dict[str, Any],
        profiles: dict[int, dict[str, Any]],
        groups: dict[int, dict[str, Any]],
        nested: bool = False,
    ) -> str:
        """Render a message (with replies and forwards) as an HTML block.

        Args:
            message: Raw message object.
            profiles: Map of user id to user object.
            groups: Map of community id to community object.
            nested: ``True`` for replied/forwarded messages (no message ID shown).
        """
        author = html.escape(cls._author_name(message.get("from_id"), profiles, groups))
        date = message.get("date")
        stamp = (
            datetime.fromtimestamp(date).strftime("%Y-%m-%d %H:%M:%S") if date else ""
        )
        header = f"<b>{author}</b> <i>{stamp}</i>"
        if not nested and "id" in message:
            header += f" <small>[{message['id']}]</small>"
        parts = [header]

        action = message.get("action")
        if action:
            parts.append(
                f"<i>[service message: {html.escape(str(action.get('type', '')))}]</i>"
            )
        if message.get("text"):
            parts.append(cls._text_html(message["text"]))
        for att in message.get("attachments", []):
            parts.append(cls._attachment_html(att))

        reply = message.get("reply_message")
        if reply:
            parts.append(
                "<blockquote>In reply to:<br>\n"
                + cls._message_html(reply, profiles, groups, nested=True)
                + "</blockquote>"
            )
        for fwd in message.get("fwd_messages", []):
            parts.append(
                "<blockquote>Forwarded:<br>\n"
                + cls._message_html(fwd, profiles, groups, nested=True)
                + "</blockquote>"
            )

        return "<div>\n" + "<br>\n".join(parts) + "\n</div>"

    @classmethod
    def _render_html(
        cls,
        title: str,
        peer_id: int,
        messages: list[dict[str, Any]],
        profiles: dict[int, dict[str, Any]],
        groups: dict[int, dict[str, Any]],
    ) -> str:
        """Build the complete HTML document for an exported conversation."""
        exported = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = "\n<hr>\n".join(cls._message_html(m, profiles, groups) for m in messages)
        return (
            "<!DOCTYPE html>\n"
            '<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
            f"<title>{html.escape(title)}</title>\n</head>\n<body>\n"
            f"<h1>{html.escape(title)}</h1>\n"
            f"<p>peer_id: {peer_id}<br>\n"
            f"messages: {len(messages)}<br>\n"
            f"exported: {exported}</p>\n<hr>\n"
            f"{body}\n</body>\n</html>\n"
        )

    @staticmethod
    def _safe_filename(name: str, fallback: str) -> str:
        """Turn a conversation name into a valid file name stem.

        Args:
            name: Conversation display name.
            fallback: Stem to use if nothing usable remains (e.g. the peer ID).
        """
        cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
        cleaned = cleaned[:MAX_FILENAME_LENGTH].rstrip(" .")
        if not cleaned or cleaned.upper() in _WINDOWS_RESERVED_NAMES:
            return fallback
        return cleaned

    def _collect_history(
        self,
        peer_id: int,
        limit: int,
        progress: ProgressCallback | None,
    ) -> tuple[
        list[dict[str, Any]],
        dict[int, dict[str, Any]],
        dict[int, dict[str, Any]],
    ]:
        """Download history page by page, newest first, then reverse it.

        Args:
            peer_id: Conversation peer identifier.
            limit: Number of latest messages to fetch; ``< 0`` means all.
            progress: Optional callback invoked with the running total of
                fetched messages after each page.

        Returns:
            ``(messages_in_chronological_order, profiles, groups)``.
        """
        messages: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        profiles: dict[int, dict[str, Any]] = {}
        groups: dict[int, dict[str, Any]] = {}
        offset = 0

        while limit < 0 or len(messages) < limit:
            batch_size = (
                MAX_HISTORY_BATCH
                if limit < 0
                else min(MAX_HISTORY_BATCH, limit - len(messages))
            )
            response = self._client.call(
                "messages.getHistory",
                peer_id=peer_id,
                offset=offset,
                count=batch_size,
                rev=0,
                extended=1,
            )
            items = response.get("items", [])
            if not items:
                break

            page_profiles, page_groups = self._index_profiles(response)
            profiles.update(page_profiles)
            groups.update(page_groups)

            offset += len(items)
            # New messages arriving mid-export shift the offset; skip duplicates.
            for item in items:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    messages.append(item)
            if progress:
                progress(len(messages))

            total = response.get("count")
            if total is not None and offset >= total:
                break

        messages.reverse()
        return messages, profiles, groups

    def export_conversation_html(
        self,
        peer_id: int,
        limit: int = -1,
        save_attachments: bool = False,
        output_dir: str | Path = EXPORT_DIR,
        progress: ProgressCallback | None = None,
    ) -> tuple[Path, int]:
        """Export a conversation to a simple standalone HTML file.

        The file is named after the conversation/peer and an existing file with
        the same name is overwritten. Attachments are replaced with links.

        Args:
            peer_id: Conversation peer identifier.
            limit: Number of latest messages to export; ``< 0`` exports all.
            save_attachments: Reserved for downloading attachments.
            output_dir: Directory for the resulting file (created if needed).
            progress: Optional callback invoked with the running total of
                fetched messages after each page.

        Returns:
            ``(path_to_file, exported_message_count)``.

        Raises:
            NotImplementedError: If ``save_attachments`` is true.
        """
        if save_attachments:
            raise NotImplementedError("Not implemented yet.")

        name = self.get_conversation_name(peer_id)
        messages, profiles, groups = self._collect_history(peer_id, limit, progress)
        document = self._render_html(name, peer_id, messages, profiles, groups)

        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self._safe_filename(name, str(peer_id))}.html"
        path.write_text(document, encoding="utf-8")
        return path, len(messages)

    def call_raw(self, method: str, **params: Any) -> Any:
        """Call an arbitrary VK API method and return the raw response.

        Args:
            method: Full VK method name, e.g. ``"messages.search"``.
            **params: Method parameters, passed as form fields.

        Returns:
            The ``response`` payload from the API.
        """
        return self._client.call(method, **params)
