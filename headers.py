from __future__ import annotations
import random
from typing import Sequence

# /home/przemek/Dokumenty/Coding/AniwatchSonarr/headers.py


_DEFAULT_USER_AGENTS: Sequence[str] = (
    # Desktop - Chrome (Windows/macOS/Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Desktop - Firefox (Windows/macOS/Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.6; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Desktop - Edge (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Mobile - Safari (iOS)
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    # Mobile - Chrome (Android)
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
)


def random_user_agent(
    user_agents: Sequence[str] | None = None, *, rng: random.Random | None = None
) -> str:
    """
    Return a random User-Agent string.

    Args:
        user_agents: Optional custom pool of user-agents. If None, uses defaults.
        rng: Optional random generator for determinism in tests.

    Raises:
        ValueError: If the provided pool is empty.

    """
    pool = tuple(user_agents) if user_agents is not None else _DEFAULT_USER_AGENTS
    if not pool:
        raise ValueError("user_agents pool must not be empty")
    r = rng or random
    return r.choice(pool)
