"""Protocol 2 between the shell and the worker: request envelopes and reply lines.

Each line is one UTF-8 JSON object. A request has a non-empty ``cmd`` and a
non-empty ``id``, and every worker line carries ``event`` and ``jobId``: the
request id for a reply, the job id for a job's lines, and null only when a
line had no usable id. A request that is not well formed gets
``error {code: "bad_request"}``. What is inside ``edit`` is not part of the
envelope, so a bad edit is refused as ``invalid_edit`` like any other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from mathimator_worker.jsonio import loads

PROTOCOL_VERSION = 2

# Every command and its top-level fields: name -> (what the value must be, whether it is required).
# Other fields are ignored.
REQUEST_FIELDS: dict[str, dict[str, tuple[str, bool]]] = {
    "status": {},
    "get_state": {},
    "edit": {"edit": ("an object", True)},
    "undo": {},
    "redo": {},
    "code": {},
    "clear_cache": {},
    "open_project": {"path": ("a string", True), "init": ("a boolean", False)},
    "reload": {},
    "overwrite": {},
    "save_as": {"path": ("a string", True)},
    "render_preview": {},
    "export": {"force": ("a boolean", False)},
    "cancel": {"jobId": ("a non-empty string", True)},
}

_KINDS: dict[str, Callable[[object], bool]] = {
    "an object": lambda value: isinstance(value, dict),
    "a string": lambda value: isinstance(value, str),
    "a non-empty string": lambda value: isinstance(value, str) and value != "",
    "a boolean": lambda value: isinstance(value, bool),
}


class BadRequest(ValueError):
    """A request line that is not well formed. ``job_id`` is its id when it has a usable one."""

    def __init__(self, message: str, job_id: str | None = None) -> None:
        super().__init__(message)
        self.job_id = job_id


@dataclass(frozen=True)
class Request:
    """A well-formed request. Its fields match ``REQUEST_FIELDS`` for its command."""

    id: str
    cmd: str
    fields: dict[str, object]

    def string(self, name: str) -> str:
        return cast(str, self.fields[name])

    def mapping(self, name: str) -> dict[str, object]:
        return cast(dict[str, object], self.fields[name])

    def flag(self, name: str) -> bool:
        """An optional boolean field, False when the request leaves it out."""
        return self.fields.get(name) is True


def parse_request(line: str) -> Request:
    """Read one request line, or raise BadRequest."""
    try:
        fields = loads(line)
    except (ValueError, RecursionError) as exc:
        raise BadRequest(f"invalid JSON: {exc}") from exc
    if not isinstance(fields, dict):
        raise BadRequest("a request must be a JSON object")
    request_id = fields.get("id")
    if not isinstance(request_id, str) or request_id == "":
        raise BadRequest("id must be a non-empty string")
    command = fields.get("cmd")
    if not isinstance(command, str) or command == "":
        raise BadRequest("cmd must be a non-empty string", request_id)
    expected = REQUEST_FIELDS.get(command)
    if expected is None:
        raise BadRequest(f"unknown command {command}", request_id)
    for name, (kind, required) in expected.items():
        if name not in fields:
            if required:
                raise BadRequest(f"{command} needs {name}", request_id)
        elif not _KINDS[kind](fields[name]):
            raise BadRequest(f"{name} must be {kind}", request_id)
    return Request(request_id, command, fields)


def rejected(job_id: str, code: str, message: str, field: str | None = None) -> dict[str, object]:
    """A refusal: nothing changed, and ``message`` is written for the user."""
    event: dict[str, object] = {"event": "rejected", "jobId": job_id, "code": code, "message": message}
    if field is not None:
        event["field"] = field
    return event


def error(job_id: str | None, code: str, message: str, details: str | None = None) -> dict[str, object]:
    """A failure: an internal error, a malformed line, or a job that failed or was cancelled."""
    event: dict[str, object] = {"event": "error", "jobId": job_id, "code": code, "message": message}
    if details is not None:
        event["details"] = details
    return event


def cancelled(job_id: str) -> dict[str, object]:
    """The terminal line of a cancelled job. Readers decide on the code; the message stays for older ones."""
    return error(job_id, "cancelled", "cancelled")


def internal(job_id: str | None, exc: BaseException) -> dict[str, object]:
    """The failure for an unexpected exception (D03). The traceback goes to worker.log."""
    return error(job_id, "internal", f"Internal error: {type(exc).__name__}: {exc}. Details are in worker.log.")
