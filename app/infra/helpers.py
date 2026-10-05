def trunc_str_with_ellipsis(max_length: int, content: str) -> str:
    if len(content) > max_length:
        return content[: max_length - 3] + "..."
    return content
