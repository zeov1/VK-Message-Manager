"""Interactive CLI for the VK Message Manager."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from vk_client import VKAPIError, VKClient
from vk_manager import MAX_HISTORY_BATCH, VKMessageManager

MAIN_MENU = """\
1) Choose conversation
2) Print all conversations
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


# ----------------------------------------------------------------------- output


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds as ``h:mm:ss``."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _print_conversations(manager: VKMessageManager) -> None:
    conversations = manager.get_conversations()
    if not conversations:
        print("(no conversations)")
        return

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
    """Return a callback that prints progress once per ``PROGRESS_STEP`` deletes.

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
        conversation = manager.get_conversation_name(index)
        return (index, conversation)
        print("Out of range.")


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
