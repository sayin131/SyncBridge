"""
planner.py — the core sync decision logic.

Given three views of the world for each file path:
  base   = hash at last successful sync (None if never synced)
  laptop = current hash on the laptop (None if file doesn't exist there)
  phone  = current hash on the phone (None if file doesn't exist there)

...decide what action to take: PUSH, PULL, DELETE_LAPTOP, DELETE_PHONE, CONFLICT, or nothing.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    kind: str   # PUSH | PULL | DELETE_LAPTOP | DELETE_PHONE | CONFLICT
    path: str


def plan(base: dict, laptop: dict, phone: dict) -> list[Action]:
    actions = []
    all_paths = set(base) | set(laptop) | set(phone)

    for path in sorted(all_paths):
        b = base.get(path)
        l = laptop.get(path)
        p = phone.get(path)

        # Both sides agree right now (including both missing) -> nothing to do.
        if l == p:
            continue

        # ---- file did not exist before (no base) ----
        if b is None:
            if l is not None and p is None:
                actions.append(Action("PUSH", path))
            elif l is None and p is not None:
                actions.append(Action("PULL", path))
            else:
                # both created it independently with different content
                actions.append(Action("CONFLICT", path))
            continue

        # ---- file existed before (base is not None) ----
        laptop_changed = (l != b)
        phone_changed = (p != b)

        if laptop_changed and phone_changed:
            # both moved away from base, and we already know l != p (checked above)
            actions.append(Action("CONFLICT", path))
        elif laptop_changed and not phone_changed:
            # laptop edited or deleted it; phone still matches base
            if l is None:
                actions.append(Action("DELETE_PHONE", path))
            else:
                actions.append(Action("PUSH", path))
        elif phone_changed and not laptop_changed:
            if p is None:
                actions.append(Action("DELETE_LAPTOP", path))
            else:
                actions.append(Action("PULL", path))
        # else: neither changed relative to base, but l != p was already excluded above,
        # so this branch is unreachable — kept for clarity.

    return actions
