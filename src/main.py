"""Interactive CLI for the VK Message Manager."""

from __future__ import annotations

from typing import Any

from vk_client import VKAPIError, VKClient
from vk_manager import VKMessageManager

MENU = """\
1) Show last 10 messages
2) Delete message by ID
3) List conversations
4) Show last 10 messages from conversation
5) Delete last message from conversation
6) Delete all messages from conversation
0) Exit
"""


def _prompt_int(prompt: str) -> int:
    """Read an integer from stdin, retrying on invalid input."""
    while True:
        try:
            return int(input(prompt).strip())
        except ValueError:
            print("Please enter a valid integer.")


def _print_last_messages(messages: list[dict[str, Any]]) -> None:
    if not messages:
        print("(no messages)")
        return
    for msg in messages:
        print(f"> [{msg['id']}] ({msg['conv_name']}) {msg['text']}")


def _print_conversations(conversations: list[tuple[int, str]]) -> None:
    if not conversations:
        print("(no conversations)")
        return
    for peer_id, name in conversations:
        print(f"[{peer_id}] {name}")


def _print_conversation_messages(
    name: str, peer_id: int, messages: list[dict[str, Any]]
) -> None:
    print(f"{name} (ID={peer_id})")
    if not messages:
        print("(no messages)")
        return
    for msg in messages:
        print(f"> [{msg['id']}] {msg['text']}")


def main() -> None:
    """Run the interactive CLI loop."""
    manager = VKMessageManager(VKClient())

    while True:
        print(MENU)
        choice = input("Choose an option: ").strip()
        try:
            if choice == "1":
                _print_last_messages(manager.get_last_messages(10))
            elif choice == "2":
                message_id = _prompt_int("Message ID: ")
                manager.delete_message(message_id)
                print("Deleted.")
            elif choice == "3":
                _print_conversations(manager.get_conversations())
            elif choice == "4":
                peer_id = _prompt_int("Conversation peer ID: ")
                name = manager.get_conversation_name(peer_id)
                messages = manager.get_conversation_messages(peer_id, limit=10)
                _print_conversation_messages(name, peer_id, messages)
            elif choice == "5":
                peer_id = _prompt_int("Conversation peer ID: ")
                manager.delete_last_message(peer_id)
                print("Deleted.")
            elif choice == "6":
                peer_id = _prompt_int("Conversation peer ID: ")
                count = manager.delete_all_messages(peer_id)
                print(f"Deleted {count} message(s).")
            elif choice == "0":
                break
            else:
                print("Unknown option.")
        except VKAPIError as err:
            print(f"VK API error: {err}")
        except ValueError as err:
            print(f"Error: {err}")


if __name__ == "__main__":
    main()
