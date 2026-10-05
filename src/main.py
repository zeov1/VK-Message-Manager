"""Interactive CLI for the VK Message Manager."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import requests

from vk_client import VKAPIError, VKClient
from vk_manager import MAX_HISTORY_BATCH, VKMessageManager

MAIN_MENU = """\
1) Choose conversation
2) Print all conversations
3) Call custom API method
4) Remove all messages in multiple conversations
5) Export conversation to HTML
0) Exit
"""

CONVERSATION_MENU = """\
1) Show last N messages
2) Delete last N messages (only yours)
3) Delete all your messages
0) Back
"""

DEFAULT_N = 5
PROGRESS_STEP = 1000
EXPORT_ALL = -1


# ------------------------------------------------------------------------- input


def _prompt_int(prompt: str) -> int:
    """Read an integer from stdin, retrying on invalid input."""
    while True:
        try:
            return int(input(prompt).strip())
        except ValueError:
            print("Please enter a valid integer.")


def _prompt_n(default: int = DEFAULT_N) -> int:
    """Prompt for the number of messages, clamped to [1, MAX_HISTORY_BATCH]."""
    raw = input(
        f"Number of messages [default {default}, max {MAX_HISTORY_BATCH}]: "
    ).strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        print(f"Invalid input, using default {default}.")
        return default
    if n < 1:
        print("Value must be >= 1, using 1.")
        return 1
    if n > MAX_HISTORY_BATCH:
        print(f"Value capped to {MAX_HISTORY_BATCH}.")
        return MAX_HISTORY_BATCH
    return n


def _prompt_peer_id() -> int | None:
    """Ask for a peer ID until it is a valid integer.

    Returns:
        The peer ID, or ``None`` if the user submitted an empty line.
    """
    while True:
        raw = input("Peer ID (empty to cancel): ").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            print("Please enter a valid integer.")


def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    """Ask a y/n question; an empty answer returns ``default``."""
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} ({hint}): ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def _prompt_export_count() -> int:
    """Ask how many latest messages to export (``-1`` means all)."""
    while True:
        raw = input("Messages count [default=-1 (save all)]: ").strip()
        if not raw:
            return EXPORT_ALL
        try:
            n = int(raw)
        except ValueError:
            print("Please enter a valid integer.")
            continue
        return n if n > 0 else EXPORT_ALL


# ----------------------------------------------------------------------- output


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds as ``h:mm:ss``."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _print_conversations(manager: VKMessageManager) -> None:
    """Fetch and print the user's conversations with 1-based indices."""
    conversations = manager.get_conversations()
    if not conversations:
        print("(no conversations)")
        return
    for index, (peer_id, name) in enumerate(conversations, start=1):
        print(f"{index:>3}) [{peer_id}] {name}")


def _print_messages(messages: list[dict[str, Any]]) -> None:
    if not messages:
        print("(no messages)")
        return
    for msg in messages:
        attachments = msg["attachments"]
        suffix = f"  [attachments: {', '.join(attachments)}]" if attachments else ""
        text = msg["text"] or "(no text)"
        print(f"> [{msg['id']}] {text}{suffix}")


def _make_progress_printer(label: str = "Deleted") -> Callable[[int], None]:
    """Return a callback that prints progress once per ``PROGRESS_STEP`` items.

    Args:
        label: Word printed before the running counter.

    Returns:
        Callback suitable for the ``progress`` parameter of the manager.
    """
    start = time.monotonic()
    last_milestone = 0

    def callback(count: int) -> None:
        nonlocal last_milestone
        milestone = count // PROGRESS_STEP
        if milestone > last_milestone:
            last_milestone = milestone
            elapsed = time.monotonic() - start
            print(f"  {label} {count} message(s) — elapsed {_format_duration(elapsed)}")

    return callback


# ------------------------------------------------------------------- workflows


