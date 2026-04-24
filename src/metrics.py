import json
from collections import defaultdict
from functools import wraps
from threading import Lock
from time import perf_counter
from typing import Any

from temporalio import activity


_LOCK = Lock()
_ACTIVITY_EVENTS: list[dict[str, Any]] = []
_AGENT_TOKEN_EVENTS: list[dict[str, Any]] = []


def instrument_activity(activity_name: str):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            workflow_id = current_workflow_id()
            started_at = perf_counter()
            status = "success"

            try:
                return await fn(*args, **kwargs)
            except Exception:
                status = "failure"
                raise
            finally:
                elapsed_ms = round((perf_counter() - started_at) * 1000, 3)
                record_activity_metric(
                    activity_name,
                    elapsed_ms=elapsed_ms,
                    status=status,
                    workflow_id=workflow_id,
                )

        return wrapper

    return decorator


def current_workflow_id() -> str | None:
    try:
        return activity.info().workflow_id
    except Exception:
        return None


def estimate_tokens(value: Any) -> int:
    text = _serialize_value(value).strip()
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def record_activity_metric(
    activity_name: str,
    *,
    elapsed_ms: float,
    status: str,
    workflow_id: str | None = None,
) -> None:
    event = {
        "activity_name": activity_name,
        "workflow_id": workflow_id,
        "elapsed_ms": elapsed_ms,
        "status": status,
    }
    with _LOCK:
        _ACTIVITY_EVENTS.append(event)


def record_agent_tokens(
    agent_name: str,
    *,
    prompt: Any,
    output: Any = None,
    status: str = "success",
    workflow_id: str | None = None,
) -> None:
    resolved_workflow_id = workflow_id or current_workflow_id()
    prompt_tokens = estimate_tokens(prompt)
    completion_tokens = estimate_tokens(output)
    event = {
        "agent_name": agent_name,
        "workflow_id": resolved_workflow_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_estimated_tokens": prompt_tokens + completion_tokens,
        "status": status,
    }
    with _LOCK:
        _AGENT_TOKEN_EVENTS.append(event)


def reset_metrics() -> None:
    with _LOCK:
        _ACTIVITY_EVENTS.clear()
        _AGENT_TOKEN_EVENTS.clear()


def snapshot_metrics() -> dict[str, Any]:
    with _LOCK:
        activity_events = [event.copy() for event in _ACTIVITY_EVENTS]
        agent_token_events = [event.copy() for event in _AGENT_TOKEN_EVENTS]

    return {
        "activity_events": activity_events,
        "activity_metrics": _summarize_activity_events(activity_events),
        "agent_token_events": agent_token_events,
        "agent_token_metrics": {
            "by_agent": _summarize_agent_events(
                agent_token_events,
                key_name="agent_name",
            ),
            "by_workflow": _summarize_agent_events(
                agent_token_events,
                key_name="workflow_id",
            ),
        },
    }


def _summarize_activity_events(activity_events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "success_count": 0,
            "failure_count": 0,
            "total_ms": 0.0,
            "avg_ms": 0.0,
            "max_ms": 0.0,
            "min_ms": 0.0,
        }
    )

    for event in activity_events:
        summary = grouped[event["activity_name"]]
        summary["count"] += 1
        if event["status"] == "success":
            summary["success_count"] += 1
        else:
            summary["failure_count"] += 1
        summary["total_ms"] += event["elapsed_ms"]
        summary["max_ms"] = max(summary["max_ms"], event["elapsed_ms"])
        if summary["min_ms"] == 0.0:
            summary["min_ms"] = event["elapsed_ms"]
        else:
            summary["min_ms"] = min(summary["min_ms"], event["elapsed_ms"])

    for summary in grouped.values():
        if summary["count"]:
            summary["total_ms"] = round(summary["total_ms"], 3)
            summary["avg_ms"] = round(summary["total_ms"] / summary["count"], 3)
            summary["max_ms"] = round(summary["max_ms"], 3)
            summary["min_ms"] = round(summary["min_ms"], 3)

    return dict(sorted(grouped.items()))


def _summarize_agent_events(
    agent_events: list[dict[str, Any]],
    *,
    key_name: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "success_count": 0,
            "failure_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_estimated_tokens": 0,
            "agents": defaultdict(
                lambda: {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_estimated_tokens": 0,
                }
            ),
        }
    )

    for event in agent_events:
        key = event.get(key_name) or "unknown"
        summary = grouped[key]
        summary["calls"] += 1
        if event["status"] == "success":
            summary["success_count"] += 1
        else:
            summary["failure_count"] += 1
        summary["prompt_tokens"] += event["prompt_tokens"]
        summary["completion_tokens"] += event["completion_tokens"]
        summary["total_estimated_tokens"] += event["total_estimated_tokens"]

        agent_summary = summary["agents"][event["agent_name"]]
        agent_summary["calls"] += 1
        agent_summary["prompt_tokens"] += event["prompt_tokens"]
        agent_summary["completion_tokens"] += event["completion_tokens"]
        agent_summary["total_estimated_tokens"] += event["total_estimated_tokens"]

    normalized: dict[str, dict[str, Any]] = {}
    for key, summary in grouped.items():
        normalized[key] = {
            "calls": summary["calls"],
            "success_count": summary["success_count"],
            "failure_count": summary["failure_count"],
            "prompt_tokens": summary["prompt_tokens"],
            "completion_tokens": summary["completion_tokens"],
            "total_estimated_tokens": summary["total_estimated_tokens"],
            "agents": dict(sorted(summary["agents"].items())),
        }

    return dict(sorted(normalized.items()))


def _serialize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json(indent=0)
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)
