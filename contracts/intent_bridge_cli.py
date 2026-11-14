"""Fail-closed JSON/stdin bridge to the existing SQLite intent journal.

Node runs python -m contracts.intent_bridge_cli. This does not perform
network I/O, accept cookies or implement a new enrollment executor.
Never print identifiers, input, exceptions, or database paths.
"""
from __future__ import annotations

import json
import sys

from .intent_bridge import reserve_shared_intent


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise ValueError("oversized request")
        request = json.loads(raw)
        if (not isinstance(request, dict)
                or set(request) != {"school", "account", "term", "course_id", "class_id"}
                or any(not isinstance(value, str) for value in request.values())):
            raise ValueError("invalid request")
        reserved = reserve_shared_intent(**request)
        print(json.dumps({"status": "reserved" if reserved else "duplicate"}))
        return 0
    except Exception:
        # All failures leave the Node submission stopped, with no data leaked.
        print('{"status":"blocked"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