def _choose_conversation(
    manager: VKMessageManager,
) -> tuple[int, str] | None:
    """Show the conversation list and let the user pick one.

    Args:
        manager: Configured :class:`VKMessageManager`.

    Returns:
        ``(peer_id, name)`` of the chosen conversation, or ``None`` if the
        user cancelled.
    """
    conversations = manager.get_conversations()
    if not conversations:
        print("(no conversations)")
        return None

    for index, (peer_id, name) in enumerate(conversations, start=1):
        print(f"{index:>3}) [{peer_id}] {name}")

    while True:
        raw = input("Choose conversation number (0 to cancel): ").strip()
        if not raw:
            continue
        try:
            index = int(raw)
        except ValueError:
            print("Please enter a valid integer.")
            continue
        if index == 0:
            return None
        if 1 <= index <= len(conversations):
            return conversations[index - 1]
        print(f"Out of range (1-{len(conversations)}).")


def _run_conversation_menu(manager: VKMessageManager, peer_id: int, name: str) -> None:
    """Run the per-conversation action loop.

    Args:
        manager: Configured :class:`VKMessageManager`.
        peer_id: Chosen conversation peer identifier.
        name: Display name of the conversation.
    """
    while True:
        print(f"\n=== {name} (ID={peer_id}) ===")
        print(CONVERSATION_MENU, end="")
        choice = input("Choose an action: ").strip()

        try:
            if choice == "1":
                n = _prompt_n()
                messages = manager.get_conversation_messages(peer_id, limit=n)
                _print_messages(messages)

            elif choice == "2":
                n = _prompt_n()
                start = time.monotonic()
                progress = _make_progress_printer("Deleted")
                deleted = manager.delete_last_n_messages(peer_id, n, progress=progress)
                elapsed = time.monotonic() - start
                print(f"Deleted {deleted} message(s) in {_format_duration(elapsed)}.")

            elif choice == "3":
                confirm = (
                    input(
                        "This will delete ALL your messages in this conversation. "
                        "Type 'yes' to confirm: "
                    )
                    .strip()
                    .lower()
                )
                if confirm != "yes":
                    print("Cancelled.")
                    continue
                start = time.monotonic()
                progress = _make_progress_printer("Deleted")
                deleted = manager.delete_all_messages(peer_id, progress=progress)
                elapsed = time.monotonic() - start
                print(f"Deleted {deleted} message(s) in {_format_duration(elapsed)}.")

            elif choice == "0":
                return

            else:
                print("Unknown option.")

        except VKAPIError as err:
            print(f"VK API error: {err}")
        except ValueError as err:
            print(f"Error: {err}")


def _parse_param_value(raw: str) -> Any:
    """Coerce a raw parameter value to int when possible, else keep it as str."""
    try:
        return int(raw)
    except ValueError:
        return raw


def _run_custom_api(manager: VKMessageManager) -> None:
    """Interactively call an arbitrary VK API method.

    Prompts for the method name, then repeatedly asks for parameter
    name/value pairs until the user submits an empty parameter name. The
    collected parameters are sent as-is and the raw JSON response is printed.

    Args:
        manager: Configured :class:`VKMessageManager`.
    """
    method = input("API method (e.g. messages.search): ").strip()
    if not method:
        print("Cancelled.")
        return

    params: dict[str, Any] = {}
    print(
        "Enter parameter name/value pairs one at a time.\n"
        "Submit an empty parameter name to send the request."
    )
    while True:
        name = input("  Param name (empty to send): ").strip()
        if not name:
            break
        value = input(f"  Value for '{name}': ").strip()
        params[name] = _parse_param_value(value)

    print(f"\nCalling {method} with params={params} ...")
    try:
        response = manager.call_raw(method, **params)
    except VKAPIError as err:
        print(f"VK API error: {err}")
        return

    print(json.dumps(response, indent=2, ensure_ascii=False))


