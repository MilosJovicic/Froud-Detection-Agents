import inspect
from collections.abc import Callable

from contracts.branch import BranchSpec


def execution_params(
    spec: BranchSpec,
    target: Callable,
    *,
    excluded_names: set[str] | None = None,
) -> dict[str, float | int | str | bool]:
    excluded = {"session", "handle"}
    if excluded_names:
        excluded.update(excluded_names)

    signature = inspect.signature(target)
    accepted_names = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind is not inspect.Parameter.VAR_KEYWORD and name not in excluded
    }

    return {
        key: value
        for key, value in spec.params.items()
        if not key.startswith("__") and key in accepted_names
    }


def injected_failure_stage(spec: BranchSpec) -> str | None:
    value = spec.params.get("__inject_failure_stage")
    return value if isinstance(value, str) else None
