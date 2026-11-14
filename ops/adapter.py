"""adapter - config bridge between the four existing executors and ops.

Real-world field names observed in HAR evidence and executor configs:
``kcid`` (课程号), ``skbj`` / ``jxbh`` (教学班号), ``priority``, ``xnxq``
(学期), plus camelCase variants emitted by some tooling. This module maps
any of those onto the ops ``{course_id, class_id, priority}`` contract and
validates strictly: unknown shapes raise instead of being silently half-
imported, so a typo can never produce an empty target list.

Nothing here touches the network and nothing echoes credentials.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = ["ALIASES", "normalize_target", "load_targets", "load_mission",
           "export_targets"]

ALIASES: Dict[str, str] = {
    # canonical course/class identity from HAR + qiangke/ncwu configs
    "kcid": "course_id", "courseid": "course_id", "course_id": "course_id",
    "course": "course_id", "课程号": "course_id",
    "skbj": "class_id", "jxbh": "class_id", "classid": "class_id",
    "class_id": "class_id", "teachingclass": "class_id", "教学班": "class_id",
    "priority": "priority", "vol": "priority", "志愿": "priority",
    "xnxq": "term", "term": "term", "学期": "term",
    "enabled": "enabled",
}

REQUIRED = ("course_id",)
_PRIORITY_RANGE = (1, 99)


def normalize_target(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map one raw executor target onto the ops contract.

    Raises ValueError on missing identity instead of emitting a hollow
    target that would later match nothing.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"target must be an object, got {type(raw).__name__}")
    out: Dict[str, Any] = {}
    seen: set = set()
    for k, v in raw.items():
        canon = ALIASES.get(str(k).strip().lower())
        if canon is None:
            out[str(k)] = v          # unknown keys pass through untouched
            continue
        if canon in seen:
            # Two legacy clients may supply both e.g. kcid and course_id.
            # Only identical values are safe; first-wins silently chooses the
            # wrong course or teaching class when the aliases disagree.
            if str(out[canon]).strip() != str(v).strip():
                raise ValueError(f"conflicting aliases for {canon}")
            continue
        seen.add(canon)
        out[canon] = v
    for req in REQUIRED:
        if not str(out.get(req, "")).strip():
            raise ValueError(f"target missing {req!r}: keys={sorted(raw)}")
    try:
        pr = int(out.get("priority", 1))
    except (TypeError, ValueError):
        raise ValueError(f"priority not an integer: {out.get('priority')!r}")
    if not (_PRIORITY_RANGE[0] <= pr <= _PRIORITY_RANGE[1]):
        raise ValueError(f"priority {pr} outside {_PRIORITY_RANGE}")
    out["priority"] = pr
    out.setdefault("enabled", True)
    out["enabled"] = bool(out["enabled"])
    if "class_id" in out:
        out["class_id"] = str(out["class_id"]).strip()
    return out


def load_targets(source: Any) -> List[Dict[str, Any]]:
    """Load targets from a JSON string, file path, or list of dicts."""
    data: Any
    if isinstance(source, (str, Path)):
        p = Path(source)
        text = p.read_text(encoding="utf-8") if p.exists() else str(source)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc}") from exc
    else:
        data = source
    if isinstance(data, dict):
        for key in ("targets", "courses", "目标课程", "course_list"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            raise ValueError("config object has no targets array "
                             "(tried targets/courses/目标课程/course_list)")
    if not isinstance(data, list) or not data:
        raise ValueError("targets must be a non-empty array")
    out = [normalize_target(t) for t in data]
    enabled = [t for t in out if t["enabled"]]
    if not enabled:
        raise ValueError("all targets disabled")
    return out


def load_mission(source: Any, **overrides) -> Dict[str, Any]:
    """Build an orchestrator ``Mission`` kwargs dict from a legacy config.

    Recognised wrapper keys: ``host`` / ``jw_host`` / ``baseUrl`` (host),
    ``poll_interval_ms`` / ``interval`` / ``查询间隔`` (base poll),
    ``sessions`` / ``账号数``. Unrecognised top-level keys are returned in
    ``unknown`` so the caller decides, never silently dropped.
    """
    data: Any
    if isinstance(source, (str, Path)):
        p = Path(source)
        data = json.loads(p.read_text(encoding="utf-8") if p.exists()
                          else str(source))
    else:
        data = source
    if not isinstance(data, dict):
        raise ValueError("mission config must be an object")
    kw: Dict[str, Any] = {}
    unknown: List[str] = []
    for k, v in data.items():
        lk = str(k).strip().lower()
        if lk in ("host", "jw_host", "baseurl", "base_url"):
            kw["host"] = str(v)
        elif lk in ("poll_interval_ms", "interval", "interval_ms", "查询间隔"):
            try:
                ms = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"poll interval not numeric: {v!r}")
            kw["poll_interval_base_ms"] = max(1000.0, min(ms, 3600000.0))
        elif lk in ("sessions", "accounts", "账号数"):
            kw["sessions"] = max(1, int(v))
        elif lk == "catalog_path":
            kw["catalog_path"] = str(v)
        elif lk in ("targets", "courses", "course_list", "目标课程"):
            pass                     # consumed by load_targets below
        else:
            unknown.append(str(k))
    kw["targets"] = load_targets(data.get("targets", data))
    kw.update(overrides)
    kw["unknown_keys"] = unknown
    return kw


def export_targets(targets: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Inverse mapping: ops contract -> executor-facing kcid/skbj fields."""
    inv = {"course_id": "kcid", "class_id": "skbj", "priority": "priority",
           "term": "xnxq", "enabled": "enabled"}
    rows: List[Dict[str, Any]] = []
    for t in targets:
        rows.append({inv.get(k, k): v for k, v in t.items()})
    return rows


def demo() -> dict:
    raw = [{"kcid": "C1", "skbj": "01", "priority": 1},
           {"courseId": "C2", "jxbh": "02", "vol": 2, "enabled": 0},
           {"course_id": "C3", "priority": "3", "note": "passthrough"}]
    good = [normalize_target(t) for t in raw]
    try:
        normalize_target({"skbj": "01"})
        bad = "accepted"
    except ValueError as exc:
        bad = f"rejected: {exc}"
    mission = load_mission({"jw_host": "TARGET_HOST", "查询间隔": 30000,
                            "账号数": 2, "mystery": 1,
                            "targets": raw[:2]})
    return {"normalized": good, "missing_course_id": bad,
            "mission": mission, "export": export_targets(good[:1])}


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=1, default=str))