def _run_bulk_delete(manager: VKMessageManager) -> None:
    """Delete every own message across a user-supplied list of conversations.

    Prompts for conversation peer IDs one at a time; an empty line ends
    the list. After a single confirmation, deletes all messages authored
    by the current user in each listed conversation. A failure in one
    conversation does not abort the rest of the batch.

    Args:
        manager: Configured :class:`VKMessageManager`.
    """
    peers: dict[int, str] = {}  # peer_id: name
    print("Enter conversation peer IDs one at a time.\nSubmit an empty line to finish.")
    while True:
        raw = input("  Peer ID (empty to finish): ").strip()
        if not raw:
            break
        try:
            peer_id = int(raw)
            name = manager.get_conversation_name(peer_id)
            peers[peer_id] = name
        except ValueError:
            print(f"Wrong peer ID: {raw}")

    if not peers:
        print("No conversations specified. Cancelled.")
        return

    print("Selected conversations:")
    for index, (pid, pname) in enumerate(peers.items()):
        print(f"{index:>2}. [{pid}], {pname}")

    confirm = (
        input(
            f"\nThis will delete ALL your messages in {len(peers)} "
            "conversation(s). Type 'yes' to confirm: "
        )
        .strip()
        .lower()
    )
    if confirm != "yes":
        print("Cancelled.")
        return

    start = time.monotonic()
    total = 0
    for index, (peer_id, name) in enumerate(peers.items(), start=1):
        header = f"[{index}/{len(peers)}] ID={peer_id}"
        try:
            header = f"[{index}/{len(peers)}] {name} (ID={peer_id})"
        except VKAPIError as err:
            print(f"\n{header}\n  VK API error while resolving name: {err}")
            continue

        print(f"\n{header}")
        progress = _make_progress_printer("Deleted")
        try:
            deleted = manager.delete_all_messages(peer_id, progress=progress)
        except VKAPIError as err:
            print(f"  VK API error: {err}")
            continue
        except ValueError as err:
            print(f"  Error: {err}")
            continue

        total += deleted
        print(f"  Deleted {deleted} message(s).")

    elapsed = time.monotonic() - start
    print(f"\nDone. Total deleted: {total} message(s) in {_format_duration(elapsed)}.")


def _run_export(manager: VKMessageManager) -> None:
    """Export a whole conversation (or its last N messages) to an HTML file.

    Asks for the peer ID, whether attachments should be saved (not
    implemented yet) and how many latest messages to export. Attachments
    are written to the file as links.

    Args:
        manager: Configured :class:`VKMessageManager`.
    """
    peer_id = _prompt_peer_id()
    if peer_id is None:
        print("Cancelled.")
        return

    if _prompt_yes_no("Save attachments", default=False):
        print("Not implemented yet.")
        return

    count = _prompt_export_count()

    start = time.monotonic()
    progress = _make_progress_printer("Fetched")
    try:
        path, exported = manager.export_conversation_html(
            peer_id, limit=count, save_attachments=False, progress=progress
        )
    except NotImplementedError as err:
        print(err)
        return
    except VKAPIError as err:
        print(f"VK API error: {err}")
        return
    except requests.RequestException as err:
        print(f"Network error: {err}")
        return

    elapsed = time.monotonic() - start
    print(f"Exported {exported} message(s) to {path} in {_format_duration(elapsed)}.")


def _run(manager: VKMessageManager) -> None:
    """Run the top-level CLI loop.

    Args:
        manager: Configured :class:`VKMessageManager`.
    """
    while True:
        print(f"\n{MAIN_MENU}", end="")
        choice = input("Choose an option: ").strip()

        match choice:
            case "1":
                try:
                    picked = _choose_conversation(manager)
                    if picked is None:
                        continue
                    peer_id, name = picked
                    _run_conversation_menu(manager, peer_id, name)
                except VKAPIError as err:
                    print(f"VK API error: {err}")
                except ValueError as err:
                    print(f"Error: {err}")
            case "2":
                try:
                    _print_conversations(manager)
                except VKAPIError as err:
                    print(f"VK API error: {err}")
                except ValueError as err:
                    print(f"Error: {err}")
            case "3":
                _run_custom_api(manager)
            case "4":
                _run_bulk_delete(manager)
            case "5":
                _run_export(manager)
            case "0":
                print("Bye.")
                return
            case _:
                print("Unknown option.")


def main() -> None:
    """Entry point with graceful Ctrl+C handling."""
    manager = VKMessageManager(VKClient())
    try:
        _run(manager)
    except KeyboardInterrupt:
        print("\nInterrupted. Bye.")


if __name__ == "__main__":
    main()
