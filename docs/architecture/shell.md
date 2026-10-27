# The shell

The shell is the Tauri 2 program in Rust, in
[`desktop/src-tauri`](../../desktop/src-tauri). It opens the window that hosts
the UI, starts and supervises the worker, forwards the UI's commands to it,
finds the bundled runtime, manages temp projects, checks that TeX works, draws
the equation glyph preview with Typst, and keeps the logs.

The shell never edits the document or renders a scene. Everything it learns
about the project comes from the worker's replies.

## Module map

| File | Role |
| --- | --- |
| [`src/main.rs`](../../desktop/src-tauri/src/main.rs) | Calls `mathimator::run()`; hides the console in release builds |
| [`src/lib.rs`](../../desktop/src-tauri/src/lib.rs) | App setup, every Tauri command, `AppStatus`, notices, the TeX status, dialogs, the leave warnings, `state.json` and the supervisor spawn |
| [`src/worker.rs`](../../desktop/src-tauri/src/worker.rs) | The worker transport: start and handshake, the request gate, routing stdout, exit and restart, and the temp projects it starts on |
| [`src/protocol.rs`](../../desktop/src-tauri/src/protocol.rs) | `PROTOCOL_VERSION`, the `Request` lines, `DialogReply` and `ShellError` |
| [`src/runtime.rs`](../../desktop/src-tauri/src/runtime.rs) | Finding Python, the worker, Typst, TinyTeX, the sample and the notices; the environment of every child |
| [`src/temp_project.rs`](../../desktop/src-tauri/src/temp_project.rs) | The temp-project marker, the touched check, pruning and the lock probe |
| [`src/tex.rs`](../../desktop/src-tauri/src/tex.rs) | The TeX self-test |
| [`src/equation.rs`](../../desktop/src-tauri/src/equation.rs) | The LaTeX-to-Typst translator and the glyph compile |
| [`src/win32_job.rs`](../../desktop/src-tauri/src/win32_job.rs) | The supervisor's kill-on-close Job Object |
| [`src/sha256.rs`](../../desktop/src-tauri/src/sha256.rs) | A small SHA-256 for the temp-project seed |
| [`tauri.conf.json`](../../desktop/src-tauri/tauri.conf.json) | The window, the Content Security Policy and the asset protocol |
| [`tauri.windows.conf.json`](../../desktop/src-tauri/tauri.windows.conf.json) | The NSIS installer, its resources and the `beforeBundleCommand` |
| [`capabilities/default.json`](../../desktop/src-tauri/capabilities/default.json) | The webview's permissions: `core:default` only |

## Startup

`run()` in [`lib.rs`](../../desktop/src-tauri/src/lib.rs) builds the app. Its
setup never fails: a missing runtime or a worker that cannot start leaves the
window open on "Worker unavailable" with the reason and Retry.

