"""Small string helpers shared across the app."""


def trunc_str_with_ellipsis(max_length: int, content: str) -> str:
    """Return ``content`` cut to at most ``max_length`` chars, ending in ``...`` if cut."""
    if len(content) > max_length:
        return content[: max_length - 3] + "..."
    return content
