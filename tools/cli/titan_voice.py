from __future__ import annotations

import sys
from typing import Optional


CHECK = "\u2713"
CROSS = "\u2717"
BULLET = "\u2022"
ARROW = "\u2192"
SPARKLE = "\U0001f31f"
BRAIN = "\U0001f9e0"
ROBOT = "\U0001f916"
KEY = "\U0001f511"
EYES = "\U0001f440"
ROCKET = "\U0001f680"
WARNING = "\u26a0"
HOURGLASS = "\U0001f551"
PIN = "\U0001f4cd"
FLAG = "\U0001f6a9"


def _print(text: str = "", file=None, flush: bool = False, end: str = "\n") -> None:
    if file is None:
        file = sys.stdout
    print(text, file=file, flush=flush, end=end)


def _outro(text: str) -> None:
    _print(f"\n{text}\n")


def intro(text: str) -> None:
    _print(f"\n{text}...\n")


def check(passed: bool, item: str, detail: str = "") -> None:
    symbol = CHECK if passed else CROSS
    color_open = ""
    color_close = ""
    if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
        if passed:
            color_open = "\033[92m"
            color_close = "\033[0m"
        else:
            color_open = "\033[91m"
            color_close = "\033[0m"
    prefix = f"{color_open}{symbol}{color_close}"
    if detail:
        _print(f"  {prefix} {item} — {detail}")
    else:
        _print(f"  {prefix} {item}")


def warn(text: str) -> None:
    _print(f"\n  {WARNING} {text}\n")


def section(text: str) -> None:
    _print(f"\n{text}\n")


def prompt(text: str) -> str:
    _print(f"\n{text}")
    _print(f"  {ARROW} ", end="", flush=True)
    return input().strip()


def prompt_choice(text: str, options: list[tuple[str, str]], default: int = 1) -> str:
    _print(f"\n{text}")
    for i, (key, desc) in enumerate(options, 1):
        marker = "(recommended)" if i == default else ""
        _print(f"  [{i}] {desc} {marker}")
    while True:
        _print(f"  {ARROW} ", end="", flush=True)
        raw = input().strip()
        if raw == "":
            return options[default - 1][0]
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        _print(f"  Hmm, that's not a valid choice. Pick a number.")


def confirm(text: str, default_yes: bool = True) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        _print(f"\n  {text} {suffix}: ", end="", flush=True)
        raw = input().strip().lower()
        if raw == "":
            return default_yes
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        _print("  Please enter 'y' or 'n'.")


def success(text: str) -> None:
    _print(f"  {CHECK} {text}")


def step(text: str) -> None:
    _print(f"\n{text}...")


def step_complete(from_text: str, to_text: str) -> None:
    _print(f"  {CHECK} {from_text} \u2192 {to_text}")


def info(text: str) -> None:
    _print(f"  {BULLET} {text}")


def error(text: str, fix: str = "") -> None:
    _print(f"\n  {CROSS} {text}\n")
    if fix:
        _print(f"  Run: {fix}\n")


def outro_success(heading: str, items: list[str], next_tip: str) -> None:
    _print(f"\n{heading}")
    for item in items:
        _print(f"  {CHECK} {item}")
    _print(f"\n{next_tip}\n")


def outro_blocked(heading: str, fix_instructions: list[str]) -> None:
    _print(f"\n{heading}")
    for line in fix_instructions:
        _print(f"  {BULLET} {line}")
    _print(f"\nOnce you've done that, run: titan setup opencode")
    _print(f"I'll be right here.\n")


def api_key_prompt(provider: str) -> str:
    _print(f"\nI'll need your API key for that.")
    _print(f"  Enter your {provider} API key: ", end="", flush=True)
    return input().strip()


def key_saved(provider: str) -> None:
    _print(f"  {CHECK} Saved securely.\n")


def model_picked(model: str, note: str = "") -> None:
    if note:
        _print(f"  {CHECK} {model} — {note}")
    else:
        _print(f"  {CHECK} {model}")


def agent_found(agent: str, path: str, confirmed: bool) -> None:
    if confirmed:
        _print(f"  {CHECK} Found {agent} at {path}")
    else:
        _print(f"  {FLAG} Found {agent} at {path} — is this right?")


def agent_not_found(agent: str) -> None:
    _print(f"  {CROSS} I couldn't find {agent} on your system.")
    _print(f"  Make sure it's installed, then run: titan setup opencode")
