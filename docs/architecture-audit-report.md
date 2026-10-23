# Mathimator audit report

Repository: `C:/Users/scfre/CursorProjects/Cloud/Mathimator`. Work branch: `audit/cleanup-gitlab-devops` at `8e49fbd`. `main` and `cursor/windows-audit-fa22` point at the same commit, so every file:line in this report holds on all three branches. The only remote is `origin` at `origin.cursor.com`. No GitLab remote is configured yet.

Scope: 209 verified findings. 1 critical, 7 high, 64 medium, 120 low, 17 info. 191 are confirmed and 18 plausible; refuted findings were dropped, and none are disputed or unverified. Nothing has been changed yet. Every behaviour change waits on the decisions in section 7.

Audit footprint (be precise about this): no file content was changed. An msys `grep` crash during the audit left two untracked files: `grep.exe.stackdump` in the repo root and `packaging/bundle/grep.exe.stackdump`. The second one would have shipped inside the installer. Both were deleted after the audit. `desktop/src-tauri/src/lib.rs` had its mtime touched to force a clippy re-run; its content is unchanged. `.cursor/` was already untracked before the audit.

---

## 1. Architecture overview

Three processes, two IPC hops, plus a per-keystroke glyph path that the shell owns.

```
+------------------------------+   invoke() (sync commands on the UI thread)
| React 19 UI  (desktop/src)   |-------------------------------------------+
|  App, useEditor (state),     |<--- listen("worker-event"): every         |
|  jobSession (job handshake), |     supervisor stdout line                |
|  playback, PreviewStage,     |                                           |
|  Timeline, Inspector,        |---- asset:// fetch -> Blob URL ---------+ |
|  FormulaPreview              |                                         | |
+------------------------------+                                         | |
                                                                         v |
+----------------------------------------------+     <project>/.mathimator/ |
| Rust shell (desktop/src-tauri, Tauri 2.12.5) |<--------------------------+
|  lib.rs  Worker: spawn, call() 30 s timeout, |
|          lazy respawn, read_stdout routing,  |
|          asset-scope grants                  |
|  equation.rs GlyphPreview  win32_job.rs      |
+-------+-------------------------------+------+
        | stdin / stdout JSON lines     | per debounced keystroke
        v                               v
+------------------------------+   python -m mathimator_worker.equation -> formula-N.typ
| Python supervisor (worker/)  |   typst compile                         -> formula-N.svg
|  Supervisor: protocol, locks |
|  Session: document + undo    |
|  codegen -> scene.py         |
|  argv -> Manim command       |
|  progress -> beat_ready      |
+--------------+---------------+
               | Popen + per-render kill-on-close Job Object (Windows)
               v
+------------------------------+
| python -m manim render       |
|  Cairo, PyAV/FFmpeg encode,  |
|  latex + dvisvgm (TinyTeX)   |
+------------------------------+
```

### Processes and threads

- **UI (WebView2).** One React tree with no router and no store. `useEditor.ts` holds about 18 `useState` slots and 6 refs. `jobSession.ts` is a pure reducer over a `Handshake` record. Its result is copied by hand into seven separate React states.
- **Shell (Rust).** 18 commands. 16 are synchronous and run on the UI thread. `Worker::call` blocks for up to 30 s. A `read_stdout` thread hands a reply to the waiting call when its `jobId` matches, and also broadcasts every line as `worker-event`. A `drain_stderr` thread writes `supervisor.err` at EOF. The supervisor runs inside a kill-on-close Job Object.
- **Supervisor (stdlib-only Python).** The main thread reads stdin and runs document commands under `_doc_lock`. Render arbitration uses `_state_lock` over `_current` and `_pending_preview`: one job thread per render, two reader threads per Manim run. A new preview cancels a running preview. An export cancels a preview. A preview that arrives during an export waits.
- **Manim child.** `python -m manim render` with `--media_dir <project>/.mathimator/cache/media`. On Windows it is placed in a ctypes Job Object. If that fails, a PowerShell/C# process-table walk is the fallback.

### Data flow from project.json to rendered output

1. **Load.** `project.json` goes through `document.load_text`: `json.loads`, then `_reject_start`, then strict `_parse_*` (unknown keys and per-type params are refused), then `_rewrite_templates` (template params win over hand edits), then `validate_project` (ids, the 0.1 s and 120 s limits, scene kind, timeline entrances, template wiring). The result is held by `Session`.
2. **Edit.** UI `commit` calls `edit_document`, which reaches `Worker::call` and then `supervisor._edit`. `Session.edit` dumps the document, reloads it, applies the edit, dumps and reloads again, and pushes the old Project onto the 100-deep undo stack. The supervisor then saves atomically (tmp file plus `os.replace`), rebuilds the preview index and emits a `state` event (document, derived starts, dirty flags, index, generated code). The UI sets state and schedules a preview after a 300 ms debounce.
3. **Preview.** `reserve_job_id`, then `render_preview`. The job thread runs `_prepare_job_locked`: `codegen.generate` writes `.mathimator/codegen/scene.py`, `argv.build_manim_argv` builds the command, the stamp and keys are captured, and last-good copies are made. It then starts Manim. Reader threads feed `ManimProgress`, which emits `beat_ready`; `_note_partial` updates `preview-index.json`. When Manim exits, the supervisor reads the manifest, marks the beats rendered, and emits `state` and `done`.
4. **Export.** A `set_export_profile` edit, then `export`. A cache hit on `exports/<key16>/export.json` returns at once. Otherwise Manim renders at the export profile, and the artifact is copied into `exports/<key16>/` with `export.json`.
5. **Playback.** `playback.ts` `playbackAt` picks the full movie, a beat partial, a last-good copy or blank. `PreviewStage` fetches the file through the asset protocol into a Blob URL.
6. **Cache identity.** `scene_stamp` covers background, camera, seed and versions. Per-beat keys cover the beat, the objects it mentions plus their axes, the profile, the index and the Manim version. Both are hashed over canonical JSON with numbers written to 3 decimals. `export_key` adds the export profile. Dirty flags mark every beat from the first key mismatch onward.

### Project folder

```
<project>/
  project.json
  .mathimator/
    codegen/scene.py                 regenerated per render
    cache/media/                     Manim media_dir: videos/, Tex/, images/
    cache/rendered.json              rendered stamps and keys
    cache/preview-index.json         per-beat partial / last-good / output
    cache/last-good/<beatId>.mp4
    exports/<key16>/                 artifact + export.json (kept by Clear cache)
    equation/formula-<gen>.typ/.svg  spec says typst/
    logs/worker.log
```

### Module responsibilities

| Area | Module | Responsibility |
|---|---|---|
| Worker model | `document.py` (1492 lines) | Schema tables, dataclasses, strict parser, template sync, validation, save form, cache identity, id allocation |
| | `session.py` | Session, undo/redo, dirty flags, state payload, 26 edit ops |
| | `expr.py` | Allowlisted expression grammar to AST to `np.*` source (user text is never evaluated) |
| | `templates.py` | Inserts axes_function, formula_transform, three_d_axes, formula, text |
| | `jsonio.py` | Stable save bytes; canonical 3-decimal hash form |
| | `equation.py` | LaTeX subset to Typst (CLI used by the shell) |
| Worker codegen | `codegen.py`, `tex_failure.py` | Project to `scene.py`, one play or wait per beat; classify failed runs |
| Worker runtime | `supervisor.py` (about 1160 lines), `argv.py`, `progress.py`, `scrub.py` | Protocol, job queue, Manim lifecycle, preview index, export publish; command line; log parsing and discard; scrub (tests only) |
| Process control | `win32_job.py`, `process_group.py`, `win32_job.rs` | Job Objects (ctypes and Rust), fallback tree kill |
| Desktop UI | `useEditor.ts`, `jobSession.ts`, `playback.ts`, `components/*` | State, job handshake, playback choice, editors |
| Desktop shell | `lib.rs`, `equation.rs` | Runtime resolution, spawning, RPC bridge, commands, asset scope; glyph preview |
| Packaging | `packaging/build-bundle.ps1/.sh`, `tauri.windows.conf.json` | Offline runtime bundle; unsigned NSIS installer |

What is strong: codegen and the expression path are injection-safe (allowlist parser, raw/`repr`/`json.dumps` literals, regex-checked ids and colours). Journey A and reference-60 match byte for byte. The cache identity design is sound. Lockfiles are committed with integrity hashes.

---

## 2. Health summary

| Subsystem | Crit | High | Med | Low | Info | Total | Verdict |
|---|---|---|---|---|---|---|---|
| Worker model (document, session, expr, templates, equation, jsonio) | 0 | 0 | 8 | 12 | 2 | 22 | Sound core. Non-`DocumentError` exceptions escape at the input boundary, numbers are rounded silently, and a few timeline and template rules have gaps. |
| Worker codegen (codegen, tex_failure) | 0 | 0 | 3 | 5 | 0 | 8 | Correct and injection-safe. TeX error classification is wrong and durations drift from frame counts. |
| Worker runtime (supervisor, progress, scrub, argv) | 0 | 4 | 20 | 24 | 0 | 48 | Weakest area. No exception boundary, log parsing that never matches, cache poisoning after interrupted runs, unsafe handling of project paths, no protection against multiple instances or outside edits, and a planted `.pyc` can run code. |
| Process control (Job Objects, fallback) | 0 | 0 | 2 | 6 | 3 | 11 | The design is right but has never been run by tests. The job handle is closed twice on every cancel, and the stale-ppid fallback can kill unrelated processes. |
| Desktop UI | 0 | 1 | 12 | 14 | 3 | 30 | The main journeys work. Split state ownership causes stale errors and selection, misapplied `beat_ready`, a broken glyph generation counter and a seek feedback loop. |
| Desktop shell | 0 | 1 | 6 | 15 | 4 | 26 | Synchronous commands freeze the window, RPCs hang 30 s after the supervisor dies, projects live only in `%TEMP%`, and release builds fall back to the build machine's repo. |
| Tests | 0 | 0 | 4 | 20 | 0 | 24 | 98 tests with good goldens. Markers do not separate tests that need Manim or Typst, several tests cannot fail, the protocol is untested without Manim, and the "opaque WebM" failure is a measurement error. |
| Packaging and supply chain | 1 | 1 | 6 | 13 | 2 | 23 | The installer cannot start its Python on any other machine. Downloads are unverified, dependencies unlocked, the bundle unchecked, and licence notices incomplete. |
| DevOps, tooling, hygiene, spec docs | 0 | 0 | 3 | 11 | 3 | 17 | No CI, linter, formatter or `.gitattributes`, and no GitLab remote. The spec's protocol and timing sections have drifted from the code. |
| **Total** | **1** | **7** | **64** | **120** | **17** | **209** | |

