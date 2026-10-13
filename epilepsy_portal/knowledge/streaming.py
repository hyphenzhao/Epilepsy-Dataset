"""Utilities for separating streamed reasoning from report text."""


def split_thinking_delta(delta, state, *, final=False):
    """Separate <think> blocks, retaining delimiters split across chunks."""
    buf = state.pop("pending", "") + (delta or "")
    thinking_parts = []
    answer_parts = []

    while buf:
        tag = "</think>" if state.get("in_think", False) else "<think>"
        parts = thinking_parts if state.get("in_think", False) else answer_parts
        position = buf.find(tag)
        if position >= 0:
            parts.append(buf[:position])
            buf = buf[position + len(tag):]
            state["in_think"] = not state.get("in_think", False)
            continue

        # A delimiter may arrive in several Ollama deltas. Hold its prefix
        # until the next delta so it cannot escape into the report body.
        pending_length = 0
        if not final:
            for size in range(min(len(buf), len(tag) - 1), 0, -1):
                if buf.endswith(tag[:size]):
                    pending_length = size
                    break
        if pending_length:
            parts.append(buf[:-pending_length])
            state["pending"] = buf[-pending_length:]
        else:
            parts.append(buf)
        break

    return "".join(thinking_parts), "".join(answer_parts)