1. Pick the log folder and resolve the runtime ([below](#runtime-resolution)).
2. Set the TeX status: failed from the start when the runtime blocks TeX,
   otherwise `checking`.
3. Build the `Shell` (events, asset scope, `state.json`, logs, notices) and a
   spawner for the supervisor.
4. Call `Worker::launch`, which starts the first worker in the background: on
   the last project when that folder still holds `project.json`, otherwise on
   a new temp project with `--init`.
5. Register the glyph preview, the runtime, the worker, the shell and the
   dialog guard as Tauri state.
6. In the background, once the launch handshake is done, run the TeX
   self-test and prune old temp projects.

A page load resets the glyph preview's generations, because a reloaded UI
counts from 1 again. A window close request is held, and `confirm_quit` asks
about a touched temp project before it closes the window.

## Tauri commands

Each command is a function in [`lib.rs`](../../desktop/src-tauri/src/lib.rs) (most are `async fn`),
registered through the `commands!` macro, which also records the list for a
test that compares it with
[`tests/fixtures/protocol/commands.json`](../../tests/fixtures/protocol/commands.json).
A worker reply comes back unchanged as the command's value, so a `rejected`
line is a value, not an error. The shell's own failures are `ShellError`s,
`{code, message}`, with the codes `worker_unavailable`, `worker_exited`,
`timeout`, `io` and `bad_argument`.

| Command | Arguments | Worker request | Timeout |
| --- | --- | --- | --- |
| `app_status` | | none; waits for the launch handshake | |
| `restart_worker` | | starts a worker when it is unavailable | |
| `dismiss_notice` | `id` | none | |
| `reserve_job_id` | `prefix`: `preview` or `export` | none | |
| `get_state` | | `get_state` | 30 s |
| `edit_document` | `edit` | `edit` | 30 s |
| `undo_document`, `redo_document` | | `undo`, `redo` | 30 s |
| `clear_cache` | | `clear_cache` | 120 s |
| `reload_project`, `overwrite_project` | | `reload`, `overwrite` | 30 s |
| `new_project` | | the leave warning, then `open_project` with `init` on a new temp folder | 30 s |
| `open_sample` | | the leave warning, then `open_project` on a temp copy of the sample | 30 s |
| `open_project_dialog` | | the leave warning, a folder picker, then `open_project` | 30 s |
| `save_project_as_dialog` | | a folder picker, then `save_as` | 600 s |
| `export_code_dialog` | | `code`, then a save picker; the shell writes the file | 30 s |
| `render_preview` | `jobId` | `render_preview`, sent only | |
| `export_movie` | `jobId`, `force` | `export`, sent only | |
| `cancel_render` | `jobId` | `cancel`, sent only | |
| `compile_equation_preview` | `latex`, `slot`, `generation` | none; runs Typst | 10 s |
| `cancel_equation_preview` | `slot`, `generation` | none | |
| `note_shell_ready` | | none; writes the ready marker when `MATHIMATOR_SHELL_READY_FILE` is set | |
| `third_party_notices` | | none; reads `THIRD_PARTY_NOTICES.txt` | |
| `open_project_path` | `path` | `open_project`; in debug builds, and in release builds only with the `test-hooks` feature | 30 s |

The dialog commands (New, Sample, Open, Save As and Export code) take one turn
of the `Dialogs` guard. A dialog command that arrives while another holds the
turn returns `{cancelled: true}`, as does a cancelled picker or a Cancel in
the leave warning. File dialogs are parented to the window, except on Linux,
so they are modal.

## The worker transport

[`Worker`](../../desktop/src-tauri/src/worker.rs) is the shell's handle on the
worker process. Every worker-bound command calls one of its methods.

### The request gate

The gate is a fair async mutex. Its holder owns the worker: only the holder
writes requests and starts or ends the process. Because the lock is fair,
requests are written in the order the commands were called, and the worker
answers them in that order. The work under the gate blocks on pipes and
replies, so it runs on Tauri's blocking thread pool, never on the UI thread.

The launch itself holds the gate until the first worker has passed its
handshake, so every command waits for the launch and nothing reaches a worker
that has not finished its handshake. A second lock, `launch`, is held for
writing for the same time. `app_status` waits on that one instead, so it waits
for the launch but not for a long request in flight.

The gate's status is one of:

- **Stopped**: no worker runs yet, or the last one was ended. The next
  command starts one.
- **Running**: a worker of one *generation*, with its stdin and process.
- **Unavailable**: commands fail with `worker_unavailable` and the reason,
  until Retry (`restart_worker`) clears it.

### Requests and replies

Every request and job id comes from one counter: `status-N`, `rpc-N`,
`cancel-N`, `tex-N`, `preview-N` and `export-N`. An RPC registers a pending
entry under its id, writes its line, and waits for the reply with its timeout.

Each worker generation has a router thread that reads stdout line by line
(`Shared::route` and `dispatch`):

- A line that is not JSON is logged to `shell.log` and dropped.
- A `state` line's `projectPath` is taken as the bound folder. When it differs
  from the last one seen, the shell widens the asset protocol's scope to that
  folder and writes it to `state.json` as the last project.
- A line whose `jobId` matches a pending request goes to that request only.
- Every other line is broadcast to the UI as a `worker-event`: job events, a
  cancel's `no_such_job` refusal, errors with a null `jobId`, and replies that
  arrive after their request timed out.

A request that times out still runs in the worker. The shell drops its pending
entry, so the late reply is broadcast, and the UI resyncs with `get_state`.

### Exit, crash and restart

When the router sees end of file and the shell did not ask the worker to stop,
it treats the exit as a crash: it fails every pending request of that
generation with `worker_exited`, logs the exit code, sends the `worker-event`
`worker_exited {message, exitCode}`, and ends the old process for good, which
closes its Job Object and so ends its Manim as well.

The next command starts a new worker on the bound folder, without `--init`.
No worker starts while the old one is still alive: a worker still running 5 s
after it was ended is kept as "stuck", and the app shows "Worker unavailable"
until Retry ends it again.

If a new worker reports a `startupError` (the project is gone, locked, or no
longer valid), `recover` opens a new temp project instead and adds a notice
that says why the old one could not be reopened. A new temp folder that no
worker took is deleted, and only `shell.log` mentions it.

### The handshake

Every worker starts the same way. The shell spawns it, sends `status`, and
waits up to 30 s for `ready`. `check_ready` refuses a worker whose `protocol`
is missing or is not `PROTOCOL_VERSION` (2), with a message that asks for a
reinstall, or in a debug build for a restart of the dev build. A worker that
exits or does not answer in time is reported with its exit code and a pointer
to `supervisor.err`.

### Spawning the supervisor

`spawn_supervisor` in [`lib.rs`](../../desktop/src-tauri/src/lib.rs) runs
`runtime.python_command(files, "mathimator_worker")` with `--project`,
`--python` and `--init` when it is a new temp folder. Before the spawn it sets
`MATHIMATOR_TEX_ERROR` when TeX is blocked and removes it otherwise. It pipes
stdin, stdout and stderr, puts the child in a kill-on-close Job Object
([`win32_job.rs`](../../desktop/src-tauri/src/win32_job.rs)), and copies
stderr into `supervisor.err` byte by byte under a header for each start, so a
crash keeps its traceback.

Ending the worker drops the Job Object, which kills the supervisor and every
process it started. Without a job, as on Linux or with
`MATHIMATOR_WIN32_JOBS=0` in a debug build, the child is killed directly.

## Runtime resolution

[`runtime::resolve`](../../desktop/src-tauri/src/runtime.rs) finds what the
shell runs. Each part is resolved on its own, so a missing piece disables only
what needs it.

| Part | Debug build | Release build |
| --- | --- | --- |
| Python | `MATHIMATOR_PYTHON`, else the repository's `.venv` | `python` in `runtime.json` |
| Worker source | `worker/` in the repository | `worker` in `runtime.json` |
| Sample | `tests/fixtures/reference-60/project.json` | `sample` in `runtime.json` |
| Typst | `MATHIMATOR_TYPST`, else `packaging/bundle` when built, else `typst` on PATH | `typst` in `runtime.json` |
| TinyTeX | `MATHIMATOR_TINYTEX_BIN`, else `packaging/bundle` when built, else the TeX on PATH | `tinytexBin` in `runtime.json` |
| Notices | `packaging/bundle/THIRD_PARTY_NOTICES.txt` | `THIRD_PARTY_NOTICES.txt` in the bundle |
| Working folder | The repository | The app's local data folder |

A release build reads only `<resources>/runtime/runtime.json`. Its `platform`
must be this machine's, such as `windows-x86_64`, and `python`, `worker` and
`sample` are required: a missing one makes the worker unavailable with "The
installed runtime is incomplete (<field>). Reinstall Mathimator." A missing
`typst` disables only the glyph preview, and a missing `tinytexBin` blocks
formulas and axis labels. `shell.log` gets the whole `runtime.json` on one
line.

`Runtime::command` gives every child the same environment: no console window,
the fixed working folder, TinyTeX's `bin` first on PATH, and none of the
inherited `PYTHON*`, `TEXINPUTS` or `TEXMF*` variables. A release build also
drops every inherited `MATHIMATOR_*` variable, so no development switch reaches
an installed worker, and names each one it ignored in `shell.log`; the only one
it reads itself is `MATHIMATOR_SHELL_READY_FILE`. `python_command` adds `-P`,
`PYTHONPATH` set to the worker source, and unbuffered UTF-8 stdio.

In a debug build on Windows, the venv's `python.exe` is a launcher that starts
the real interpreter as its child. The shell reads the venv's `pyvenv.cfg` and
starts the real interpreter directly, with `__PYVENV_LAUNCHER__` naming the
launcher, so the process in the Job Object is the worker itself.

## Temp projects and the project lock

The shell makes temp projects for the launch project (when there is no last
project to reopen), for New, and for each Sample copy
([`worker.rs`](../../desktop/src-tauri/src/worker.rs) and
[`temp_project.rs`](../../desktop/src-tauri/src/temp_project.rs)):

- Each is a folder `%TEMP%\mathimator-<pid>-<ms>`, where `<ms>` counts up
  until the name is free.
- Once the worker has bound and locked it, the shell writes the marker
  `.mathimator\temp-project.json`: `{"kind": "mathimator-temp", "seedSha256",
  "createdMs"}`. The seed is the SHA-256 of the `project.json` it started with.
- A temp project is **touched** when its `project.json` no longer hashes to the
  seed or its `exports` folder holds a file. An unmarked temp folder counts as
  touched.
- Before Open, Sample, New and quitting, `leave` asks about a touched temp
  project, with Save As…, Don't Save (or Quit) and Cancel. An untouched one is
  left without a question.
- A Save As away from a marked temp project deletes the old folder, so it is a
  move. Any other old folder is kept.

**Pruning** deletes `mathimator-<pid>-<ms>` folders in `%TEMP%` that are at
least 24 hours old, are neither the bound project nor a last project no worker
has reopened yet, are not locked, and have no file in `exports`. A marked
folder must also still hash to its seed. Each deletion goes to `shell.log`.
Other folders, such as the TeX self-test's, are never touched, and pruning
never follows a link.

To tell whether a worker holds a folder's lock, the shell on Windows reads
the first byte of `.mathimator\lock`, which Windows refuses while the worker
has it locked (`is_locked`). It takes no lock itself, because a probe that took
the lock, however briefly, could make a worker that binds the folder at that
moment fail with `project_locked`. Elsewhere `flock` cannot be tested without
being taken, so the shell takes the lock and releases it at once.

The last project is kept in `%APPDATA%\com.mathimator.desktop\state.json` as
`{"lastProject": "<folder>"}`, written atomically whenever a `state` line names
a new folder, temp projects included.

## The TeX self-test

At every launch where TeX is not blocked already, the shell runs
[`tex::self_test`](../../desktop/src-tauri/src/tex.rs) in the background once
the launch handshake is done. In a fresh `%TEMP%\mathimator-texcheck-<pid>`
folder it runs `latex` on a one-line formula with `amsmath` and `amssymb`,
then `dvisvgm --no-fonts`, with the worker's environment, hidden consoles and
30 s in all.

The result becomes the TeX status. The shell logs it, sends the `worker-event`
`tex_status {state, message}`, and sends `set_tex_error` to the running worker
with the failure message, or null when TeX works. Every worker started later
gets the same message in `MATHIMATOR_TEX_ERROR`. While TeX is blocked, the
worker refuses jobs whose scene needs LaTeX with `tex_unavailable`; nothing
running is stopped, and the document keeps its undo history.

## The glyph preview

The equation editor shows a live Typst glyph of the formula being edited
([`equation.rs`](../../desktop/src-tauri/src/equation.rs)). The scene and every
export still use LaTeX; the glyph is an approximation.

1. `translate` turns the supported LaTeX subset into Typst math: a handful of
   function and Greek-letter names, `\frac`, `\sqrt`, superscripts,
   subscripts, operators, brackets, letters and digits. Anything else, such as
   `\int`, gives `unsupported`, and the editor says "No live preview for this
   formula." The worker's [`equation.py`](../../worker/mathimator_worker/equation.py)
   is the reference implementation; both are tested against
   [`tests/fixtures/equation/translate-cases.json`](../../tests/fixtures/equation/translate-cases.json).
2. Each request names a `slot`, the formula's object id, and a `generation`
   from one counter in the UI. The `Gate` keeps the newest generation per slot.
   An older compile or cancel comes back `cancelled` at once, and a newer one
   makes the running compile stale, which ends its Typst.
3. Before the first compile, the shell runs `typst --version` hidden and checks
   that it is the pinned version.
4. The compile writes `formula-<slot>-<generation>.typ` in
   `.mathimator/equation/`, creating that folder inside the project folder only
   and refusing a link in its place, and runs
   `typst compile --ignore-system-fonts` there with bare file names and a 10 s
   limit.
5. If the compile is still the newest for its slot, it deletes the slot's
   other files and returns `ok` with the SVG path, which the UI reads through
   the asset protocol. A Typst failure returns `error` with its message.

## Logs and notices

`shell.log` and `supervisor.err` are in `out/logs` in a debug build, and in
`%LOCALAPPDATA%\com.mathimator.desktop\logs` when installed (or
`%TEMP%\Mathimator\logs` when Tauri cannot name that folder). `shell.log`
records the runtime, each spawn and exit with its exit code, protocol
mismatches, Job Object failures, the TeX self-test, deletions by pruning and
Save As, ignored `MATHIMATOR_*` variables, and worker lines that are not JSON.
`supervisor.err` appends the worker's stderr under a header for each start,
and moves to `supervisor.err.1` above 1 MiB. The worker's own `worker.log`
stays in the project.

Notices are short messages for the user, such as "Could not reopen <folder>
...". The shell keeps the open ones, skips a notice whose text is already open,
sends each new one as a `notice {id, message}` event, and drops it when the UI
calls `dismiss_notice`. `app_status` returns the open notices together with the
worker state and the TeX status, so a UI that loads late still sees them.

## The webview's reach

The webview's capability grants Tauri's `core:default` and nothing more. The
Content Security Policy in [`tauri.conf.json`](../../desktop/src-tauri/tauri.conf.json)
limits the page to the app itself, the IPC channel and the asset protocol. The
asset scope starts empty and grows by each project folder a `state` line names,
and is never narrowed during a session. The UI fetches previews and glyphs over
the asset protocol and shows them from `blob:` URLs.
