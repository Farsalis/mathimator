# The protocol

The shell and the worker talk in **protocol 2**: one JSON object per line, in
UTF-8, over the worker's stdin and stdout. This page is a readable tour.

The normative definition is
[section 6 of the design spec](../design-spec.md#6-preview-and-re-render-strategy)
("Protocol" and "Jobs"), which lists every command, field, event, refusal code
and message. The machine-readable lists are the fixtures in
[`tests/fixtures/protocol`](../../tests/fixtures/protocol), which the worker's,
the shell's and the UI's tests all check. When this page and the spec differ,
the spec is right.

The two ends are [`worker/mathimator_worker/protocol.py`](../../worker/mathimator_worker/protocol.py)
with the handlers in [`supervisor.py`](../../worker/mathimator_worker/supervisor.py),
and [`desktop/src-tauri/src/protocol.rs`](../../desktop/src-tauri/src/protocol.rs)
with the transport in [`worker.rs`](../../desktop/src-tauri/src/worker.rs).
Both define `PROTOCOL_VERSION = 2`.

## Lines

A **request** has a `cmd` and an `id`, a non-empty string unique within one
worker process. The shell takes every id from one counter, and the prefix
tells what it is: `status-N`, `rpc-N`, `cancel-N`, `tex-N`, `preview-N` or
`export-N`. Other request fields depend on the command; unknown ones are
ignored.

Every line the worker writes has an `event` and a `jobId`. For a reply,
`jobId` is the request's id. For a job's lines, it is the job's id. It is null
only for a line that had no usable id, such as a malformed request.

Numbers are finite. `NaN` and `Infinity` make a line invalid JSON, and the
worker never writes them: a line it cannot serialize goes out as
`error internal` for the same id instead, so the shell can always parse what it
reads.

The worker's stderr is not part of the protocol. The shell appends it to
`supervisor.err`.

## A session, line by line

These lines come from
[`tests/fixtures/protocol/replies.json`](../../tests/fixtures/protocol/replies.json),
which records what a real worker wrote for the requests in
[`requests.json`](../../tests/fixtures/protocol/requests.json). `→` is a line
the shell writes, `←` one the worker writes, and `state` replies are shortened.

```text
→ {"id": "status-1", "cmd": "status"}
← {"event": "ready", "jobId": "status-1", "protocol": 2, "startupError": null}

→ {"id": "rpc-3", "cmd": "edit", "edit": {"op": "add_formula", "afterBeatId": null}}
← {"event": "state", "jobId": "rpc-3", "projectPath": "...", "document": {...}, "derived": {...},
   "index": {...}, "source": "...", "changed": true, "focus": {"beatId": "beat_1", "objectId": "formula_1"}}

→ {"id": "rpc-4", "cmd": "edit", "edit": {"op": "set_latex", "objectId": "formula_1", "latex": "\\csname x\\endcsname"}}
← {"event": "rejected", "jobId": "rpc-4", "code": "untrusted_latex",
   "message": "latex uses \\csname, which formulas cannot use.", "field": "latex"}

→ {"id": "tex-10", "cmd": "set_tex_error", "message": null}

→ {"id": "preview-11", "cmd": "render_preview"}
← {"event": "started", "jobId": "preview-11"}
← {"event": "beat_ready", "jobId": "preview-11", "beatIndex": 0, "path": ".../partial_movie_files/MainScene/<hash>.mp4"}
← {"event": "beat_ready", "jobId": "preview-11", "beatIndex": 1, "path": ".../partial_movie_files/MainScene/<hash>.mp4"}
← {"event": "done", "jobId": "preview-11", "path": ".../videos/scene/480p15/MainScene.mp4"}

→ {"id": "export-12", "cmd": "export", "force": false}
← {"event": "started", "jobId": "export-12"}
← {"event": "done", "jobId": "export-12", "path": ".../.mathimator/exports/<key16>/scene.mp4", "current": true}

→ {"id": "export-13", "cmd": "export", "force": false}
← {"event": "done", "jobId": "export-13", "path": ".../.mathimator/exports/<key16>/scene.mp4", "current": true}

→ {"id": "cancel-14", "cmd": "cancel", "jobId": "preview-999"}
← {"event": "rejected", "jobId": "cancel-14", "code": "no_such_job", "message": "No render with that id is running."}

→ {"cmd": "status"}
← {"event": "error", "jobId": null, "code": "bad_request", "message": "id must be a non-empty string"}
```

The second export finds its key already published and answers at once,
without `started`.

## Three kinds of answer

| Class | Line | Meaning |
| --- | --- | --- |
| Success | The command's result: `ready`, `state` or `code`, or a job's `done` | Done |
| Refusal | `rejected {code, message, field?}` | Refused before anything changed. `message` is written for the user, and `field` names the value at fault when there is one |
| Failure | `error {code, message, details?}` | An internal failure, a malformed request, or a job that failed or was cancelled. `details` holds more lines, such as the end of Manim's output |

A request whose answer the shell waits for (an RPC) gets exactly one reply
line, written before the worker reads its next request. So replies arrive in
request order.

## Commands

| `cmd` | Kind | Fields | Success |
| --- | --- | --- | --- |
| `status` | RPC | | `ready {protocol, startupError}` |
| `get_state` | RPC | | `state` |
| `edit` | RPC | `edit`: an object with an `op` | `state` with `changed` and `focus` |
| `undo`, `redo` | RPC | | `state` |
| `code` | RPC | | `code {source}` |
| `clear_cache` | RPC | | `state`, with `warning` when a file could not be removed |
| `open_project` | RPC | `path`; `init`, default false | `state` |
| `reload`, `overwrite` | RPC | | `state` |
| `save_as` | RPC | `path` | `state` |
| `render_preview` | Job | | job lines |
| `export` | Job | `force`, default false | job lines |
| `cancel` | Send | `jobId` | no line; the job ends with `error cancelled` |
| `set_tex_error` | Send | `message`: a string, or null when TeX works | no line |

The envelope of each command (which fields it needs and their types) is
`REQUEST_FIELDS` in [`protocol.py`](../../worker/mathimator_worker/protocol.py)
and `workerRequests` in
[`commands.json`](../../tests/fixtures/protocol/commands.json). A request that
breaks it gets `error bad_request`. What is inside `edit` is a document
matter: an unknown `op` or a bad edit field gets `rejected invalid_edit` or a
more specific refusal code.

The edit operations, their fields and an accepted example of each are in
[`edit-ops.json`](../../tests/fixtures/protocol/edit-ops.json). They cover
renaming, the fields of each object type, the template inserts,
`add_formula`, `add_text` and `add_shape`, `delete_object`, the 3D camera and
mode, `set_export_profile`, and the beat operations `add_beat`,
`set_beat_action`, `set_beat_targets`, `set_beat_param`, `set_beat_easing`,
`set_beat_duration`, `move_beat` and `delete_beat`.

There is no scrub command and no frame command: playback belongs to the UI.

## The state reply

Every command that changes or reads the document answers with the whole
`state`:

```text
{
  "event": "state",
  "jobId": "rpc-7",
  "projectPath": "<the bound folder>",
  "document": { ...project.json as an object... },
  "derived": {
    "starts": [0, 2, 4],          // each beat's derived start
    "total": 5,
    "dirty": [false, true, true], // per beat, for the preview profile
    "canUndo": true, "canRedo": false,
    "onScreen": [[], ["axes_1"], ["axes_1", "plot_1"], [...]]   // before each beat, then after the last
  },
  "index": null | {
    "output": "<the full preview movie>" | null,
    "beats": [{"id", "index", "derivedStart", "duration", "partial", "lastGood", "fresh"}]
  },
  "source": "<the generated scene.py>" | null,
  "sourceError": "<why there is no source>",           // only when source is null
  "focus": {"beatId": ..., "objectId": ...},           // edit replies only
  "changed": true,                                     // edit replies only
  "warning": {"code": "files_in_use", "message": ...}  // clear_cache only
}
```

Sending the whole state each time keeps the UI simple: it replaces its copy
and never merges. Every path in it is the bound folder's long path joined with
a path inside that folder, so the webview can read it through the asset
protocol, whose scope covers each `projectPath` the shell has seen.

## Jobs

`render_preview` and `export` start jobs. They get no reply line. All of a
job's lines carry the job's id, in this order:

1. At most one `queued {code: "stopping_previous", message}`, when the job has
   to wait because the render before it did not stop within a second.
2. At most one `started`, when Manim starts. A job that ends without Manim (a
   refusal, a cancelled held job, or an export found in the export cache)
   sends none.
3. For a preview, zero or more `beat_ready {beatIndex, path}`, each index at
   most once, in increasing order. Exports send none.
4. Exactly one terminal line: `done`, `error` or `rejected`. Nothing follows
   it.

A preview's `done.path` is the full preview movie. An export's `done.path` is
the published file or folder in `.mathimator/exports/<key16>/`, and
`done.current` says whether the export key captured when the export started is
still the current document's.

Before it starts Manim, a job checks, in order: a project is bound; the folder
and `project.json` are as the worker last left them; there is no link where the
job writes; the scene generates; TeX is available when the scene needs it; the
project path can render; and, for an export without `force`, whether the
export is already published. Each failed check ends the job with its refusal.

The shell hands out the ids (`reserve_job_id`), and the UI records an id as its
current preview or export before it sends the job, so it never gets an event
for an id it does not know.

## Codes

The refusal codes fall into groups:

- **No project or a changed folder**: `no_project`, `not_a_project`,
  `project_unavailable`, `read_failed`, `project_locked`, `lock_failed`,
  `linked_folder`, `project_changed`, `save_failed`.
- **The document**: `not_utf8`, `invalid_json`, `invalid_text`,
  `untrusted_latex`, `unsupported_expression`, `limit_exceeded`,
  `invalid_document`.
- **An edit**: `invalid_edit` (and the document codes above, for a value the
  edit sets), `nothing_to_undo`, `nothing_to_redo`.
- **Jobs and the queue**: `export_running`, `render_in_progress`,
  `tex_unavailable`, `path_unsupported`, `save_as_failed`, `no_such_job`.

The failure codes are `cancelled`, `render_failed`, `publish_failed`,
`internal` and `bad_request`. The UI decides on the code. The one exception
is Render again, which the UI offers when a preview's `render_failed` message
says the incomplete clip was removed. Otherwise the messages are for the user.
The spec's tables give each code's message and when it is sent.

## The shell's side

The shell adds three things around the worker's protocol:

- **The handshake.** It sends `status` to every worker it starts and refuses a
  `ready` whose `protocol` is not 2. `ready.startupError` tells it the worker
  could not bind its folder.
- **Its own errors.** A Tauri command that cannot get an answer rejects with
  `{code, message}` and one of `worker_unavailable`, `worker_exited`,
  `timeout`, `io` or `bad_argument`.
- **Its own events.** The `worker-event` channel carries, besides the worker
  lines no request claimed, `worker_exited {message, exitCode}`,
  `notice {id, message}` and `tex_status {state, message}`, all with
  `jobId: null`.

Within protocol 2, the shell and the UI ignore unknown fields and unknown
events, so the worker can add information without breaking them.

## The fixtures

| Fixture | Holds | Checked by |
| --- | --- | --- |
| [`commands.json`](../../tests/fixtures/protocol/commands.json) | Every worker request with its field types, and every Tauri command with its arguments, the request it sends and whether only debug and `test-hooks` builds have it | Python, Rust and TypeScript tests |
| [`requests.json`](../../tests/fixtures/protocol/requests.json) | One request line per worker command, in the shell's id order, with what each reply must hold | Python tests (in process and on the shell's spawn line) and Rust tests (built from the shell's `Request` type) |
| [`replies.json`](../../tests/fixtures/protocol/replies.json) | The lines a real worker wrote for `requests.json` | TypeScript tests, which parse them as the UI would |
| [`edit-ops.json`](../../tests/fixtures/protocol/edit-ops.json) | Every edit op, its field types and accepted examples | Python tests (each example is one undo step, also through a real `edit` request) and TypeScript tests (each builder in `edits.ts`) |
| [`export-choices.json`](../../tests/fixtures/protocol/export-choices.json) | Export formats, sizes, frame rates, opaque-format messages, and the beat and timeline limits | Python and TypeScript tests |
| [`beat-kinds.json`](../../tests/fixtures/protocol/beat-kinds.json) | The beat catalog, easings and shapes from `beats.py` | Python and TypeScript tests |

`replies.json` and `beat-kinds.json` are generated: rewrite them with
`uv run pytest tests/test_shell_fixture.py --update-golden` and
`uv run pytest tests/test_beats.py --update-golden`.

## Changing the protocol

A change to a command or an event touches both ends and their fixtures:

1. The worker: `REQUEST_FIELDS` and the handler in `supervisor.py`, or the
   edit function in `session.py` and its entry in `_EDITS`.
2. The shell: `Request` in `protocol.rs`, the `Worker` method in `worker.rs`,
   and the Tauri command in `lib.rs` when the UI needs a new one.
3. The UI: `commands.ts`, the parser in `types.ts`, and the builder in
   `edits.ts` for an edit op.
4. The fixtures: `commands.json`, `requests.json` and `edit-ops.json` by hand,
   then regenerate `replies.json`.
5. Section 6 of the spec.

An incompatible change also bumps `PROTOCOL_VERSION` in both `protocol.py` and
`protocol.rs`, so a mismatched worker is refused at the handshake.