---

## 3. Findings by severity

Path abbreviations: `W/` = `worker/mathimator_worker/`, `UI/` = `desktop/src/`, `RS/` = `desktop/src-tauri/`, `PK/` = `packaging/`. `Dnn` means the fix waits on that owner decision (section 7). Status `plaus.` = plausible.

### Critical (1)

| ID | Location | Title | Status | Fix |
|---|---|---|---|---|
| F060 | `PK/build-bundle.ps1:135` | Bundled venv is not relocatable (its `pyvenv.cfg` points at the builder's uv Python), so the installed app cannot start the worker on any other machine | confirmed | Ship a real CPython with Manim installed into it, add that path to `resolve_runtime`, and fail the build unless an isolated `python -I -c "import manim"` succeeds (D33). |

### High (7)

| ID | Location | Title | Status | Fix |
|---|---|---|---|---|
| F021 | `W/supervisor.py:920` | PNG-sequence export publishes leftover frames from earlier renders | confirmed | Empty `images/<stem>/MainScene` before each PNG export, and refuse to publish unless the frame count matches the run. |
| F037 | `UI/components/FormulaPreview.tsx:22` | Per-instance glyph generation counter collides with the shell's global cancel mark, so the glyph goes blank after reselecting a formula | confirmed | Use one module-level counter, cancel only while a compile is in flight, and retry a `cancelled` reply for the current request. |
| F054 | `RS/src/lib.rs:343` | New projects live only in `%TEMP%`, with no Save As and no reopen | confirmed | Give the launch project a real home and reopen the last project, per D29. |
| F170 | `W/supervisor.py:788` | An attacker-controlled `.mathimator/codegen` (a planted unchecked-hash `.pyc`) runs code when the project is rendered | confirmed | Delete and recreate the codegen folder before writing `scene.py`, and add `--check-hash-based-pycs always` with the other argv changes (D60). |
| G1.1 | `W/supervisor.py:705` | A segment cut off by quit, crash or external kill stays cached and breaks every later render of that beat | confirmed | Write a run marker before `Popen`, sweep unfinished media at startup or open when the marker exists, and discard on non-zero exit. |
| G4.1 | `W/supervisor.py:81` | The startup project keeps the 8.3 `TEMP` path, and its `~` breaks every LaTeX compile | confirmed | Resolve the startup path in `Supervisor.__init__` as `_open_project` already does, with a short-name regression test. |
| G4.2 | `RS/tauri.windows.conf.json:12` | Bundled TinyTeX cannot start from a non-ASCII install path, and the default per-user install sits under the profile folder | confirmed | Install per-machine or move TeX to an ASCII root, and add a one-line compile self-test (D62). |

### Medium (64)

| ID | Location | Title | Status | Fix |
|---|---|---|---|---|
| F001 | `W/expr.py:145` | Unary minus binds tighter than `^`, so `exp(-x^2)` plots e^(x^2) | confirmed | Parse unary minus above the power so `-x^2` means -(x^2), update spec §4, and bump generatorVersion (D01). |
| F002 | `tests/test_phase2.py:202` | Expression grammar edge cases are not pinned by tests | confirmed | Add parametrized tests for unary minus, `^` chains, literal forms, nesting and huge literals once D01 and D02 are settled. |
| F003 | `W/expr.py:258` | Expression literals are silently rounded to 3 decimals (`x/0.0001` becomes `x / 0`) | confirmed | Emit literals exactly, or refuse finer literals, per D02, and reject non-finite literals. |
| F004 | `W/supervisor.py:319` | A crafted `project.json` crashes the worker through OverflowError or RecursionError | confirmed | Bound expression length and nesting, reject non-finite numbers, map JSON errors to `DocumentError`, and add a dispatch exception boundary (D03, D04). |
| F005 | `W/supervisor.py:137` | No exception boundary, so disk, encoding or type errors kill the supervisor mid-export and lose undo | confirmed | Add a dispatch boundary plus all-or-nothing open, edit and history, and collect `rmtree` errors (D03, D05). |
| F008 | `W/session.py:78` | Every edit becomes an undo entry, including no-ops and colour drags | confirmed | Skip the undo push and the save when the serialized document is unchanged (D08). |
| F009 | `UI/components/Inspector.tsx:478` | The colour swatch commits an edit on every input event | confirmed | Update only the draft on input and commit once on the native `change` event (D08). |
| F010 | `W/session.py:123` | Removing trailing beats keeps the old full movie with no out-of-date badge | confirmed | Add `Session.output_current`, comparing whole key lists, and drop `index.output` when it returns false. |
| F015 | `W/equation.py:85` | The Typst translator passes letter runs through, so `y = mx + b` fails | confirmed | Emit adjacent letters and digits as separate atoms and add a compile corpus test (D10). |
| F016 | `W/tex_failure.py:23` | TeX log-line extraction does not match Manim 0.22's real output | confirmed | Read the job's newest `Tex/*.log` and report its `!` line (plus the `l.N` line per D11), keeping tail parsing only as a fallback. |
| F017 | `W/tex_failure.py:32` | Any failure is reported as "LaTeX failed." when "latex" or ".log" appears anywhere in the output | confirmed | Classify a failure as LaTeX only on Manim's own TeX markers, with regression tests that use misleading paths. |
| F022 | `W/progress.py:18` | Partial-movie regexes never match Manim's wrapped Rich output | confirmed | Track new files in the job's partials folder instead of parsing Rich text, and keep finished segments on cancel (D13). |
| F023 | `W/supervisor.py:693` | The job handle is closed twice on every Windows cancel or preview supersede | confirmed | Make `terminate_job` only terminate, take the handle out of `_Job` under `_state_lock`, and close it exactly once. |
| F024 | `W/process_group.py:365` | The fallback tree walk trusts stale parent PIDs and can terminate unrelated user processes | confirmed | Accept a parent edge only when the child's creation time is not earlier than the parent's, and kill through one identity-checked handle (D15). |
| F028 | `W/process_group.py:272` | `MATHIMATOR_SKIP_PROCESS_TABLE` only gates warm-up, so "safe" tests still reach the kernel32 helper | confirmed | Gate every table use on the flag, join the job thread at stdin EOF, and cancel fakes before closing in tests. |
| F029 | `W/progress.py:37` | `ManimProgress.feed` is quadratic and runs `rglob` for each cached beat on every log line | confirmed | Make feed incremental, resolve cached digests by direct path, and bound the buffer. |
| F032 | `W/supervisor.py:679` | Export jobs emit `beat_ready`, and PNG exports flush another quality's manifest | confirmed | Patch the preview index only from preview events, and limit the manifest flush to the job's quality folder (D17). |
| F033 | `UI/jobSession.ts:93` | `receive()` reports ignored events as applied | confirmed | Return an accepted flag and put only current-preview `beat_ready` events into `applied`, in both `receive` and `flush`. |
| F034 | `W/supervisor.py:1000` | The preview keeps an older last-good file for a beat that was edited again | confirmed | Clear `last_good` when a fresh partial is recorded, and prefer a fresh partial in the dirty branch. |
| F035 | `W/supervisor.py:1090` | Paths in `preview-index.json` are trusted, so a crafted project can copy any readable file into its own folder | confirmed | Derive `lastGood` from `last_good_dir`, and accept partial and output paths only when they lie lexically under `media_dir`. |
| F038 | `RS/src/lib.rs:626` | Synchronous Tauri commands run on the UI thread, so the window freezes and glyph cancel can never take effect | confirmed | Make the glyph commands async now, and move worker RPCs to an ordered off-thread queue (D20). |
| F039 | `RS/src/lib.rs:318` | Pending RPCs are not failed when the supervisor exits | confirmed | At stdout EOF, fail every pending call belonging to that supervisor generation. |
| F041 | `UI/jobSession.ts:176` | An Export or Open that cancels a preview leaves "Cancelled" on the status line | confirmed | Show "Cancelled" only for the export job's own cancel (D21). |
| F043 | `UI/components/PreviewStage.tsx:66` | During playback, every `timeupdate` seeks the video back | confirmed | Skip the seek when the target equals the time the element itself just reported. |
| F044 | `UI/index.css:50` | A long multi-line worker error collapses the stage and the inspector | confirmed | Cap the error block with scrolling, give it a separate cell, and add summary, details and Dismiss (D23). |
| F046 | `UI/useEditor.ts:436` | The Code view shows stale code after edits | confirmed | Render CodeView from `state.code` and `codeError` (D25). |
| F049 | `UI/useEditor.ts:222` | No preview is rendered after Open, Sample, Clear cache or Undo/Redo | confirmed | Schedule a preview after Open, Sample and Clear cache; undo and redo follow D26. |
| F053 | `RS/src/equation.rs:237` | The glyph path costs 300-400 ms, over the 200 ms budget | confirmed | Add `--ignore-system-fonts` and async commands now, and stop starting Python on every keystroke (D28). |
| F055 | `RS/src/lib.rs:122` | Debug builds run the stale bundled worker and venv | confirmed | Prefer the repo `worker/` and `.venv` in debug builds (D30). |
| F056 | `RS/src/lib.rs:663` | `setup()` errors panic silently, and optional Typst/TinyTeX overrides can block startup | confirmed | Resolve the runtime once, never fail setup for optional tools, and show startup errors in the UI (D31). |
| F061 | `RS/tauri.conf.json:10` | `tauri build` ships whatever is in `packaging/bundle` | confirmed | Add a `beforeBundleCommand` that checks `runtime.json`, the platform, the tools and worker freshness, and remove the repo fallback from release builds (D30). |
| F062 | `PK/build-bundle.ps1:44` | The offline bundle is built from unverified downloads and an unpinned toolchain | confirmed | Pin uv, check every download by SHA-256, and install Python dependencies from a hashed lock (D34, D35). |
| F063 | `docs/design-spec.md:32` | No CI, no lint or format gates, no build README, although the spec says CI builds the bundle | confirmed | Add tool configs, README and CONTRIBUTING, and a staged `.gitlab-ci.yml` as described in section 5. |
| F064 | `tests/test_phase2.py:151` | No `.gitattributes`, so autocrlf clones get CRLF fixtures and the save byte test fails | confirmed | Add `* text=auto eol=lf` plus binary rules. |
| F076 | `W/document.py:616` | Unsanitized LaTeX is compiled with file reads allowed (`openin_any=a`) | confirmed | Check formula LaTeX against a math-command allowlist and set `shell_escape=f` for the latex child (D18). |
| F077 | `W/document.py:536` | A malformed or non-UTF-8 `project.json` crashes the supervisor | confirmed | Catch decode and recursion errors, add type guards, reject overflowing ints and lone surrogates, and read through one helper (D07). |
| F078 | `W/document.py:1390` | Axes ranges have no cap on tick count | confirmed | Add one shared range validator that quantizes, caps ticks per axis and bounds plot sampling (D04). |
| F079 | `W/supervisor.py:1038` | Saves are not durable, and an `os.replace` OSError kills the worker after the in-memory commit | confirmed | fsync and retry the replace, and roll back or flag the edit per D05. |
| F105 | `tests/test_win32_job.py:161` | C# stand-ins, the csc invocation and the hidden-console flags are copy-pasted | confirmed | Move the C# sources into `tests/fixtures/win32`, and add one compile helper and one Rust `hidden_command` helper. |
| F109 | `UI/useEditor.ts:71` | A worker error stays in the handshake and is shown again on every later event | confirmed | Treat errors as per-step events instead of persistent handshake state. |
| F117 | `UI/useEditor.ts:218` | Selection is not reconciled after delete or undo, so Add fails with "unknown beat" | confirmed | Derive the exposed selection from the current document. |
| F118 | `UI/useEditor.ts:32` | `useEditor` is a god hook that copies the handshake into React state by hand | confirmed | Route every job transition through pure jobSession functions and one `publish`; splitting the hook follows D41. |
| F120 | `desktop/package.json:6` | The UI has no lint, no hook or component tests and no CI job | confirmed | Add typecheck, lint and format scripts, a linter config, jsdom component tests and a CI job (D51). |
| F135 | `W/supervisor.py:1014` | Undo during an in-flight preview leaves the other document's partial marked fresh | confirmed | Tag partial and last-good entries with their beat key, and count them fresh only when the key matches. |
| F150 | `PK/build-bundle.ps1:289` | TinyTeX packages come from live CTAN, so the build breaks at the next TeX Live release | confirmed | Point tlmgr at a pinned, dated tlnet snapshot in both scripts (D35). |
| F154 | `PK/build-bundle.ps1:140` | Bundled Python dependencies are not locked or hash-checked | confirmed | Commit a hashed universal lock seeded from today's bundle and install it with `uv pip sync --require-hashes` (D34). |
| F168 | `UI/useEditor.ts:306` | Every Export click records an undo step and clears redo | confirmed | Have `Session.edit` skip no-op edits, so an unchanged `set_export_profile` leaves history alone (D08). |
| F177 | `UI/notices.ts:1` | The shipped runtime includes GPL and LGPL codec libraries that the notices do not cover | confirmed | Generate full notices from the built bundle, correct the spec, and decide on x264/x265 (D75). |
| F178 | `PK/build-bundle.ps1:339` | Typst's LICENSE and NOTICE are dropped when the binary is bundled | confirmed | Copy both files next to `typst.exe` in both scripts, and fail the build if they are missing. |
| G1.2 | `W/supervisor.py:947` | A truncated WebM segment concatenates silently, and the short export is then a permanent cache hit | confirmed | Rely on the G1.1 sweep for the cause, and add a way to bypass the export cache (D56). |
| G2.1 | `W/argv.py:75` | Above 100 beats, Manim prunes the render's own segments | confirmed | Pass a Mathimator `manim.cfg` with `max_files_cached` of at least 2400 via `--config_file` (D60). |
| G2.2 | `W/argv.py:95` | Every successful render calls pypi.org (10 s timeout) before Manim exits | confirmed | Add `--silent` to every Manim command and update spec §5 (D60). |
| G2.3 | `W/supervisor.py:589` | The user-wide and current-folder `manim.cfg` apply to every render | confirmed | Run Manim with an isolated USERPROFILE/HOME and an app-owned cwd (D60). |
| G2.5 | `tests/test_phase5_render.py:291` | The known "opaque WebM" failure is a measurement artifact (the native vp9 decoder drops alpha) | confirmed | Decode with `libvpx-vp9`, compute `alpha_check` from the result, and correct the reports. |
| G3.1 | `W/codegen.py:100` | Beat lengths are rounded to whole frames in opposite directions for plays and waits | confirmed | Snap timing to frames at render time, or map the real frame counts in playback (D63). |
| G3.2 | `UI/playback.ts:21` | Movie mode seeks the full preview to raw timeline time | confirmed | Map timeline time through rendered beat boundaries in `playbackAt`, `onTimeUpdate` and `scrub_at` (D63). |
| G4.3 | `W/supervisor.py:101` | An apostrophe in the project path breaks every video render (Manim's unescaped concat manifest) | confirmed | Escape the manifest through a thin Manim launcher, or relocate or refuse per D61, and fix the worker's quote-splitting regexes. |
| G4.5 | `W/argv.py:98` | `%`, `~` or `^^` in an opened project's path break every LaTeX compile | confirmed | Run Manim from the project folder with a relative `--media_dir` (D61). |
| G4.6 | `W/argv.py:98` | Path characters outside the ANSI code page make dvisvgm fail | confirmed | Check the media path against `mbcs` on open, fall back to the 8.3 path or warn, and classify the error correctly (D61). |
| G4.7 | `W/supervisor.py:101` | Deep project paths break dvisvgm at 260 characters and probably the partial checks from 117 | confirmed | Check path length on open with a clear message, or move to a shorter cache root (D61). |
| G5.1 | `W/supervisor.py:1032` | Two app instances on one project silently overwrite each other's `project.json` | confirmed | Add a per-project lock or a disk-change check before saving (D58). |
| G5.2 | `W/supervisor.py:261` | Changes made to `project.json` outside the app are overwritten by the next in-app save | confirmed | Remember the bytes last read or written, and offer Reload or Overwrite when the disk copy differs (D59). |
| G5.3 | `W/supervisor.py:1036` | Moving or deleting the open folder makes the next edit silently recreate the old path | confirmed | Record the root's identity, never create the root, and reject saves once it is gone (D57). |
| G5.5 | `RS/src/lib.rs:388` | Respawn is pinned to the last project path, so Open and Open sample fail too when it is unreachable | confirmed | Keep the worker alive when the folder is lost, and fall back to a fresh project when respawning at the old path fails (D57). |

### Low (120)

| ID | Location | Title | Status | Fix |
|---|---|---|---|---|
| F006 | `W/session.py:668` | Non-finite edit values crash the supervisor, and a NaN scrub emits invalid JSON | confirmed | Make `_finite` reject non-finite and overflowing values with `DocumentError`, and refuse a non-finite scrub time. |
| F007 | `W/codegen.py:308` | Lone surrogates and NUL pass validation, then crash the supervisor or break `scene.py` | confirmed | Add one text validator for load and edit setters, open the log with `backslashreplace`, and send non-printable text through `repr` (D06). |
| F011 | `W/document.py:886` | A plot, surface or curve can enter before its axes | confirmed | Refuse a child entrance that comes before its axes' entrance in `_check_timeline`. |
| F012 | `W/document.py:915` | A transform source still counts as on screen, so later beats bring it back | confirmed | Track an `on_screen` set that drops the source after a transform, and refuse later beats on it (D09). |
| F013 | `W/document.py:1342` | Object ids `u`, `v`, `t` and the `<axes>_labels`/`_group` names are not reserved | confirmed | Refuse these scoped collisions at load time (D09). |
| F014 | `W/document.py:304` | `schemaVersion` and `generatorVersion` accept `true` and `1.0` | confirmed | Require a real int that is not a bool. |
| F018 | `W/codegen.py:118` | Move and set_color on labelled axes leave the labels behind | confirmed | Animate the axes group for labelled axes and bump generatorVersion (D12). |
| F019 | `worker/requirements-render.txt:1` | The render requirements differ from the spec and the bundle, and nothing transitive is pinned | confirmed | Use one `manim[typst]==0.22.0` input with a hashed lock shared by dev, CI and the bundle (D34). |
| F020 | `worker/pyproject.toml:7` | Test dependencies are undeclared and unpinned | confirmed | Declare a pinned dev/test dependency group backed by the shared lock (D34). |
| F025 | `W/process_group.py:365` | A process-table helper failure raises out of cancel and kills the supervisor loop | confirmed | Catch OSError around the table read and still kill the root, and fall back to `proc.kill` in `_kill_process`. |
| F026 | `W/supervisor.py:635` | Manim joins its Job Object only after it has started, so children launched through the venv launcher can escape | plaus. | Start Manim with the real interpreter, or start it suspended and assign before resuming, and add a launcher-shaped test (D16). |
| F027 | `W/supervisor.py:96` | The fallback PowerShell helper starts on every launch and reads every process's PEB | confirmed | Skip the warm-up when Job Objects are on, drop command lines from the production snapshot, and decide whether to replace the helper (D15). |
| F030 | `W/progress.py:156` | Cancel discards by a 1 s mtime window across all qualities instead of a snapshot | confirmed | Snapshot the job's quality folder before `Popen` and delete only files the job created or changed. |
| F031 | `W/supervisor.py:838` | Export publish copies files while holding `_doc_lock` | confirmed | Publish outside the lock and keep only the `export_key` comparison under it. |
| F036 | `W/supervisor.py:692` | The job thread has unbounded waits, and a stop timeout drops the new request | plaus. | Keep the readers draining after cancel, bound joins for jobs that are no longer current, and queue the request instead of dropping it (D19). |
| F040 | `UI/jobSession.ts:180` | "supervisor exited" leaves the export stuck at "Rendering export" | confirmed | Emit a dedicated exit event that ends every in-flight job (D21). |
| F042 | `UI/useEditor.ts:69` | `publish()` overwrites exportNote and exportPath values set elsewhere | confirmed | Make the handshake the only owner of the export note and path, and handle Open during an export per D22. |
| F045 | `UI/components/Inspector.tsx:600` | CommitNumber commits 0 for an empty field and silently drops comma decimals | confirmed | Revert on empty, parse with a strict pattern, and accept or flag commas per D24. |
| F047 | `UI/useEditor.ts:308` | A sticky `pendingCancel` makes the next export cancel itself | confirmed | Add a pure `abortExport` transition and use it in the reject and catch branches. |
| F048 | `UI/jobSession.ts:119` | The handshake buffer grows without bound | confirmed | Drop events for unknown job ids and remove the buffer. |
| F052 | `RS/src/equation.rs:199` | `typst --version` is spawned without `CREATE_NO_WINDOW` | confirmed | Build the probe through the shared hidden-console helper. |
| F057 | `RS/src/lib.rs:42` | The release binary embeds `CARGO_MANIFEST_DIR` and falls back to repo files | confirmed | Make `repo_root()` debug-only and log to the app log folder in release (D30, D32). |
| F065 | `tests/test_phase1.py:241` | The default and "fast" selections still run real Manim and Typst | confirmed | Mark the four Manim tests `render`, add `typst` and `process_table` markers, and turn on `--strict-markers` (D36). |
| F066 | `RS/rust-toolchain.toml:2` | Rust floats on "stable", Node has no engines field, and vitest uses a caret range | confirmed | Pin an exact Rust with components, add `.nvmrc` and engines, and pin vitest exactly (D37). |
| F067 | `desktop/vite.config.ts:29` | `vite.config.ts` has type errors that no check covers | confirmed | Import `defineConfig` from `vitest/config` and type-check the file through `tsconfig.node.json`. |
| F068 | `desktop/vite.config.ts:24` | `envPrefix: "TAURI_"` would expose every `TAURI_*` variable to the client bundle | confirmed | Narrow it to `["VITE_", "TAURI_ENV_"]` or remove it. |
| F069 | `.gitignore:4` | Agent state and Windows clutter are not ignored, and `.python-version` is blocked | confirmed | Ignore `Thumbs.db`, `desktop.ini` and `*.stackdump`, and handle `.cursor/` and `.python-version` per D39. |
| F070 | `RS/src/lib.rs:465` | `cargo fmt --check` already fails (11 hunks in 3 files) | confirmed | Make one formatting-only commit, list it in `.git-blame-ignore-revs`, then add the gate (D79). |
| F071 | `W/scrub.py:47` | Scrub selection exists in both Python and TypeScript, and only tests use the worker's scrub command | confirmed | Pick one owner (D44), and delete the unused `format.ts` `beatAt` now. |
| F072 | `W/session.py:602` | Template binding is split across modules, so a missed reverse sync silently reverts edits | confirmed | Refuse an edit whose reload changes the document, and make the 3D sync public (D41). |
| F073 | `W/codegen.py:131` | Untyped `params` dicts and string dispatch force codegen to re-validate every field | confirmed | Do the small de-duplication fixes now; enums and typed params only with a type checker (D41, D43). |
| F074 | `W/session.py:665` | Validators, limits and presets are duplicated, and some copies already behave differently | confirmed | Centralize constants and validators in `document.py` and reuse them. |
| F075 | `docs/design-spec.md:558` | The spec's protocol table covers about a third of the real contract | confirmed | Rewrite §6 to list every command and event (D40). |
| F080 | `W/document.py:296` | A `project.json` saved with a UTF-8 BOM is refused | confirmed | Strip one leading BOM in `load_text` (D07). |
| F082 | `W/session.py:123` | Dead and redundant code in the model layer | confirmed | Remove the dead for-else, the double validation and the repeated `start` check, and put the field path in id and anchor errors. |
| F084 | `W/codegen.py:327` | Numbers are validated before rounding, so values that pass are emitted as 0; seed is not range-checked | confirmed | Validate the rounded values and range-check seed (D02). |
| F085 | `W/codegen.py:39` | Dead and redundant code in codegen and templates | confirmed | Build the object map once, add a `_play` helper after adding goldens, and remove unreachable branches. |
| F087 | `W/supervisor.py:384` | A preview cancelled by an Export is never re-queued | confirmed | Schedule a preview after the export ends when beats are dirty (D26). |
| F088 | `W/supervisor.py:71` | Supervisor is a god class | plaus. | Extract layout, preview index and exports first, and leave scheduling until the Windows process tests pass (D41). |
| F089 | `W/supervisor.py:53` | `_Job` is filled field by field, and a test-only hand-written-scene mode branches the hot path | confirmed | Block the hand-written path when `project.json` was refused, and build a frozen RenderPlan (D42). |
| F090 | `W/supervisor.py:154` | The stdio protocol is untyped and cancellation is signalled by a magic string | plaus. | Add an `_emit_error` helper and a `cancelled` code field (D40). |
| F091 | `W/supervisor.py:1030` | The preview index has two sources of truth that are re-synced by hand | confirmed | Pass the PreviewIndex into `state_event` and delete `Session.preview_index`. |
| F092 | `W/supervisor.py:1049` | `preview-index.json` stores absolute paths, so a copied project points at the original folder's media | confirmed | Store project-relative paths, and rebase or drop legacy absolute entries. |
| F093 | `W/supervisor.py:911` | Format tables are duplicated across modules and languages | confirmed | Use one `FORMATS` table of `FormatSpec` in `document.py`. |
| F094 | `W/session.py:179` | A 30-branch edit if-chain, duplicated validators and a private cross-module import | confirmed | Reuse public document validators, delete dead code, and type params as `JsonValue`. |
| F095 | `W/supervisor.py:705` | Render errors are shown as Rich box-drawn tracebacks with venv paths | confirmed | Report the exit status plus the final exception line and point to `worker.log` (D23). |
| F096 | `W/supervisor.py:119` | Log file opened per line with no rotation, plus type holes in the job thread | plaus. | Log state and code events as summaries and fix the Popen, consume and `_buffer` typing (D43, D55). |
| F097 | `W/supervisor.py:620` | An exception after `Popen` leaves Manim running unsupervised and leaks its job handle | confirmed | Guard the code after `Popen` so it always closes the job or kills the process and emits a terminal event. |
| F102 | `W/process_group.py:148` | `process_group.py` mixes POSIX code, the Windows helper and test-only probes | confirmed | Do the small cleanups now; split or replace per D15 and D41. |
| F103 | `tests/test_win32_job.py:36` | The reap test passes even when TerminateJobObject fails | plaus. | Assert that "job terminate failed" is absent and that the supervisor still answers after cancel. |
| F104 | `RS/src/win32_job.rs:24` | The Rust Job Object binding is hand-rolled and swallows errors | confirmed | Use `OwnedHandle` and `as_raw_handle`, return `io::Result`, and log failures (D45, D32). |
| F106 | `W/win32_job.py:83` | The kernel32 binding is rebuilt on every call, and terminate is gated on an environment variable | confirmed | Cache the binding, raise `WinError`, and remove the env gate from `terminate_job`. |
| F110 | `UI/components/Timeline.tsx:97` | The duration drag ignores pointercancel and lost capture | confirmed | Track the pointerId, handle cancel and lost capture, and add `touch-action: none` (D48). |
| F111 | `UI/components/PreviewStage.tsx:117` | Playback stalls with a disabled Pause button at blank or shared-partial beats | confirmed | Replay on beat change, handle blank beats per D49, and never disable Pause while playing. |
| F112 | `UI/useEditor.ts:206` | `fieldError` is one global string that history and Open never clear | confirmed | Scope it to object and field, and clear it on history, open and selection changes. |
| F114 | `UI/useEditor.ts:509` | The duration cell snaps back during the commit round trip | confirmed | Keep the draft until the commit settles. |
| F115 | `UI/components/PreviewStage.tsx:47` | Preview file load failures are swallowed and remembered for the whole session | confirmed | Tie the missing set to the index it was observed against and log the HTTP status (D23). |
| F119 | `UI/components/Inspector.tsx:1` | Inspector.tsx is 667 lines with untyped params and silent fallbacks | plaus. | Extract shared field components now; typed params per D50 and D41. |
| F122 | `UI/types.ts:104` | Type-safety holes: index access, three `isRecord` guards, casts, an incomplete event union | plaus. | One `isRecord`, a typed `beatAt`, `noUncheckedIndexedAccess`, and parse errors that name the field (D50). |
| F123 | `UI/components/PreviewStage.tsx:79` | Leaked listener, permanent missing set, unclamped playhead, stuck drafts | confirmed | Remove the listener, reset `missing` per index, clamp the playhead, and handle pointer cancel (D49). |
| F124 | `RS/src/lib.rs:237` | Python children put the inherited cwd on `sys.path` and inherit PYTHONHOME | confirmed | Launch with `-P`, remove PYTHONHOME, and set a fixed app-controlled cwd. |
| F125 | `RS/src/lib.rs:216` | The asset scope only grows, CSP is null, and the unused `dialog:default` lets JS widen the scope | confirmed | Drop `dialog:default`, gate `open_project_path`, and add a CSP; a project-scoped file scheme only per D27. |
| F128 | `RS/src/equation.rs:168` | The shell's pure logic has no automated tests | confirmed | Add Rust unit tests for the generation gate, `clip`, `typst_failure`, routing and runtime resolution. |
| F129 | `RS/src/lib.rs:345` | Release builds keep test hooks and write logs to the repo or a shared `%TEMP%` folder; stderr capture loses crash output | confirmed | Debug-only repo root, the app log folder in release, append stderr per spawn, and an opt-in shell-ready marker (D32). |
| F130 | `RS/src/lib.rs:580` | Native dialogs have no parent window, so they are not modal | confirmed | Set the calling window as the dialog parent on Windows and macOS. |
| F131 | `RS/src/lib.rs:26` | `lib.rs` mixes six jobs, and Worker spreads one process across five Mutexes | confirmed | Add one `python_command` helper now; a single `Mutex<Option<Running>>` and one reap path per D41. |
| F132 | `RS/src/lib.rs:94` | The bundle layout is defined twice: packaging writes `runtime.json` and the shell ignores it | plaus. | Read `runtime.json` in the shell and check its platform (D30). |
| F133 | `RS/src/lib.rs:458` | The command surface is untyped, rejections arrive as Ok, and `json!` panics on non-UTF-8 paths | confirmed | Add a typed `GlyphResult`, `to_str()` checks for paths, and one refusal contract (D45, D46). |
| F136 | `tests/test_phase2.py:371` | Export cache-key tests only check hits, built with the function under test | confirmed | Add miss tests for each profile field, rename-invariance tests and a golden `export_key`. |
| F137 | `W/codegen.py:308` | No test protects codegen string escaping | confirmed | Add `ast.parse` round-trip tests over adversarial LaTeX and Text, and route NUL through `repr`. |
| F138 | `W/supervisor.py:889` | The supervisor protocol and render pipeline are untested without real Manim | confirmed | Add in-process `handle_line` tests, direct publish tests and a pure-Python fake manim package. |
| F139 | `tests/test_phase3.py:291` | Three phase-3 tests cannot fail | confirmed | Remove the vacuous helpers and tests, and test `applyGlyph` and the generation gate instead. |
| F140 | `tests/test_followup.py:174` | The discard test cannot detect a broken keep-set | confirmed | Test the keep, delete and old-file branches separately. |
| F141 | `tests/test_followup.py:108` | `test_stop_process_joins_for_one_second` asserts only an upper bound | confirmed | Assert the 1 s join timeout and the emitted message. |
| F142 | `tests/test_phase1.py:498` | The cancel-grandchild test can pass without watching any TeX process | confirmed | Require a live TeX pid before cancel, and add a deterministic fake-latex test. |
| F143 | `tests/test_phase5_render.py:182` | The known WebM failure is not xfail and its evidence record is hard-coded | confirmed | Compute `alpha_check` from the measurement, then follow D54. |
| F144 | `W/process_group.py:404` | The pure Windows tree helpers are untested and trust stale parent PIDs | confirmed | Add creation-time edge checks and pure tests with stale-ppid maps. |
| F145 | `tests/support.py:156` | SupervisorClient hides tracebacks and requires a Manim venv | confirmed | Keep a stderr tail, fail fast on EOF, and fix the setup message. |
| F146 | `tests/support.py:63` | Helpers and fixtures are duplicated across modules and languages | confirmed | Consolidate into `support.py` helpers; share the C# fixtures per D53. |
| F147 | `UI/jobSession.test.ts:106` | The desktop test file mixes modules and one test is misnamed | confirmed | Move the playback tests to `playback.test.ts` and add the real dirty-beat case. |
| F148 | `tests/test_phase2_render.py:235` | Render tests ignore edit rejections | confirmed | Fail when an edit reply is not a `state` event. |
| F149 | `tests/test_phase1.py` | Tests are organised by build phase rather than by module | plaus. | Keep the phase files and fix markers and docstrings (D53). |
| F151 | `PK/build-bundle.ps1:66` | TinyTeX, Typst and uv downloads are not checksum-verified, and Mark-of-the-Web is removed | confirmed | Check SHA-256 on download and on cache hit, and remove `Unblock-File` (D35). |
| F152 | `PK/build-bundle.ps1:44` | The uv bootstrap is unpinned and cached forever, and the sh installer edits shell profiles | confirmed | Pin the uv version and hash, cache per version, and use `UV_UNMANAGED_INSTALL` (D35). |
| F153 | `PK/build-bundle.ps1:168` | Any failure triggers the Python fallback, and `runtime.json` records a false reason | confirmed | Choose the interpreter from uv metadata and fall back only after a real no-wheel probe (D35). |
| F155 | `PK/build-bundle.sh:167` | `build-bundle.sh` fails on a second run because the empty offline repo is still configured | confirmed | Reset the tlmgr repository before `update --self`. |
| F156 | `PK/build-bundle.ps1:323` | The bundle ships build-host paths and a 20.8 MB cached CTAN database | confirmed | Clean tlmgr temp, backup and cache files, and add a USERPROFILE leak gate. |
| F157 | `PK/build-bundle.ps1:386` | Version bumps are unsafe (unversioned Typst cache, hard-coded check, unstamped TinyTeX) | confirmed | Version the cache names, compare against the variables, and stamp the TinyTeX tree. |
| F158 | `PK/build-bundle.sh:234` | `runtime.json` is written by pasting shell values into Python source | confirmed | Use a quoted heredoc and pass the values through the environment. |
| F159 | `PK/build-bundle.sh:51` | The Cygwin branch passes a POSIX path to `powershell -File` | confirmed | Wrap the path in `cygpath -w`. |
| F160 | `PK/build-bundle.ps1:395` | No sizes are logged for the bundle directories | confirmed | Sum file lengths recursively and log a total. |
| F163 | `PK/build-bundle.sh:142` | Dead no-op block after `install_manim` | confirmed | Delete it and stop capturing the unused value. |
| F164 | `RS/src/lib.rs:471` | The RPC timeout is not part of the protocol, so a late reply is dropped but its edit is still applied | plaus. | Publish exports outside `_doc_lock`, refresh state after a failed RPC, and reconcile the project path from `state` events. |
| F165 | `W/supervisor.py:158` | No version negotiation, and unknown commands surface as generic failures | confirmed | Surface `error` replies as errors now, and add a protocol version to `ready` (D40, D30). |
| F167 | `W/supervisor.py:132` | `worker.log` records every full payload, never rotates, and Clear cache keeps it | confirmed | Rotate it, truncate it on Clear cache, and log state and code as summaries (D55). |
| F169 | `RS/src/equation.rs:70` | Unbounded disk growth: one glyph pair per keystroke, the full-payload log, a temp project per launch | confirmed | Delete older glyph pairs after a successful compile, rotate the log, and handle temp projects per D29. |
| F173 | `W/document.py:1346` | `_id_value` and `_anchor` discard the path argument | confirmed | Include the field path in both messages. |
| F174 | `UI/exportProfile.ts:21` | The pure UI rule modules that mirror worker logic are untested | confirmed | Add `format.test.ts`, `exportProfile.test.ts` and a constant-parity check. |
| F175 | `W/jsonio.py:16` | mypy reports 50 errors, none a runtime defect | confirmed | Return `list[JsonValue]` from producers, use `sys.platform` guards, and fix six small sites (D43). |
| F179 | `worker/pyproject.toml:3` | The app version and toolchain pins are hand-copied with no check | confirmed | Keep single sources plus a `check_versions.py` lint job (D72). |
| F180 | `desktop/package.json:25` | Node is unpinned and the npm pin policy is inconsistent | confirmed | Add `.nvmrc`, engines and `.npmrc`, and pin vitest exactly (D37). |
| G1.3 | `RS/src/lib.rs:388` | Respawn is pinned to an unavailable folder, and startup has no exception boundary | confirmed | Let startup survive OSError, fall back to a fresh project, and never pass `--init` for an opened folder (D57). |
| G1.4 | `UI/useEditor.ts:76` | The UI never re-syncs after "supervisor exited" | confirmed | Drain pending calls, emit a typed exit event, resync once, and tell the user the history was cleared (D21). |
| G2.6 | `RS/src/lib.rs:173` | A missing bundled TinyTeX is not detected, so renders silently use whatever TeX is on PATH | confirmed | Fail closed when the bundle has no TinyTeX, and log the resolved tool paths (D31). |
| G3.3 | `W/document.py:384` | Snapping to 1/15 s cannot fix the drift, and Manim miscounts even frame-exact durations | confirmed | Fix within the frame-timing change (a render-time helper or a ±1e-6 margin) (D63). |
| G3.4 | `W/scrub.py:104` | Beat lookup compares against a floating-point start plus duration | confirmed | Pick the beat by the next beat's rounded `derivedStart` on both sides. |
| G3.5 | `docs/design-spec.md:460` | The spec has no rule for how beat time maps to frames | confirmed | Add a timing rule to spec §4 and §6 plus a fractional-duration fixture (D63). |
| G3.6 | `tests/test_phase3.py:257` | Tests lock in the raw movie-time mapping and only check whole-second durations | confirmed | Add pure frame-count tests and scrub tests where the movie is longer than the timeline (D63). |
| G4.4 | `W/argv.py:80` | `{` or `}` in the project path crashes Manim's directory templating | confirmed | Refuse `{` in the project path with a clear message, or relocate per D61. |
| G4.8 | `RS/src/equation.rs:289` | Typst reads the glyph output path as a page template | confirmed | Run typst from the equation folder with file names only. |
| G4.9 | `W/supervisor.py:907` | A folder named `480p15` anywhere in the path makes exports fail | confirmed | Check path parts relative to `media_dir` only. |
| G4.10 | `tests/support.py:215` | No test uses a project path with special characters | confirmed | Add parametrized path tests and an 8.3 alias test. |
| G5.4 | `RS/src/lib.rs:245` | Respawn re-initialises with `--init` and the UI is never re-synced | confirmed | Pass `--init` only for the launch project, refuse a missing `project.json`, and emit a restarted event (D57, D21). |
| G5.6 | `W/supervisor.py:788` | Two instances share `scene.py` and the media tree, so an export can cache the other window's scene | confirmed | Add a per-project lock and check that `scene.py` is unchanged before publishing (D58). |
| G5.7 | `W/progress.py:156` | Cancel's mtime-window discard deletes another instance's files | confirmed | Use a per-project lock plus snapshot-based discard (D58). |
| G5.8 | `W/supervisor.py:524` | Clear cache's `rmtree` raises on in-use files and kills the supervisor | confirmed | Collect `rmtree` errors, rebuild the index, and reply with a files-in-use rejection (D58). |
| G5.9 | `W/supervisor.py:1037` | All instances share one fixed temp name for saves, and sharing violations are never retried | plaus. | Use a unique `mkstemp` temp file, fsync, and retry briefly on WinError 5 and 32 (D58). |
| G5.10 | `W/progress.py:160` | Discard compares file-system mtimes with the local clock and swallows unlink failures | confirmed | Discard by snapshot, and retry and log unlink failures. |
| G6.1 | `RS/tauri.windows.conf.json:13` | The stray `packaging/bundle/grep.exe.stackdump` will ship in the installer | confirmed | Delete it, and make the bundle scripts prune or refuse unexpected entries (D64). |
| G6.2 | `grep.exe.stackdump:1` | The root stackdump is untracked and not ignored | confirmed | Delete it, ignore `*.stackdump`, and stage explicit paths only. |

### Info (17)

| ID | Location | Title | Status | Fix |
|---|---|---|---|---|
| F051 | `RS/capabilities/default.json:6` | Least privilege: null CSP, `dialog:default`, no app-command ACL | plaus. | Same remedy as F125 (D27). |
| F059 | `RS/src/lib.rs:304` | Every RPC reply is also broadcast as a worker-event | confirmed | Emit only lines that no pending caller claimed. |
| F081 | `W/document.py:1` | `document.py` is a 1492-line god module | plaus. | Move identity hashing and the model types out only if D41 schedules it. |
| F083 | `W/document.py:1276` | Per-beat object maps are rebuilt (quadratic) and keys are hashed repeatedly | confirmed | Build the object map once and memoize keys per project object, together with other refactoring. |
| F098 | `W/process_group.py:164` | Helper errors are silently turned into an empty process table | plaus. | Raise on ERROR lines and on empty snapshots, and still kill the root process. |
| F101 | `RS/src/win32_job.rs:44` | Job setup failures carry no usable diagnostics | plaus. | Raise `ctypes.WinError` in Python and return `io::Error` in Rust, logged to a shell log. |
| F107 | `W/process_group.py:323` | The POSIX helpers depend on `/proc`, which macOS lacks | confirmed | Fall back to `os.kill(pid, 0)` and skip the `/proc` tests where it is absent (D47). |
| F113 | `UI/useEditor.ts:187` | A failed `render_preview` leaves the status at "Rendering preview" | plaus. | Add a pure `abandonPreview` transition and use it in the catch. |
| F116 | `UI/useEditor.ts:50` | `rendering` state is never read, and `format.beatAt` is unused | confirmed | Delete both, and share one `beat_ready` helper and one `isRecord`. |
| F121 | `UI/playback.ts:9` | Preview and timing rules are duplicated across TypeScript and Python | plaus. | Add named limit constants and a `roundMs` helper; shared fixtures per D44. |
| F127 | `RS/src/equation.rs:68` | Glyph files go under `equation/` while the spec says `typst/`, and one pair is left per compile | confirmed | Pick one folder name (D52), and delete older glyph pairs after each compile. |
| F134 | `RS/src/win32_job.rs:60` | Smaller idiom and config issues in the shell | confirmed | Use `OwnedHandle`, pin the toolchain, and drop `mobile_entry_point`; the identifier follows D38. |
| F161 | `worker/pyproject.toml:6` | Deprecated table form of `license` with an unpinned setuptools | confirmed | Use `license = "MIT"` and `setuptools>=77`. |
| F162 | `RS/tauri.conf.json:5` | The bundle identifier ends in `.app` | confirmed | Choose the permanent identifier before the first public release (D38). |
| F176 | `W/session.py:179` | ruff baseline: 23 default and 377 extended hits, 22 of 34 files unformatted | confirmed | Commit a ruff config and one mechanical format commit (D43, D79). |
| G6.3 | `.git/HEAD:1` | The checkout is `audit/cleanup-gitlab-devops`, not `cursor/windows-audit-fa22` | confirmed | Do all work on `audit/cleanup-gitlab-devops`; all three branches are `8e49fbd`. |
| G6.4 | `.git/config:7` | No GitLab remote; `origin` is a Cursor-hosted URL that has never been fetched | confirmed | Add the GitLab remote the owner names before any push (D65). |

---

## 4. Code style and tooling baseline

| Tool | Version | Command | Result |
|---|---|---|---|
| ruff (default rules) | 0.15.9 (miniconda; not in `.venv`, not pinned) | `ruff check worker tests scenes` | 23 hits. 22 are `from manim import *` in the hand-written scenes, which is intended Manim style. 1 is an unused variable in a test. No defects in `worker/`. |
| ruff (extended set) | 0.15.9 | E,W,F,I,B,UP,SIM,RUF,PL,PT,N,C4,PIE,RET,ARG,PTH,C90 | 377 hits, 23 auto-fixable. 187 E501 at 88 columns but only 5 at 120. Two hits point at real problems: ARG001 (path dropped, F173) and RET504 (`_finite` never checks finiteness, F006). Complexity hot spots: `apply_edit` C901=29, `_run_job_inner` 23, `_check_timeline` 23, `handle_line` 17. |
| ruff format | 0.15.9 | `--check` | 22 of 34 files change at 88 columns (about 892 diff lines); 15 of 34 at 120 columns (about 179 lines). The de facto width is roughly 100 to 120. |
| mypy | 2.1.0 | `--python-version 3.11` and `--strict` | 50 errors either way (strict adds 0, so annotations are complete). About 40 come from `JsonValue` invariance and 4 from `os.name` guards; none is a runtime defect. `--platform linux` gives 67. |
| tsc | 5.9.3 | `tsc --noEmit -p .` | Clean under strict. `vite.config.ts` is never checked; checked on its own it has 2 errors (F067). Optional `noUncheckedIndexedAccess` gives 15 errors and `exactOptionalPropertyTypes` gives 2. |
| vitest | 5.0.5 (Node 22.16.0) | `npm test` | 1 file, 8 tests, all pass. `format.ts` and `exportProfile.ts` have no tests. |
| eslint / prettier / biome | none | | None installed, no configs, no lint script. |
| cargo fmt | rustfmt 1.8.0, cargo 1.93.0 | `--check` | 11 hunks in 3 files fail (F070). |
| cargo clippy | 0.1.93 | `--offline --locked --all-targets` | 1 warning (`manual_map`, `equation.rs:105`). Pedantic gives 43 unique hits, 27 of them false positives (`needless_pass_by_value` on Tauri commands). |
| pytest | 9.1.1 on CPython 3.14.2 | `--collect-only` | 98 tests, clean under `--strict-markers -W error`. `not render and not export and not offline` still leaves 4 real-Manim tests plus 3 Typst tests. |
| git eol | 2.45.1 | `ls-files --eol` | All 88 text files are LF in the index. `core.autocrlf=true` system- and user-wide with no `.gitattributes`, so a fresh Windows clone checks out CRLF and the byte test fails (F064). |

What enforcing style would take:
- **Python:** one ruff format commit, plus 23 safe autofixes (import sorting, UP037, UP035, UP017). Per-file ignores for `scenes/` (golden output, must stay byte-identical) and for test magic numbers. A separate mypy commit clears the 50 errors with no behaviour change.
- **TypeScript:** pick a formatter and linter (D51). The size of the reformat cannot be measured without installing the tool. Add `tsconfig.node.json` for the Vite config.
- **Rust:** one `cargo fmt` commit, one clippy fix, then pin the toolchain with the `rustfmt` and `clippy` components.
- **Repo:** `.gitattributes` with `eol=lf` (renormalizing is a no-op today) and `.editorconfig`. Every bulk-format commit is listed in `.git-blame-ignore-revs`. The choice between one mass reformat and gradual adoption is D79.

---

## 5. GitLab DevOps plan

### 5.1 Starting point

What exists: MIT LICENSE, `.gitignore`, `pytest.ini` with 3 markers, a strict `tsconfig.json`, committed `package-lock.json` (v3, integrity hashes) and `Cargo.lock` (v4), a floating Rust toolchain, the docs and phase reports, the two packaging scripts, and Conventional Commit messages.

What is missing: README, CONTRIBUTING, CHANGELOG, SECURITY, CODEOWNERS, any CI file, `.gitattributes`, `.editorconfig`, linter, formatter and type-check configs, a Node pin, Python dev dependencies and a lock, a bundle lock, version-sync checks, generated licence notices, a release flow, and a guard against building an installer with an empty runtime. There is also no GitLab remote yet (D65).

One build prerequisite affects CI: `Cargo.toml` enables `custom-protocol` by default, so every cargo job needs `desktop/dist` built first.

### 5.2 Pipeline

Stages: `prepare`, `lint`, `test`, `build`, `package`, `release`. Files: `.gitlab-ci.yml` (workflow, defaults, includes) plus `ci/linux.yml`, `ci/windows.yml`, `ci/security.yml`, `ci/release.yml`, `ci/renovate.yml` and `ci/images/tauri-linux.Dockerfile`.

| Job | Stage | Runner | Runs when | Content |
|---|---|---|---|---|
| prepare:tauri-image | prepare | Linux Docker | Dockerfile or toolchain changes | rust:1.93 plus webkit2gtk-4.1, gtk3, libsoup3 and other build deps, plus Node; pushed to the project registry |
| lint:versions | lint | Linux | always | `scripts/check_versions.py` (app version, Manim/Typst/TinyTeX/uv/CPython pins, tag equals version) |
| lint:python | lint | Linux (uv image) | worker/ or tests/ changes; main | `ruff check --output-format=gitlab`, `ruff format --check`, mypy (win32 platform, optionally linux) |
| lint:ui | lint | Linux (node) | desktop/ changes; main | `npm ci`, eslint (gitlab formatter), prettier `--check`, tsc for src and the node config |
| lint:rust:fmt | lint | Linux | src-tauri changes; main | `cargo fmt --check` (no compile needed) |
| lint:pre-commit | lint | Linux | always (if D71) | `pre-commit run --all-files` |
| test:python:linux | test | Linux | always | Matrix over the supported versions (D70); fast marker set; JUnit and Cobertura reports; subprocess coverage |
| test:python:typst | test | Linux | worker/ changes | Pinned Typst 0.15.0 (checked by SHA-256), `-m typst` |
| test:ui | test | Linux | desktop/src changes | vitest with JUnit and Cobertura |
| build:ui | build | Linux | before any cargo job | `npm run build`, with `desktop/dist` as a 1-day artifact |
| lint:rust:clippy:linux, test:rust:linux | test | Linux tauri image | src-tauri changes | Pre-check only; `#[cfg(windows)]` code is not compiled |
| deny:rust, audit:npm, audit:python, SAST, Secret Detection | test | Linux | always or nightly | cargo-deny, `npm audit --omit=dev`, pip-audit on the bundle lock, GitLab templates (D77) |
| test:python:windows | test | Windows (D67) | worker/ or tests/ changes; main | Fast lane on 3.14.9; the Windows `process_group` branch and csc fakes |
| lint:rust:clippy:windows, test:rust:windows | test | Windows | src-tauri changes; main | The only lanes that compile `win32_job.rs` |
| test:win32-jobs | test | Windows | per D14 | `MATHIMATOR_WIN32_TESTS=1` pytest plus `cargo test -- --ignored` |
| package:windows | package | Windows | nightly, tags, manual (D73) | `build-bundle.ps1` (Windows PowerShell 5.1), `check_bundle.py`, `npm ci`, `tauri build --ci`, SHA256SUMS, notices, CycloneDX SBOMs |
| test:render:windows | package | the same Windows runner | nightly, tags, manual on MRs (D68) | `-m "render or export or offline or typst"` against the fresh bundle (the bundle is about 1 GB, so it is never passed as an artifact) |
| upload:installer, release | release | Linux | protected tags | Generic Package Registry upload and a GitLab Release with notes from CHANGELOG |
| renovate | n/a | Linux | schedule (D76) | Spec-aware pin rules: Tauri 2.12.x, React 19, Vite 8; Manim and Typst disabled |

Workflow rules: run merge-request pipelines and skip duplicate branch pipelines; run on the default branch, `vX.Y.Z(-rc.N)` tags, schedules and web triggers. Everything is interruptible except package and release. MRs use `rules:changes` to skip unaffected stacks; main and tags run everything.

Caches:
- npm, keyed on `package-lock.json`.
- cargo registry, keyed on `Cargo.lock` plus the toolchain, per OS.
- uv, keyed on `uv.lock`.
- `packaging/.cache`, keyed on `versions.env` plus the bundle lock.
- The cargo `target/` directory only on the persistent Windows runner.

Artifacts:
- JUnit, Cobertura and code-quality reports: 1 week.
- `dist`: 1 day.
- Installers: 7 days on MRs and nightly; 30 days on tags, plus a permanent copy in the registry.
- Render evidence (`out/**`): 30 days, which finally makes the phase-report evidence durable.

Timeouts: 30 min for Linux jobs, 120 min for package and render jobs, plus pytest-timeout.

### 5.3 Repository hygiene and project files

| File | Purpose |
|---|---|
| `.gitattributes`, `.editorconfig`, `.gitignore` additions, `.git-blame-ignore-revs` | LF everywhere, editor defaults, Windows/MSYS clutter, bulk-format commits hidden from blame |
| `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `SECURITY.md` | What the app is, dev setup (current `MATHIMATOR_*` variables, test tiers, `npm run build` before cargo), release steps, reporting channel (D80) |
| `.gitlab/CODEOWNERS`, `.gitlab/merge_request_templates/Default.md`, `.gitlab/issue_templates/{Bug,Feature,Render-regression}.md` | Review routing and a spec-aware MR checklist (generatorVersion bump, pins untouched, tiers run) |
| Root `pyproject.toml` (uv workspace), `uv.lock`, `.python-version` | Pinned dev/test/render groups (D34, D70) |
| `desktop/.nvmrc`, `.npmrc`, `eslint.config.js`, `.prettierrc.json`, `tsconfig.node.json` | Node pin and the TS lint and format setup (D37, D51) |
| `desktop/src-tauri/rust-toolchain.toml`, `rustfmt.toml`, `deny.toml`, `.config/nextest.toml` | Exact toolchain, formatting, licence and advisory checks, JUnit output |
| `packaging/versions.env`, `packaging/requirements-bundle.{in,txt}` | Single source for tool pins and hashes; hashed bundle lock (D34, D35) |
| `scripts/check_versions.py`, `scripts/check_bundle.py`, `scripts/generate_notices.py`, `.bumpversion.toml`, `cliff.toml` | Pin and version gate, installer guard, licence notices, version bumps and changelog (D72, D75) |
| `.pre-commit-config.yaml`, `renovate.json5` | Local hooks mirrored in CI; dependency updates (D71, D76) |

### 5.4 Release flow

1. Bump the version on a release branch (one command). The changelog is generated from Conventional Commits.
2. Merge the "Release vX.Y.Z" MR into protected `main`.
3. A maintainer pushes a protected tag. The tag pipeline checks that the tag matches the manifests, runs the full Linux and Windows suites (including render, export and offline against the fresh bundle), builds and checks the installer, generates notices, SBOMs and SHA256SUMS, uploads everything to the Generic Package Registry, and creates a GitLab Release.
4. Signing stays off for v1, as the spec requires. A disabled `signCommand` hook is wired in so a later spec revision only needs configuration (D74).
5. Optional: a manual clean-VM install job that finally runs the spec §7 clean-machine criterion. It has never been run, and F060 shows it would fail today.

---

## 6. Proposed implementation sequence

Rules for every step:
- One commit per step, made on `audit/cleanup-gitlab-devops` after the step is verified. Verification uses targeted tests plus non-pytest gates (ruff, mypy, tsc, vitest, cargo fmt and clippy); a full-suite run happens only with your approval.
- Stage explicit paths only. No attribution trailers.
- Steps marked with decisions start only after those decisions are answered.

**Phase A: hygiene and tooling baseline**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S01 | Delete audit stackdumps; ignore `*.stackdump`, Thumbs.db, desktop.ini | G6.2, G6.1, F069 | none |
| S02 | `.gitattributes` (eol=lf) and `.editorconfig` | F064 | none |
| S03 | `.cursor/` and `.python-version` handling | F069, G6.2 | D39 |
| S04 | Pin Rust toolchain; bulk `cargo fmt`; clippy fix; blame-ignore | F066, F070, F134 | D37, D79 |
| S05 | ruff config; bulk format and safe autofixes | F176, F096 | D43, D70, D79 |
| S06 | mypy clean-up (50 errors) | F175 | D43 |
| S07 | Node pin, engines, exact vitest, `tsconfig.node.json`, envPrefix | F066, F067, F068, F180 | D37 |
| S08 | TS linter and formatter config; bulk format | F120 | D51, D79 |
| S09 | Honest pytest markers and `--strict-markers` | F065, F149 | D36 |
| S10 | Python dev dependencies and lock | F019, F020 | D34, D70 |
| S11 | Linux GitLab CI skeleton | F063, F120 | D65, D66, D43, D51, D37, D71 |
| S12 | Add GitLab remote, protected main, push branch, open MR (only after explicit approval) | G6.3, G6.4 | D65, D69 |

**Phase B: critical and high**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S13 | Recreate the codegen folder before writing `scene.py` (planted `.pyc` test) | F170 | none |
| S14 | Resolve the startup project path (8.3 test) | G4.1, G4.10 | none |
| S15 | PNG export: clear frames folder, verify frame count | F021 | none |
| S16 | Interrupted-run marker and sweep; discard on non-zero exit | G1.1, G1.2 | none |
| S17 | Glyph generation counter fix and tests | F037, F139 | none |
| S18 | Relocatable Python in the bundle, with an import gate | F060 | D33 |
| S19 | TeX on an ASCII path plus self-test | G4.2 | D62 |
| S20 | Project home, reopen last, temp pruning | F054, F169 | D29 |

**Phase C: worker robustness**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S21 | Boundary validation (numbers, structure, expression limits, versions, field paths) | F004, F006, F014, F077, F082, F173 | D04 |
| S22 | Text validator; log `backslashreplace`; `repr` for non-printable text | F007, F137 | D06 |
| S23 | One `project.json` reader (UTF-8 and BOM policy) | F077, F080, F138 | D07 |
| S24 | Exception boundary; non-raising log and kill; post-Popen guard | F005, F004, F006, F025, F097, F098 | D03 |
| S25 | All-or-nothing state changes; durable atomic save | F005, F079, G5.9, F039 | D05 |
| S26 | Clear cache error collection | F005, G5.8 | D58 |
| S27 | Shell fails pending RPCs at EOF; no duplicate broadcast | F039, F059 | none |
| S28 | TeX failure reporting from the TeX log; one-line summaries | F016, F017, F095 | D11, D23 |

**Phase D: process control**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S29 | Single ownership of the job handle; WinError; cached kernel32 | F023, F101, F106 | none |
| S30 | `process_group` cleanups and flag gating | F027, F028, F098, F102 | none |
| S31 | Fallback tree-kill safety, or helper replacement | F024, F027, F144 | D15 |
| S32 | Launcher race; stronger reap test | F026, F103 | D16, D33, D14 |
| S33 | Rust Job Object binding with OwnedHandle and logged errors | F104, F101, F134 | D45, D32 |
| S34 | macOS process checks | F107 | D47 |

**Phase E: render pipeline**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S35 | Progress tracking rewrite; snapshot discard; scoped flush | F022, F029, F030, F032, G5.7, G5.10 | D13, D17 |
| S36 | Preview index correctness and path containment | F010, F034, F035, F091, F092, F135 | none |
| S37 | Export publish outside `_doc_lock` | F031, F164 | none |
| S38 | Stop-timeout behaviour | F036 | D19 |
| S39 | Manim isolation and argv changes (config, `--silent`, env, `-P`, hash-checked pycs) | G2.1, G2.2, G2.3, F170, F124 | D60 |
| S40 | Path robustness (relative media dir, quoting, preflight) | G4.3, G4.4, G4.5, G4.6, G4.7, G4.10 | D61, D60 |
| S41 | `480p15` check relative to `media_dir` | G4.9 | none |
| S42 | Untrusted LaTeX allowlist and `shell_escape=f` | F076, F035 | D18 |
| S43 | Frame timing | G3.1, G3.2, G3.3, G3.5, G3.6 | D63 |
| S44 | Beat boundary lookup | G3.4 | none |
| S45 | Export cache bypass | G1.2 | D56 |
| S46 | Multi-window lock and outside-edit detection | G5.1, G5.2, G5.6 | D58, D59 |
| S47 | Lost or moved project folder handling | G1.3, G5.3, G5.4, G5.5 | D57, D21 |

**Phase F: document model**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S48 | Expression grammar and literals; generatorVersion bump | F001, F002, F003 | D01, D02 |
| S49 | Validate rounded document numbers; seed range | F084 | D02 |
| S50 | Child entrance not before its axes | F011 | none |
| S51 | Transform-source and id-collision refusals | F012, F013 | D09 |
| S52 | Tick cap and sampling bound | F078 | D04 |
| S53 | No-op edits, colour commit, export-settings undo | F008, F009, F168 | D08 |
| S54 | Typst letter splitting | F015 | D10 |
| S55 | Labelled-axes moves | F018 | D12 |
| S56 | Behaviour-neutral de-duplication | F074, F082, F085, F093, F094 | none |

**Phase G: desktop UI**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S57 | jobSession transitions; accepted beat_ready; no buffer; errors as events | F033, F032, F047, F048, F109, F113, F118 | none |
| S58 | Supervisor-exit and auto-cancel status handling | F040, F041, G1.4 | D21 |
| S59 | Selection reconciliation; scoped fieldError | F117, F112 | none |
| S60 | PreviewStage fixes that need no choice | F043, F115, F123, F111, F114 | none |
| S61 | Blank-beat playback | F111, F123 | D49 |
| S62 | Timeline drag cancel and touch | F110 | D48 |
| S63 | Error display | F044, F095, F115 | D23 |
| S64 | Code view from state | F046 | D25 |
| S65 | Export note ownership; Open during export | F042 | D22 |
| S66 | Number field parsing | F045 | D24 |
| S67 | Automatic previews | F049, F087 | D26 |
| S68 | UI dead code and shared helpers | F116, F121, F071 | none |
| S69 | TS strictness and field-path parse errors | F122, F119 | D50 |

**Phase H: desktop shell**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S70 | Glyph pipeline: async, per-generation child, font flag, hidden probe, cleanup, tests | F038, F052, F053, G4.8, F127, F169, F128 | none |
| S71 | Worker RPCs off the UI thread, with ordering | F038, F164 | D20 |
| S72 | Move the LaTeX-to-Typst translator | F053 | D28 |
| S73 | Runtime resolution, `runtime.json`, bundle check, version check | F055, F057, F061, F132, F165 | D30, D40 |
| S74 | Startup failure handling | F056, G2.6 | D31 |
| S75 | Log locations; append stderr; opt-in shell-ready | F129, F057 | D32 |
| S76 | Python spawn hygiene (`-P`, env, cwd, one hide-console helper) | F124, F105, F131 | none |
| S77 | Webview hardening | F051, F125 | D27 |
| S78 | Modal dialogs | F130 | none |
| S79 | Typed command results; refusal contract | F133 | D45, D46 |

**Phase I: packaging**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S80 | Script fixes that need no decision (tlmgr reset, heredoc, cygpath, sizes, cleanup, version stamps, Typst licence, pyproject licence) | F155, F158, F159, F160, F163, F156, F157, F178, F161 | none |
| S81 | Pinned and hashed downloads; TeX snapshot; strict fallback | F062, F150, F151, F152, F153 | D35 |
| S82 | Hashed bundle lock | F154, F019, F062 | D34 |
| S83 | Unexpected bundle entries | G6.1 | D64 |
| S84 | Generated third-party notices; spec licensing text | F177 | D75 |
| S85 | Permanent bundle identifier | F162 | D38 |
| S86 | Single-source version and pin check | F179 | D72 |

**Phase J: tests, docs, CI and refactors**

| Step | Commit | Findings | Waits on |
|---|---|---|---|
| S87 | Fix tests that cannot fail; WebM measured with libvpx-vp9 | F139, F140, F141, F142, F147, F148, G2.5, F143 | none |
| S88 | Known WebM test handling | F143, G2.5 | D54 |
| S89 | New fast tests (export keys, escaping, in-process protocol, fake manim, UI rule modules, Rust logic) | F136, F137, F138, F174, F128 | none |
| S90 | Test support consolidation; shared C# fixtures | F145, F146, F105 | D53 |
| S91 | Protocol documentation, cancelled code, protocol version | F075, F090, F165 | D40 |
| S92 | Scrub ownership | F071, F121 | D44 |
| S93 | Glyph folder name | F127 | D52 |
| S94 | README, CONTRIBUTING, CHANGELOG, SECURITY, CODEOWNERS, templates | F063 | D80, D72, D65 |
| S95 | Worker log rotation and summaries | F167, F096, F169 | D55 |
| S96 | Windows CI lanes and package job | F063, F061 | D67, D14, D68, D73 |
| S97 | Security scanning, Renovate, coverage, pre-commit | F063 | D77, D76, D78, D71 |
| S98 | Release flow and disabled signing hook | F063 | D73, D74, D72 |
| S99 | Refactors (after the Windows process tests pass) | F088, F081, F072, F073, F083, F089, F118, F119, F131, F102 | D41 |
| S100 | Hand-written scene mode | F089 | D42 |

---

## 7. Decisions needed from the owner

Answering these first unblocks the most work:
- **D33**: bundle Python (the critical finding).
- **D29**: where projects live.
- **D62**: install location for TeX.
- **D65 and D69**: GitLab remote and branches.
- **D79, D43, D37 and D51**: the formatting baseline.

Each decision's full options are in the structured decision list.

| ID | Topic | Recommended | Findings |
|---|---|---|---|
| D01 | Reading of -x^2 | Usual math: -(x^2) | F001, F002 |
| D02 | Number precision | Exact in formulas, refuse bad document numbers | F002, F003, F084 |
| D03 | Unexpected worker errors | Report and keep running | F004, F005, F006 |
| D04 | Size limits | 1000 chars, 64 levels, 500 ticks per axis | F004, F078 |
| D05 | Failed save | Undo the edit and say 'could not save' | F005, F079 |
| D06 | Broken text in project files | Refuse, naming the field; allow tab and newline | F007 |
| D07 | Other text encodings | Accept a UTF-8 BOM, refuse other encodings clearly | F077, F080 |
| D08 | Undo for colours, no-ops, export settings | Commit colour on close; ignore no-ops; export settings undoable | F008, F009, F168 |
| D09 | Refuse hand-edited timelines that render wrong | Refuse on open, naming the beat or id | F012, F013 |
| D10 | Live preview for E = mc^2 | Split adjacent letters into separate symbols | F015 |
| D11 | LaTeX error text | TeX error line plus the 'l.N' line | F016 |
| D12 | Moving labelled axes | Labels move with the axes; recolour unchanged | F018 |
| D13 | Preview progress and cancel | Watch the render folder; keep finished beats | F022 |
| D14 | Windows process tests | Dedicated CI VM, for worker/ and win32 changes plus nightly | F024 |
| D15 | Backup process killer | Keep the helper, start it only when needed, add start-time checks | F024, F027, F102 |
| D16 | Cancel race with the venv launcher | Use the real interpreter for Manim | F026 |
| D17 | Per-beat messages during export | Only previews send them | F032 |
| D18 | Projects from other people | Untrusted: allow only approved math commands | F035, F076 |
| D19 | Previous render will not stop in time | Hold the request and run it when the old render stops | F036 |
| D20 | Window freezes during worker calls | Fix glyph preview now and move worker calls off the UI thread with ordering | F038 |
| D21 | After crashes and automatic cancels | End jobs, reload from disk, 'Cancelled' only for user cancels | F040, F041, G1.4, G5.4 |
| D22 | Open or Sample during an export | Block Open and Sample until the export ends | F042 |
| D23 | How render errors are shown | One-line summary, Show details, Dismiss | F044, F095, F115 |
| D24 | Empty or comma number fields | Empty reverts; comma accepted as decimal point | F045 |
| D25 | View code panel while editing | Update live | F046 |
| D26 | Automatic preview renders | Auto-preview after Open, Sample, Clear cache and preempting exports; undo/redo stay render-free | F049, F087 |
| D27 | Webview lock-down | Cheap hardening | F051, F125 |
| D28 | Where the translator runs | In the Rust shell, with shared test cases | F053 |
| D29 | Where projects live | Add Save As/New, reopen last, prune old temp projects, warn on quit | F054, F169 |
| D30 | Dev and release runtime lookup | Debug uses repo sources; release uses only the bundle and refuses a stale or missing one | F055, F057, F061, F132, F165 |
| D31 | Startup when a tool is missing | Open with 'Worker unavailable' and the reason | F056, G2.6 |
| D32 | Log locations | App log folder in release; shell-ready only on request | F057, F104, F129 |
| D33 | Python inside the installer | Ship the real Python with Manim installed into it | F060 |
| D34 | Python dependency lock | One hashed lock for dev, CI and both bundle scripts | F019, F020, F062, F154 |
| D35 | Bundle download pinning | Pin uv and every download; dated TeX snapshot; strict fallback | F062, F150, F151, F152, F153 |
| D36 | What plain pytest runs | Plain pytest stays the full suite; CI picks the fast subset | F065 |
| D37 | Toolchain versions | Rust 1.93.0 and Node 24, mismatches are errors | F066, F134, F180 |
| D38 | Permanent app identifier | com.mathimator.desktop | F134, F162 |
| D39 | .cursor folder and Python version file | Ignore .cursor/ for everyone; commit .python-version 3.14.9 | F069, G6.2 |
| D40 | Protocol documentation and versioning | Rewrite spec §6, add a protocol version and a 'cancelled' code; no new deps | F075, F090, F165 |
| D41 | How far to refactor | Low-risk extractions now, big splits later | F072, F073, F081, F088, F102, F118, F119, F131 |
| D42 | Hand-written scene mode | Keep it only for folders without project.json | F089 |
| D43 | Python style and type checking | ruff at 120 columns, wider rules, mypy strict | F073, F096, F175, F176 |
| D44 | Who owns scrub logic | TypeScript owns it; remove the worker scrub command | F071, F121 |
| D45 | New Rust dependencies | Add windows-sys and serde directly; no code generators | F104, F132, F133 |
| D46 | Worker refusals in the shell | One TypeScript helper throws on refusals | F133 |
| D47 | macOS | Windows only for now; spec says macOS untested | F107 |
| D48 | Interrupted duration drag | Return to the pre-drag value; support touch and pen | F110 |
| D49 | Playback across beats with no video | Keep the clock running over a blank canvas | F111, F123 |
| D50 | TypeScript type strictness | Stricter index checks and field-path errors; no new dependency | F119, F122 |
| D51 | TypeScript lint and tests | ESLint (type-aware), react-hooks, Prettier, jsdom and Testing Library | F120 |
| D52 | Glyph folder name | Spec says equation/ | F127 |
| D53 | Test layout | Keep phase files and one support.py; share C# fixtures | F146, F149 |
| D54 | Known transparent-WebM test | Fix the measurement first; strict xfail if still failing | F143, G2.5 |
| D55 | Worker log | Clear cache empties it; 5 MB rotation; summaries | F096, F167, F169 |
| D56 | Getting a fresh export | A Re-export action that skips the export cache | G1.2 |
| D57 | Folder moved, deleted or unreachable | Allow it; stop saving and say so; never recreate project.json | G1.3, G5.3, G5.4, G5.5 |
| D58 | Same project in two windows | Lock the project; a second window refuses it | G5.1, G5.6, G5.7, G5.8, G5.9 |
| D59 | Edits made outside the app | Refuse and offer Reload or Overwrite | G5.2 |
| D60 | Manim environment and command line | Isolate Manim and update spec §5 | G2.1, G2.2, G2.3 |
| D61 | Unusual characters and long paths | Run Manim from the project folder with relative paths, fix Manim's quoting, preflight the rest | G4.3, G4.4, G4.5, G4.6, G4.7 |
| D62 | Install location and TeX | Install for all users under Program Files | G4.2 |
| D63 | Beat timing vs video frames | Generated code snaps each beat to whole frames | G3.1, G3.2, G3.3, G3.5, G3.6 |
| D64 | Unexpected files in the bundle folder | Build scripts delete them | G6.1 |
| D65 | GitLab project and remote | GitLab.com, added as a second remote | F063, G6.4 |
| D66 | GitLab tier | Free | F063 |
| D67 | Windows CI runner | Dedicated self-managed Windows VM | F063, F120 |
| D68 | When slow suites run | Nightly and tags, plus a manual MR job | F063 |
| D69 | Branches and merge method | Work on audit/cleanup-gitlab-devops; protected main at 8e49fbd; semi-linear merges | G6.3 |
| D70 | Supported Python versions | >=3.13; test 3.13.17 and 3.14.9 | F020 |
| D71 | Local git hooks | pre-commit, mirrored in CI | F063 |
| D72 | Version bumps and changelog | bump-my-version, a version check in CI, git-cliff | F179 |
| D73 | Release flow | Protected tag triggers build, tests, upload and Release | F063 |
| D74 | Code signing | Stay unsigned for v1, with a disabled hook | F063 |
| D75 | GPL video encoders and notices | Keep x264/x265, ship full notices and a source offer, legal review first | F177 |
| D76 | Dependency update bot | Renovate with spec pin rules | F063 |
| D77 | Security scanners | GitLab SAST and Secret Detection plus OSS audits | F063 |
| D78 | Coverage gate | Report only | F063 |
| D79 | Bringing existing code to the formatters | One bulk commit per language, hidden from blame | F070, F176 |
| D80 | Security contact and code owners | Confidential issues; per-area CODEOWNERS | F063 |
