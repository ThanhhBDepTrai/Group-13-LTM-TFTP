"""Trạng thái nhiệm vụ và các chuyển đổi hợp lệ."""

STATUSES = ("todo", "in_progress", "review", "done", "cancelled")

TRANSITIONS = {
    "todo": {"in_progress", "cancelled"},
    "in_progress": {"review", "todo", "cancelled"},
    "review": {"done", "in_progress"},
    "done": set(),
    "cancelled": set(),
}


def can_transition(old: str, new: str) -> bool:
    return new in TRANSITIONS.get(old, set())
