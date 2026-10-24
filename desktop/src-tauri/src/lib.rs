use std::fs::{self, File, OpenOptions};
use std::future::Future;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::webview::PageLoadEvent;
use tauri::{async_runtime, AppHandle, Emitter, Manager, State, Window, WindowEvent};
use tauri_plugin_dialog::{
    DialogExt, FileDialogBuilder, FilePath, MessageDialogButtons, MessageDialogKind,
    MessageDialogResult,
};

mod equation;
mod protocol;
mod runtime;
mod sha256;
mod temp_project;
mod tex;
mod win32_job;
mod worker;

use equation::{GlyphPreview, GlyphResult};
use protocol::{DialogReply, ErrorCode, ShellError};
use runtime::{Runtime, WorkerFiles};
use worker::{event_of, message_of, Host, Spawned, Spawner, Worker, WorkerProcess};

/// The variable that makes the worker refuse formulas with its text (C9.6, C9.7).
const TEX_ERROR: &str = "MATHIMATOR_TEX_ERROR";
/// supervisor.err is rotated to supervisor.err.1 at a spawn once it is larger than this (C9.8).
const STDERR_LOG_LIMIT: u64 = 1024 * 1024;

/// Locks a mutex, recovering the data if a panicking thread poisoned it.
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

fn unix_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| u64::try_from(duration.as_millis()).unwrap_or(u64::MAX))
        .unwrap_or(0)
}

/// Replaces `file` with `bytes` through a temp file beside it, so that no reader sees half of it.
fn write_atomically(file: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut temp = file.as_os_str().to_owned();
    temp.push(".tmp");
    fs::write(&temp, bytes)?;
    fs::rename(&temp, file).inspect_err(|_| {
        let _ = fs::remove_file(&temp);
    })
}

/// shell.log and supervisor.err go to the repository's out/logs in debug builds and to the app
/// log folder, %LOCALAPPDATA%\<identifier>\logs, in release builds (C9.8, D32).
fn log_dir(app: &AppHandle) -> PathBuf {
    match runtime::repo_root() {
        Some(repo) => repo.join("out").join("logs"),
        None => app
            .path()
            .app_log_dir()
            .unwrap_or_else(|_| std::env::temp_dir().join("Mathimator").join("logs")),
    }
}

/// Appends one timestamped line to shell.log in `logs` (C9.8). Logging never fails its caller.
fn log_line(logs: &Path, line: &str) {
    let _ = fs::create_dir_all(logs);
    let file = logs.join("shell.log");
    if let Ok(mut log) = OpenOptions::new().create(true).append(true).open(file) {
        let _ = writeln!(log, "{} {line}", unix_millis());
    }
}

fn allow_project(app: &AppHandle, path: &Path) -> Result<(), String> {
    let scope = app.asset_protocol_scope();
    scope
        .allow_directory(path, true)
        .map_err(|err| err.to_string())?;
    // The grant above already covers .mathimator while requireLiteralLeadingDot is false. This one
    // keeps the previews there readable should that setting change (F134).
    scope
        .allow_directory(path.join(".mathimator"), true)
        .map_err(|err| err.to_string())?;
    Ok(())
}

/// The command that starts the Python supervisor on a project folder (C9.6). `--init` is only for
/// new temp projects, and `tex_error` makes the worker refuse formulas (C9.7).
fn supervisor_command(
    runtime: &Runtime,
    files: &WorkerFiles,
    project: &Path,
    init: bool,
    tex_error: Option<&str>,
) -> Command {
    let mut command = runtime.python_command(files, "mathimator_worker");
    command
        .arg("--project")
        .arg(project)
        .arg("--python")
        .arg(&files.python);
    if init {
        command.arg("--init");
    }
    match tex_error {
        Some(error) => command.env(TEX_ERROR, error),
        None => command.env_remove(TEX_ERROR),
    };
    command
}

/// The worker's spawner. Each spawn passes on the TeX error of its moment, so that a worker started
/// after a failed self-test refuses formulas (C9.7, C14 Q4).
fn spawner<F>(tex: Arc<Tex>, spawn: F) -> Spawner
where
    F: Fn(&Path, bool, Option<&str>) -> io::Result<Spawned> + Send + Sync + 'static,
{
    Box::new(move |project: &Path, init: bool| spawn(project, init, tex.error().as_deref()))
}

/// Starts the supervisor in a kill-on-close Job Object, with its stderr appended to
/// supervisor.err (C9.6, C9.8).
fn spawn_supervisor(
    runtime: &Runtime,
    files: &WorkerFiles,
    shell: &Shell,
    project: &Path,
    init: bool,
    tex_error: Option<&str>,
) -> io::Result<Spawned> {
    let mut command = supervisor_command(runtime, files, project, init, tex_error);
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let job = win32_job::assign_supervisor(&child).unwrap_or_else(|err| {
        shell.log(&format!("Could not put the worker in a Job Object: {err}."));
        None
    });
    let (Some(stdin), Some(stdout), Some(stderr)) =
        (child.stdin.take(), child.stdout.take(), child.stderr.take())
    else {
        return Err(io::Error::other("the supervisor pipes were not set up"));
    };
    let stderr_log = shell.supervisor_err();
    let log = open_stderr_log(&stderr_log, child.id(), &|line| shell.log(line))
        .inspect_err(|err| shell.log(&format!("Could not open {}: {err}.", stderr_log.display())))
        .ok();
    thread::spawn(move || drain_stderr(log, stderr));
    Ok(Spawned {
        stdin: Box::new(stdin),
        stdout: Box::new(stdout),
        process: Box::new(Supervisor { child, job }),
    })
}

/// Opens supervisor.err for the worker with this pid (C9.8): rotated to supervisor.err.1 once it
/// is over 1 MiB, then appended to under a header for this spawn. A rotation that fails, for
/// example while another program holds the file, is logged, and the file is appended to anyway,
/// so the worker's stderr is still kept (F129).
fn open_stderr_log(path: &Path, pid: u32, log: &dyn Fn(&str)) -> io::Result<File> {
    if fs::metadata(path).is_ok_and(|metadata| metadata.len() > STDERR_LOG_LIMIT) {
        let mut rotated = path.as_os_str().to_owned();
        rotated.push(".1");
        if let Err(err) = fs::rename(path, rotated) {
            log(&format!("Could not rotate {}: {err}.", path.display()));
        }
    }
    if let Some(folder) = path.parent() {
        fs::create_dir_all(folder)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(
        file,
        "--- supervisor pid={pid} started {} ---",
        unix_millis()
    )?;
    Ok(file)
}

/// Copies the worker's stderr into supervisor.err as it arrives, line by line and as bytes, so
/// a crash keeps its traceback and output that is not UTF-8 cannot stop the copy (F129). Without
/// a log the output is still read, so the worker never blocks on a full pipe.
fn drain_stderr(mut log: Option<impl Write>, stderr: impl Read) {
    let mut stderr = BufReader::new(stderr);
    let mut line = Vec::new();
    while stderr
        .read_until(b'\n', &mut line)
        .is_ok_and(|read| read > 0)
    {
        if let Some(log) = &mut log {
            let _ = log.write_all(&line);
        }
        line.clear();
    }
}

/// The supervisor process, and the Job Object that ends it together with its Manim grandchild.
struct Supervisor {
    child: Child,
    job: Option<win32_job::SupervisorJob>,
}

impl WorkerProcess for Supervisor {
    fn exit_code(&mut self, limit: Duration) -> Option<i32> {
        wait_child(&mut self.child, limit).and_then(|status| status.code())
    }

    fn end(&mut self, limit: Duration) -> bool {
        // Closing the Job Object kills the supervisor and everything it started.
        if self.job.take().is_none() {
            let _ = self.child.kill();
        }
        wait_child(&mut self.child, limit).is_some()
    }
}

fn wait_child(child: &mut Child, limit: Duration) -> Option<ExitStatus> {
    let deadline = Instant::now() + limit;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
            _ => return None,
        }
    }
}

/// The shell side of worker output: `worker-event`s, the asset scope, `lastProject`, shell.log,
/// notices and the TeX state (C8.5, C9.4, C9.5, C9.7, C9.8).
struct Shell {
    app: AppHandle,
    /// The folder of shell.log and supervisor.err (C9.8).
    logs: PathBuf,
    state_file: Option<PathBuf>,
    notices: Mutex<Notices>,
    tex: Arc<Tex>,
}

impl Shell {
    fn new(app: AppHandle, logs: PathBuf, tex: Arc<Tex>) -> Self {
        let state_file = app
            .path()
            .app_config_dir()
            .ok()
            .map(|folder| folder.join("state.json"));
        Self {
            app,
            logs,
            state_file,
            notices: Mutex::default(),
            tex,
        }
    }

    fn supervisor_err(&self) -> PathBuf {
        self.logs.join("supervisor.err")
    }

    /// The `lastProject` of state.json, which the launch reopens (C8.5).
    fn last_project(&self) -> Option<PathBuf> {
        let file = self.state_file.as_ref()?;
        read_last_project(file)
            .inspect_err(|err| {
                if err.kind() != io::ErrorKind::NotFound {
                    self.log(&format!("Could not read {}: {err}", file.display()));
                }
            })
            .ok()
    }

    fn status(&self, unavailable: Option<String>) -> AppStatus {
        AppStatus::new(unavailable, self.tex.status(), &lock(&self.notices))
    }
}

impl Host for Shell {
    fn emit(&self, event: Value) {
        let _ = self.app.emit("worker-event", event);
    }

    fn bind(&self, project: &Path) {
        if let Err(err) = allow_project(&self.app, project) {
            self.log(&format!(
                "Could not allow {} in the asset scope: {err}",
                project.display()
            ));
        }
        if let Some(file) = &self.state_file {
            if let Err(err) = write_last_project(file, project) {
                self.log(&format!("Could not write {}: {err}", file.display()));
            }
        }
    }

    fn log(&self, message: &str) {
        log_line(&self.logs, message);
    }

    fn notice(&self, message: String) {
        let notice = lock(&self.notices).add(message);
        self.emit(json!({
            "event": "notice",
            "jobId": null,
            "id": notice.id,
            "message": notice.message,
        }));
    }
}

/// What `app_status` and `restart_worker` return (C9.5).
#[derive(Debug, PartialEq, Serialize)]
struct AppStatus {
    worker: WorkerState,
    /// Why the worker is unavailable.
    reason: Option<String>,
    tex: TexStatus,
    notices: Vec<Notice>,
}

#[derive(Debug, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
enum WorkerState {
    Ready,
    Unavailable,
}

impl AppStatus {
    fn new(unavailable: Option<String>, tex: TexStatus, notices: &Notices) -> Self {
        Self {
            worker: match unavailable {
                Some(_) => WorkerState::Unavailable,
                None => WorkerState::Ready,
            },
            reason: unavailable,
            tex,
            notices: notices.open.clone(),
        }
    }
}

/// Whether formulas can render (C9.5, C9.7).
#[derive(Clone, Debug, PartialEq, Serialize)]
struct TexStatus {
    state: TexState,
    message: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum TexState {
    Checking,
    Ok,
    Failed,
}

impl TexStatus {
    /// Failed from the start when TeX is blocked, else checking until the self-test ends (C9.7).
    fn at_launch(tex_error: Option<String>) -> Self {
        match tex_error {
            Some(message) => Self::checked(Err(message)),
            None => Self {
                state: TexState::Checking,
                message: None,
            },
        }
    }

    fn checked(result: Result<(), String>) -> Self {
        match result {
            Ok(()) => Self {
                state: TexState::Ok,
                message: None,
            },
            Err(message) => Self {
                state: TexState::Failed,
                message: Some(message),
            },
        }
    }

    /// The message formulas are refused with, while TeX has failed.
    fn error(&self) -> Option<&str> {
        match self.state {
            TexState::Failed => self.message.as_deref(),
            TexState::Checking | TexState::Ok => None,
        }
    }

    /// The `tex_status` event that tells the UI this status (C9.5).
    fn event(&self) -> Value {
        json!({
            "event": "tex_status",
            "jobId": null,
            "state": self.state,
            "message": self.message,
        })
    }
}

/// The TeX status, shared by the shell and the worker's spawner (C9.7).
struct Tex(Mutex<TexStatus>);

impl Tex {
    fn new(status: TexStatus) -> Self {
        Self(Mutex::new(status))
    }

    fn status(&self) -> TexStatus {
        lock(&self.0).clone()
    }

    /// The message the worker refuses formulas with, while TeX has failed (C9.7).
    fn error(&self) -> Option<String> {
        lock(&self.0).error().map(str::to_owned)
    }

    /// Records the self-test result, and returns the status it gives.
    fn checked(&self, result: Result<(), String>) -> TexStatus {
        let status = TexStatus::checked(result);
        *lock(&self.0) = status.clone();
        status
    }
}

/// Records the TeX self-test result, logs it and sends `tex_status` (C9.7). A failure blocks
/// formulas (C14 Q4): the running worker hears the result through `set_tex_error`, and every
/// worker started later through MATHIMATOR_TEX_ERROR. Nothing running is stopped.
async fn tex_checked(worker: &Worker, tex: &Tex, host: &dyn Host, result: Result<(), String>) {
    let status = tex.checked(result);
    host.log(&match &status.message {
        Some(message) => format!("The TeX self-test failed: {message}"),
        None => "The TeX self-test passed.".to_owned(),
    });
    host.emit(status.event());
    let message = status.error().map(str::to_owned);
    if let Err(err) = worker.set_tex_error(message).await {
        host.log(&err.message);
    }
}

/// The notices not yet dismissed (C9.5).
#[derive(Default)]
struct Notices {
    last_id: u64,
    open: Vec<Notice>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
struct Notice {
    id: u64,
    message: String,
}

impl Notices {
    fn add(&mut self, message: String) -> Notice {
        self.last_id += 1;
        let notice = Notice {
            id: self.last_id,
            message,
        };
        self.open.push(notice.clone());
        notice
    }

    fn dismiss(&mut self, id: u64) {
        self.open.retain(|notice| notice.id != id);
    }
}

/// `<app_config_dir>\state.json` (C8.5).
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct AppState {
    /// The project the next launch reopens.
    last_project: PathBuf,
}

fn read_last_project(file: &Path) -> io::Result<PathBuf> {
    let state: AppState = serde_json::from_slice(&fs::read(file)?)?;
    Ok(state.last_project)
}

/// Rewrites state.json with `project` as `lastProject`, atomically (C8.5).
fn write_last_project(file: &Path, project: &Path) -> io::Result<()> {
    let state = AppState {
        last_project: project.to_owned(),
    };
    if let Some(folder) = file.parent() {
        fs::create_dir_all(folder)?;
    }
    write_atomically(file, &serde_json::to_vec(&state)?)
}

/// One native dialog at a time (F130). A dialog command that arrives while another is pending
/// is answered `{cancelled: true}`, and a close request leaves the window open.
#[derive(Default)]
struct Dialogs(AtomicBool);

struct DialogTurn<'a>(&'a AtomicBool);

impl Dialogs {
    fn begin(&self) -> Option<DialogTurn<'_>> {
        (!self.0.swap(true, Ordering::SeqCst)).then_some(DialogTurn(&self.0))
    }
}

impl Drop for DialogTurn<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}

/// Parents a file dialog to the window, which makes it modal. The dialog plugin does the same,
/// and like it this skips Linux.
fn parented<R: tauri::Runtime>(
    builder: FileDialogBuilder<R>,
    window: &Window<R>,
) -> FileDialogBuilder<R> {
    #[cfg(any(windows, target_os = "macos"))]
    let builder = builder.set_parent(window);
    #[cfg(not(any(windows, target_os = "macos")))]
    let _ = window;
    builder
}

fn picked_path(picked: FilePath) -> Result<PathBuf, ShellError> {
    picked
        .into_path()
        .map_err(|err| ShellError::new(ErrorCode::Io, err.to_string()))
}

/// The first line of the warning before a touched temp project is left (C8.5).
const TEMP_WARNING: &str = "This project is in a temporary folder that Windows may delete.";
const SAVE_AS: &str = "Save As\u{2026}";

/// What leaves the bound project: quitting (C8.5, C14 Q8), or Open, Sample or New (C14 Q2).
#[derive(Clone, Copy)]
enum Leaving {
    Quit,
    Switch,
}

/// What the user picked in the temp-project warning.
#[derive(Debug, PartialEq)]
enum Choice {
    SaveAs,
    Leave,
    Stay,
}

impl Leaving {
    /// The warning's question, and the label of the button that leaves without saving.
    fn words(self) -> (&'static str, &'static str) {
        match self {
            Self::Quit => ("Save it before quitting?", "Quit"),
            Self::Switch => ("Save it before switching projects?", "Don't Save"),
        }
    }

    /// Reads the warning's result. Closing the warning stays, as Cancel does.
    fn choice(self, result: &MessageDialogResult) -> Choice {
        let (_, leave_label) = self.words();
        match result {
            MessageDialogResult::Custom(label) if label == SAVE_AS => Choice::SaveAs,
            MessageDialogResult::Custom(label) if label == leave_label => Choice::Leave,
            _ => Choice::Stay,
        }
    }
}

/// How leaving the bound project ended.
enum Left {
    /// Nothing needed saving, or the user chose not to save it.
    Unsaved,
    /// Save As moved the project; its `state` reply.
    Saved(Value),
    /// The user stayed. The reply to return instead: `{cancelled: true}`, or a Save As refusal.
    Stayed(DialogReply),
}

/// Asks before leaving a touched temp project, with Save As (C8.5, C14 Q2, Q8). An untouched
/// one is left without a word.
async fn leave(
    app: &AppHandle,
    window: &Window,
    worker: &Worker,
    leaving: Leaving,
) -> Result<Left, ShellError> {
    if !worker.is_touched_temp_project().await {
        return Ok(Left::Unsaved);
    }
    let (question, leave_label) = leaving.words();
    let result = app
        .dialog()
        .message(format!("{TEMP_WARNING} {question}"))
        .parent(window)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::YesNoCancelCustom(
            SAVE_AS.to_owned(),
            leave_label.to_owned(),
            "Cancel".to_owned(),
        ))
        .blocking_show_with_result();
    Ok(match leaving.choice(&result) {
        Choice::Leave => Left::Unsaved,
        Choice::Stay => Left::Stayed(DialogReply::CANCELLED),
        Choice::SaveAs => match save_as(app, window, worker).await? {
            DialogReply::Worker(reply) if event_of(&reply) == Some("state") => Left::Saved(reply),
            reply => Left::Stayed(reply),
        },
    })
}

/// Save As (C8.5): a parented folder picker, then `save_as`. Returns the reply, or
/// `{cancelled: true}` when no folder was picked.
async fn save_as(
    app: &AppHandle,
    window: &Window,
    worker: &Worker,
) -> Result<DialogReply, ShellError> {
    let picked = parented(app.dialog().file(), window)
        .set_title("Save project as")
        .blocking_pick_folder();
    let Some(folder) = picked else {
        return Ok(DialogReply::CANCELLED);
    };
    worker
        .save_project_as(&picked_path(folder)?)
        .await
        .map(DialogReply::Worker)
}

/// Open, Sample and New (C14 Q2): asks before a touched temp project is left, then awaits `open`,
/// which binds another project, or gives `None` when the user picked none. Like every future,
/// `open` does nothing until it is awaited, so nothing runs when the user stays.
async fn switch_project(
    app: &AppHandle,
    window: &Window,
    worker: &Worker,
    host: &dyn Host,
    open: impl Future<Output = Result<Option<Value>, ShellError>>,
) -> Result<DialogReply, ShellError> {
    match leave(app, window, worker, Leaving::Switch).await? {
        Left::Unsaved => Ok(open
            .await?
            .map_or(DialogReply::CANCELLED, DialogReply::Worker)),
        Left::Saved(state) => Ok(DialogReply::Worker(after_save_as(host, state, open.await))),
        Left::Stayed(reply) => Ok(reply),
    }
}

/// What Open, Sample or New returns after a Save As in the leave warning, which already moved
/// the project. When the command binds no other project, the UI gets the Save As state, so that
/// it follows the move, and why the command failed becomes a notice.
fn after_save_as(
    host: &dyn Host,
    saved: Value,
    opened: Result<Option<Value>, ShellError>,
) -> Value {
    let reason = match opened {
        Ok(Some(reply)) if event_of(&reply) == Some("state") => return reply,
        Ok(Some(reply)) => message_of(&reply),
        Ok(None) => return saved,
        Err(err) => err.message,
    };
    let reason = reason.trim_end_matches('.');
    host.notice(format!(
        "The project was saved, but the next project could not be opened: {reason}."
    ));
    saved
}

/// The quit warning (C8.5, C14 Q8), on the window's close request. The window closes unless the
/// user stays: Cancel, a cancelled picker, or a Save As that failed, whose reason is shown. While
/// another dialog command runs, the window stays open.
async fn confirm_quit(window: Window) {
    let app = window.app_handle();
    let dialogs = app.state::<Dialogs>();
    let Some(_turn) = dialogs.begin() else {
        return;
    };
    let reason = match leave(app, &window, &app.state::<Worker>(), Leaving::Quit).await {
        Ok(Left::Unsaved | Left::Saved(_)) => {
            let _ = window.destroy();
            return;
        }
        Ok(Left::Stayed(DialogReply::Worker(reply))) => message_of(&reply),
        Ok(Left::Stayed(_)) => return,
        Err(err) => err.message,
    };
    app.dialog()
        .message(reason)
        .parent(&window)
        .kind(MessageDialogKind::Error)
        .blocking_show();
}

#[tauri::command]
async fn app_status(
    worker: State<'_, Worker>,
    shell: State<'_, Arc<Shell>>,
) -> Result<AppStatus, ShellError> {
    Ok(shell.status(worker.unavailable().await))
}

#[tauri::command]
async fn restart_worker(
    worker: State<'_, Worker>,
    shell: State<'_, Arc<Shell>>,
) -> Result<AppStatus, ShellError> {
    Ok(shell.status(worker.restart().await?))
}

#[tauri::command]
fn dismiss_notice(shell: State<'_, Arc<Shell>>, id: u64) {
    lock(&shell.notices).dismiss(id);
}

#[tauri::command]
fn reserve_job_id(worker: State<'_, Worker>, prefix: String) -> Result<String, ShellError> {
    worker.reserve_job_id(&prefix)
}

#[tauri::command]
async fn get_state(worker: State<'_, Worker>) -> Result<Value, ShellError> {
    worker.get_state().await
}

#[tauri::command]
async fn edit_document(worker: State<'_, Worker>, edit: Value) -> Result<Value, ShellError> {
    worker.edit_document(edit).await
}

#[tauri::command]
async fn undo_document(worker: State<'_, Worker>) -> Result<Value, ShellError> {
    worker.undo_document().await
}

#[tauri::command]
async fn redo_document(worker: State<'_, Worker>) -> Result<Value, ShellError> {
    worker.redo_document().await
}

#[tauri::command]
async fn clear_cache(worker: State<'_, Worker>) -> Result<Value, ShellError> {
    worker.clear_cache().await
}

#[tauri::command]
async fn reload_project(worker: State<'_, Worker>) -> Result<Value, ShellError> {
    worker.reload_project().await
}

#[tauri::command]
async fn overwrite_project(worker: State<'_, Worker>) -> Result<Value, ShellError> {
    worker.overwrite_project().await
}

#[tauri::command]
async fn new_project(
    app: AppHandle,
    window: Window,
    worker: State<'_, Worker>,
    shell: State<'_, Arc<Shell>>,
    dialogs: State<'_, Dialogs>,
) -> Result<DialogReply, ShellError> {
    let Some(_turn) = dialogs.begin() else {
        return Ok(DialogReply::CANCELLED);
    };
    let open = async { worker.new_project().await.map(Some) };
    switch_project(&app, &window, &worker, &**shell, open).await
}

#[tauri::command]
async fn open_sample(
    app: AppHandle,
    window: Window,
    worker: State<'_, Worker>,
    runtime: State<'_, Runtime>,
    shell: State<'_, Arc<Shell>>,
    dialogs: State<'_, Dialogs>,
) -> Result<DialogReply, ShellError> {
    let sample = match &runtime.worker {
        Ok(files) => files.sample.clone(),
        Err(reason) => return Err(ShellError::new(ErrorCode::WorkerUnavailable, reason)),
    };
    let Some(_turn) = dialogs.begin() else {
        return Ok(DialogReply::CANCELLED);
    };
    let open = async { worker.open_sample(sample).await.map(Some) };
    switch_project(&app, &window, &worker, &**shell, open).await
}

#[tauri::command]
async fn open_project_dialog(
    app: AppHandle,
    window: Window,
    worker: State<'_, Worker>,
    shell: State<'_, Arc<Shell>>,
    dialogs: State<'_, Dialogs>,
) -> Result<DialogReply, ShellError> {
    let Some(_turn) = dialogs.begin() else {
        return Ok(DialogReply::CANCELLED);
    };
    let open = async {
        let picked = parented(app.dialog().file(), &window)
            .set_title("Open Mathimator project")
            .blocking_pick_folder();
        match picked {
            Some(folder) => worker.open_project(&picked_path(folder)?).await.map(Some),
            None => Ok(None),
        }
    };
    switch_project(&app, &window, &worker, &**shell, open).await
}

#[tauri::command]
async fn save_project_as_dialog(
    app: AppHandle,
    window: Window,
    worker: State<'_, Worker>,
    dialogs: State<'_, Dialogs>,
) -> Result<DialogReply, ShellError> {
    let Some(_turn) = dialogs.begin() else {
        return Ok(DialogReply::CANCELLED);
    };
    save_as(&app, &window, &worker).await
}

#[tauri::command]
async fn export_code_dialog(
    app: AppHandle,
    window: Window,
    worker: State<'_, Worker>,
    dialogs: State<'_, Dialogs>,
) -> Result<DialogReply, ShellError> {
    let Some(_turn) = dialogs.begin() else {
        return Ok(DialogReply::CANCELLED);
    };
    let reply = worker.generated_code().await?;
    if event_of(&reply) != Some("code") {
        return Ok(DialogReply::Worker(reply));
    }
    let Some(source) = reply.get("source").and_then(Value::as_str) else {
        return Err(ShellError::new(
            ErrorCode::Io,
            "The worker sent no generated code.",
        ));
    };
    let picked = parented(app.dialog().file(), &window)
        .set_title("Export code")
        .set_file_name("scene.py")
        .add_filter("Python", &["py"])
        .blocking_save_file();
    let Some(file) = picked else {
        return Ok(DialogReply::CANCELLED);
    };
    let path = picked_path(file)?;
    fs::write(&path, source).map_err(|err| {
        ShellError::new(
            ErrorCode::Io,
            format!("Could not write {}: {err}.", path.display()),
        )
    })?;
    Ok(DialogReply::Written {
        path: path.display().to_string(),
    })
}

#[tauri::command]
async fn render_preview(worker: State<'_, Worker>, job_id: String) -> Result<(), ShellError> {
    worker.render_preview(job_id).await
}

#[tauri::command]
async fn export_movie(
    worker: State<'_, Worker>,
    job_id: String,
    force: Option<bool>,
) -> Result<(), ShellError> {
    worker.export_movie(job_id, force.unwrap_or(false)).await
}

#[tauri::command]
async fn cancel_render(worker: State<'_, Worker>, job_id: String) -> Result<(), ShellError> {
    worker.cancel_render(job_id).await
}

/// Opens a project without a dialog, for the journey tests. Release builds have it only with the
/// `test-hooks` feature (D27).
#[cfg(any(debug_assertions, feature = "test-hooks"))]
#[tauri::command]
async fn open_project_path(worker: State<'_, Worker>, path: String) -> Result<Value, ShellError> {
    worker.open_project(Path::new(&path)).await
}

#[tauri::command]
async fn cancel_equation_preview(
    glyph: State<'_, Arc<GlyphPreview>>,
    slot: String,
    generation: u64,
) -> Result<(), ShellError> {
    equation::check_arguments(&slot, generation)?;
    glyph.cancel(&slot, generation);
    Ok(())
}

/// Compiles a formula's glyph on the blocking pool, so that a cancel or the next keystroke can
/// end it, and the window never waits for Typst (C9.9, D20).
#[tauri::command]
async fn compile_equation_preview(
    worker: State<'_, Worker>,
    glyph: State<'_, Arc<GlyphPreview>>,
    latex: String,
    slot: String,
    generation: u64,
) -> Result<GlyphResult, ShellError> {
    equation::check_arguments(&slot, generation)?;
    let Some(project) = worker.project() else {
        return Ok(GlyphResult::error("No project is open."));
    };
    let glyph = Arc::clone(&glyph);
    async_runtime::spawn_blocking(move || glyph.compile(&project, &latex, &slot, generation))
        .await
        .map_err(|err| {
            ShellError::new(ErrorCode::Io, format!("The equation preview failed: {err}"))
        })
}

/// Writes the shell-ready marker, only when MATHIMATOR_SHELL_READY_FILE names a file (D32).
#[tauri::command]
fn note_shell_ready() -> Result<(), ShellError> {
    match std::env::var_os(runtime::SHELL_READY_FILE).filter(|file| !file.is_empty()) {
        Some(file) => write_ready_marker(Path::new(&file)),
        None => Ok(()),
    }
}

fn write_ready_marker(file: &Path) -> Result<(), ShellError> {
    fs::write(file, format!("ready {}\n", unix_millis())).map_err(|err| {
        ShellError::new(
            ErrorCode::Io,
            format!("Could not write {}: {err}.", file.display()),
        )
    })
}

/// The THIRD_PARTY_NOTICES.txt the installer ships, read-only, for the Notices panel (D75).
#[tauri::command]
async fn third_party_notices(runtime: State<'_, Runtime>) -> Result<String, ShellError> {
    runtime
        .notices
        .read()
        .map_err(|message| ShellError::new(ErrorCode::Io, message))
}

/// Runs the TeX self-test once the launch is done (C8.5 launch step 5, C9.7).
async fn check_tex(app: AppHandle) {
    let (worker, shell) = (app.state::<Worker>(), app.state::<Arc<Shell>>());
    if shell.tex.status().state != TexState::Checking {
        return;
    }
    worker.launched().await;
    let runtime = Runtime::clone(&app.state::<Runtime>());
    let result = async_runtime::spawn_blocking(move || tex::self_test(&runtime))
        .await
        .unwrap_or_else(|err| Err(format!("The TeX self-test stopped: {err}")));
    tex_checked(&worker, &shell.tex, &**shell, result).await;
}

/// Registers the Tauri commands, each with the names the UI passes its arguments by: Tauri's
/// camelCase of the Rust parameter names. Tests keep each name with the text of its `cfg`
/// attribute ("" when it has none) and those argument names for the `commands.json` check (C9.1),
/// so every build checks the release surface.
macro_rules! commands {
    ($($(#[$cfg:meta])* $command:ident($($arg:ident),*)),* $(,)?) => {
        #[cfg(test)]
        const COMMANDS: &[(&str, &str, &[&str])] = &[$((
            stringify!($command),
            concat!($(stringify!($cfg)),*),
            &[$(stringify!($arg)),*],
        )),*];

        fn invoke_handler() -> impl Fn(tauri::ipc::Invoke) -> bool + Send + Sync + 'static {
            tauri::generate_handler![$($(#[$cfg])* $command),*]
        }
    };
}

commands![
    app_status(),
    restart_worker(),
    dismiss_notice(id),
    reserve_job_id(prefix),
    get_state(),
    edit_document(edit),
    undo_document(),
    redo_document(),
    clear_cache(),
    reload_project(),
    overwrite_project(),
    new_project(),
    open_sample(),
    open_project_dialog(),
    save_project_as_dialog(),
    export_code_dialog(),
    render_preview(jobId),
    export_movie(jobId, force),
    cancel_render(jobId),
    #[cfg(any(debug_assertions, feature = "test-hooks"))]
    open_project_path(path),
    cancel_equation_preview(slot, generation),
    compile_equation_preview(latex, slot, generation),
    note_shell_ready(),
    third_party_notices(),
];

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // Nothing here fails: a missing runtime or a worker that cannot start leaves the window
            // open with "Worker unavailable" and the reason (C9.5, D31).
            let handle = app.handle();
            let logs = log_dir(handle);
            let runtime = runtime::resolve(handle, &|line| log_line(&logs, line));
            let tex = Arc::new(Tex::new(TexStatus::at_launch(runtime.tex_error.clone())));
            let shell = Arc::new(Shell::new(handle.clone(), logs, Arc::clone(&tex)));
            let spawn = runtime.worker.clone().map(|files| {
                let (runtime, shell) = (runtime.clone(), Arc::clone(&shell));
                spawner(
                    tex,
                    move |project: &Path, init: bool, tex_error: Option<&str>| {
                        spawn_supervisor(&runtime, &files, &shell, project, init, tex_error)
                    },
                )
            });
            let worker = Worker::launch(
                shell.clone(),
                spawn,
                std::env::temp_dir(),
                shell.last_project(),
                shell.supervisor_err(),
            );
            app.manage(Arc::new(GlyphPreview::new(runtime.typst.clone())));
            app.manage(runtime);
            app.manage(worker);
            app.manage(shell);
            app.manage(Dialogs::default());
            async_runtime::spawn(check_tex(handle.clone()));
            let pruning = handle.clone();
            async_runtime::spawn(async move {
                pruning.state::<Worker>().prune_temp_projects().await;
            });
            Ok(())
        })
        .on_page_load(|webview, payload| {
            // A reload, or a renderer restart, starts the UI's glyph generations at 1 again (C9.9).
            if payload.event() == PageLoadEvent::Started {
                if let Some(glyph) = webview.try_state::<Arc<GlyphPreview>>() {
                    glyph.reset();
                }
            }
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                async_runtime::spawn(confirm_quit(window.clone()));
            }
        })
        .invoke_handler(invoke_handler())
        .run(tauri::generate_context!())
        .expect("error while running Mathimator");
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::ffi::OsStr;

    use super::*;
    use crate::runtime::NoticesFile;

    #[test]
    fn the_registered_commands_are_the_commands_json_list() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol/commands.json"
        ))
        .unwrap();
        // Release builds register a debugOnly command only with the test-hooks feature (D27).
        let debug_only = r#"cfg(any(debug_assertions, feature = "test-hooks"))"#;
        let listed: Vec<(&str, &str, Vec<&str>)> = fixture["tauriCommands"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| {
                let gate = if entry["debugOnly"] == true {
                    debug_only
                } else {
                    ""
                };
                let args = entry["args"].as_array().unwrap();
                let args = args.iter().map(|arg| arg.as_str().unwrap()).collect();
                (entry["name"].as_str().unwrap(), gate, args)
            })
            .collect();
        let registered: Vec<(&str, &str, Vec<&str>)> = COMMANDS
            .iter()
            .map(|&(name, gate, args)| (name, gate, args.to_vec()))
            .collect();
        let missing: Vec<_> = listed
            .iter()
            .filter(|command| !registered.contains(command))
            .collect();
        let unlisted: Vec<_> = registered
            .iter()
            .filter(|command| !listed.contains(command))
            .collect();
        assert!(
            missing.is_empty(),
            "not registered with this gate and these arguments: {missing:?}"
        );
        assert!(
            unlisted.is_empty(),
            "not in commands.json with this gate and these arguments: {unlisted:?}"
        );
    }

    #[test]
    fn the_app_keeps_its_identifier_and_the_webview_gets_core_default_only() {
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let capability: Value =
            serde_json::from_str(include_str!("../capabilities/default.json")).unwrap();
        assert_eq!(config["identifier"], "com.mathimator.desktop");
        assert_eq!(capability["permissions"], json!(["core:default"]));
    }

    #[test]
    fn the_csp_admits_the_ipc_and_the_previews_but_no_remote_or_inline_script() {
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let csp = config["app"]["security"]["csp"].as_object().unwrap();
        let sources = |directive: &str| -> Vec<&str> {
            csp[directive]
                .as_str()
                .unwrap()
                .split_whitespace()
                .collect()
        };
        assert_eq!(sources("default-src"), ["'self'"]);
        assert!(
            !csp.contains_key("script-src"),
            "scripts follow default-src"
        );
        assert!(sources("connect-src").contains(&"http://ipc.localhost"));
        // The previews are fetched over the asset protocol and shown from blob: URLs.
        for directive in ["connect-src", "img-src", "media-src"] {
            assert!(
                sources(directive).contains(&"http://asset.localhost"),
                "{directive}"
            );
        }
        for directive in ["img-src", "media-src"] {
            assert!(sources(directive).contains(&"blob:"), "{directive}");
        }
        let remote: Vec<&str> = csp
            .keys()
            .flat_map(|directive| sources(directive))
            .filter(|source| source.starts_with("http") && !source.ends_with(".localhost"))
            .collect();
        assert!(remote.is_empty(), "{remote:?}");
    }

    #[test]
    fn the_ready_marker_holds_ready_and_the_time() {
        let folder = scratch("ready");
        let file = folder.join("shell-ready.txt");
        write_ready_marker(&file).unwrap();
        let text = fs::read_to_string(&file).unwrap();
        let failed = write_ready_marker(&folder.join("missing").join("shell-ready.txt"));
        let _ = fs::remove_dir_all(&folder);
        let millis = text
            .strip_prefix("ready ")
            .and_then(|rest| rest.strip_suffix('\n'));
        assert!(
            millis.is_some_and(|millis| millis.parse::<u64>().is_ok()),
            "{text:?}"
        );
        assert_eq!(failed.unwrap_err().code, ErrorCode::Io);
    }

    #[test]
    fn shell_log_lines_are_appended_after_a_timestamp() {
        let folder = scratch("shell-log");
        let logs = folder.join("logs");
        log_line(&logs, "Started a worker on C:\\p.");
        log_line(&logs, "Stopped the worker.");
        let text = fs::read_to_string(logs.join("shell.log")).unwrap();
        let _ = fs::remove_dir_all(&folder);
        let lines: Vec<(&str, &str)> = text
            .lines()
            .map(|line| line.split_once(' ').unwrap())
            .collect();
        assert!(
            lines
                .iter()
                .all(|(millis, _)| millis.parse::<u64>().is_ok()),
            "{text}"
        );
        let messages: Vec<&str> = lines.iter().map(|(_, message)| *message).collect();
        assert_eq!(
            messages,
            ["Started a worker on C:\\p.", "Stopped the worker."]
        );
    }

    #[test]
    fn a_failed_atomic_write_leaves_no_temp_file() {
        let folder = scratch("atomic");
        let file = folder.join("state.json");
        // A folder that is not empty cannot be replaced by the rename.
        fs::create_dir_all(file.join("kept")).unwrap();
        let written = write_atomically(&file, b"{}");
        let left: Vec<_> = fs::read_dir(&folder)
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect();
        let _ = fs::remove_dir_all(&folder);
        assert!(written.is_err());
        assert_eq!(left, ["state.json"]);
    }

    #[test]
    fn a_picked_path_must_be_a_file_path() {
        let folder = PathBuf::from("C:\\Lessons\\waves");
        assert_eq!(picked_path(FilePath::Path(folder.clone())).unwrap(), folder);
        let url = FilePath::Url("https://example.invalid/waves".parse().unwrap());
        assert_eq!(picked_path(url).unwrap_err().code, ErrorCode::Io);
    }

    #[test]
    fn notices_stay_until_dismissed() {
        let mut notices = Notices::default();
        let first = notices.add("Could not reopen C:\\a.".to_owned());
        let second = notices.add("Could not reopen C:\\b.".to_owned());
        assert_eq!((first.id, second.id), (1, 2));
        notices.dismiss(7);
        assert_eq!(notices.open.len(), 2);
        notices.dismiss(first.id);
        let open: Vec<_> = notices
            .open
            .iter()
            .map(|notice| (notice.id, notice.message.as_str()))
            .collect();
        assert_eq!(open, [(2, "Could not reopen C:\\b.")]);
    }

    #[test]
    fn state_json_round_trips_the_last_project() {
        let folder = scratch("state");
        let file = folder.join("config").join("state.json");
        assert_eq!(
            read_last_project(&file).unwrap_err().kind(),
            io::ErrorKind::NotFound
        );
        write_last_project(&file, Path::new("C:\\Users\\me\\Lessons\\waves")).unwrap();
        write_last_project(&file, Path::new("C:\\Users\\me\\Lessons\\sine")).unwrap();
        let saved: Value = serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
        let read = read_last_project(&file).unwrap();
        let left: Vec<_> = fs::read_dir(file.parent().unwrap())
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect();
        let _ = fs::remove_dir_all(&folder);
        assert_eq!(
            saved,
            json!({"lastProject": "C:\\Users\\me\\Lessons\\sine"})
        );
        assert_eq!(read, Path::new("C:\\Users\\me\\Lessons\\sine"));
        assert_eq!(left, ["state.json"], "a temp file was left");
    }

    #[test]
    fn a_state_json_without_a_last_project_is_an_error() {
        let folder = scratch("state-invalid");
        let file = folder.join("state.json");
        let refused = ["{", "{}", "{\"lastProject\": 3}"].map(|text| {
            fs::write(&file, text).unwrap();
            read_last_project(&file).is_err()
        });
        let _ = fs::remove_dir_all(&folder);
        assert_eq!(refused, [true; 3]);
    }

    #[test]
    fn the_temp_project_warning_reads_its_buttons() {
        let custom = |label: &str| MessageDialogResult::Custom(label.to_owned());
        for leaving in [Leaving::Quit, Leaving::Switch] {
            let (_, leave_label) = leaving.words();
            assert_eq!(leaving.choice(&custom(SAVE_AS)), Choice::SaveAs);
            assert_eq!(leaving.choice(&custom(leave_label)), Choice::Leave);
            assert_eq!(leaving.choice(&custom("Cancel")), Choice::Stay);
            assert_eq!(leaving.choice(&MessageDialogResult::Cancel), Choice::Stay);
        }
        assert_eq!(Leaving::Quit.choice(&custom("Don't Save")), Choice::Stay);
        assert_eq!(Leaving::Switch.choice(&custom("Quit")), Choice::Stay);
    }

    /// Keeps the notices the shell is asked to add.
    #[derive(Default)]
    struct Notified(Mutex<Vec<String>>);

    impl Host for Notified {
        fn emit(&self, _event: Value) {}

        fn bind(&self, _project: &Path) {}

        fn log(&self, _message: &str) {}

        fn notice(&self, message: String) {
            lock(&self.0).push(message);
        }
    }

    fn saved_state() -> Value {
        json!({"event": "state", "jobId": "rpc-3", "projectPath": "C:\\Lessons\\waves"})
    }

    #[test]
    fn a_switch_after_save_as_returns_its_own_state_or_the_saved_one_when_cancelled() {
        let opened =
            json!({"event": "state", "jobId": "rpc-4", "projectPath": "C:\\Lessons\\sine"});
        let host = Notified::default();
        assert_eq!(
            after_save_as(&host, saved_state(), Ok(Some(opened.clone()))),
            opened
        );
        assert_eq!(after_save_as(&host, saved_state(), Ok(None)), saved_state());
        assert!(lock(&host.0).is_empty());
    }

    #[test]
    fn a_switch_that_fails_after_save_as_returns_the_saved_state_and_a_notice() {
        let refused = json!({
            "event": "rejected",
            "jobId": "rpc-4",
            "code": "not_a_project",
            "message": "That folder is not a Mathimator project.",
        });
        let failed = ShellError::new(ErrorCode::Timeout, "The worker did not answer in time.");
        let host = Notified::default();
        assert_eq!(
            after_save_as(&host, saved_state(), Ok(Some(refused))),
            saved_state()
        );
        assert_eq!(
            after_save_as(&host, saved_state(), Err(failed)),
            saved_state()
        );
        assert_eq!(
            *lock(&host.0),
            [
                "The project was saved, but the next project could not be opened: That folder is not a Mathimator project.",
                "The project was saved, but the next project could not be opened: The worker did not answer in time.",
            ]
        );
    }

    #[test]
    fn app_status_keeps_a_notice_until_it_is_dismissed() {
        let mut notices = Notices::default();
        let notice = notices.add("Could not reopen C:\\a. A new project was started.".to_owned());
        let checking = TexStatus::at_launch(None);
        let first = AppStatus::new(None, checking.clone(), &notices);
        assert_eq!(
            serde_json::to_value(&first).unwrap(),
            json!({
                "worker": "ready",
                "reason": null,
                "tex": {"state": "checking", "message": null},
                "notices": [{"id": 1, "message": "Could not reopen C:\\a. A new project was started."}],
            })
        );
        assert_eq!(AppStatus::new(None, checking.clone(), &notices), first);
        notices.dismiss(notice.id);
        assert!(AppStatus::new(None, checking, &notices).notices.is_empty());
    }

    #[test]
    fn app_status_reports_an_unavailable_worker_and_a_failed_tex() {
        let status = AppStatus::new(
            Some("The installed runtime is incomplete (python). Reinstall Mathimator.".to_owned()),
            TexStatus::at_launch(Some(tex::MISSING.to_owned())),
            &Notices::default(),
        );
        assert_eq!(
            serde_json::to_value(status).unwrap(),
            json!({
                "worker": "unavailable",
                "reason": "The installed runtime is incomplete (python). Reinstall Mathimator.",
                "tex": {"state": "failed", "message": tex::MISSING},
                "notices": [],
            })
        );
    }

    #[test]
    fn only_a_failed_tex_check_blocks_formulas() {
        assert_eq!(TexStatus::at_launch(None).error(), None);
        assert_eq!(TexStatus::checked(Ok(())).error(), None);
        assert_eq!(
            TexStatus::checked(Err("LaTeX is not working.".to_owned())).error(),
            Some("LaTeX is not working.")
        );
        assert_eq!(
            TexStatus::at_launch(Some(tex::MISSING.to_owned())).error(),
            Some(tex::MISSING)
        );
    }

    #[test]
    fn tex_status_events_carry_the_state_and_the_message() {
        assert_eq!(
            TexStatus::checked(Ok(())).event(),
            json!({"event": "tex_status", "jobId": null, "state": "ok", "message": null})
        );
        assert_eq!(
            TexStatus::checked(Err("LaTeX is not working.".to_owned())).event(),
            json!({
                "event": "tex_status",
                "jobId": null,
                "state": "failed",
                "message": "LaTeX is not working.",
            })
        );
    }

    #[test]
    fn the_tex_error_follows_the_latest_check() {
        let tex = Tex::new(TexStatus::at_launch(None));
        assert_eq!(tex.error(), None);
        tex.checked(Err("LaTeX is not working.".to_owned()));
        assert_eq!(tex.error().as_deref(), Some("LaTeX is not working."));
        assert_eq!(tex.status().state, TexState::Failed);
        tex.checked(Ok(()));
        assert_eq!(tex.error(), None);
    }

    fn files() -> WorkerFiles {
        WorkerFiles {
            python: PathBuf::from("C:\\rt\\python\\python.exe"),
            source: PathBuf::from("C:\\rt\\worker"),
            sample: PathBuf::from("C:\\rt\\sample.json"),
        }
    }

    fn installed_runtime() -> Runtime {
        Runtime {
            worker: Ok(files()),
            typst: Err("none".to_owned()),
            tinytex_bin: None,
            tex_error: None,
            work_dir: PathBuf::from("C:\\Users\\me\\AppData\\Local\\com.mathimator.desktop"),
            notices: NoticesFile {
                path: None,
                missing: "none".to_owned(),
            },
        }
    }

    fn env_value<'a>(command: &'a Command, key: &str) -> Option<Option<&'a OsStr>> {
        command
            .get_envs()
            .find(|(name, _)| name.eq_ignore_ascii_case(key))
            .map(|(_, value)| value)
    }

    #[test]
    fn the_supervisor_runs_with_the_contract_command_line() {
        let project = Path::new("C:\\Users\\me\\Lessons\\waves");
        let command = supervisor_command(&installed_runtime(), &files(), project, true, None);
        assert_eq!(
            command.get_program(),
            Path::new("C:\\rt\\python\\python.exe")
        );
        let args: Vec<&OsStr> = command.get_args().collect();
        assert_eq!(
            args,
            [
                "-P",
                "-m",
                "mathimator_worker",
                "--project",
                "C:\\Users\\me\\Lessons\\waves",
                "--python",
                "C:\\rt\\python\\python.exe",
                "--init",
            ]
        );
        assert_eq!(
            command.get_current_dir(),
            Some(Path::new(
                "C:\\Users\\me\\AppData\\Local\\com.mathimator.desktop"
            ))
        );
        assert_eq!(env_value(&command, TEX_ERROR), Some(None));
        let reopened = supervisor_command(&installed_runtime(), &files(), project, false, None);
        assert!(!reopened.get_args().any(|arg| arg == "--init"));
    }

    #[test]
    fn a_tex_error_reaches_the_worker_environment() {
        let command = supervisor_command(
            &installed_runtime(),
            &files(),
            Path::new("C:\\p"),
            false,
            Some(tex::MISSING),
        );
        assert_eq!(
            env_value(&command, TEX_ERROR),
            Some(Some(OsStr::new(tex::MISSING)))
        );
    }

    fn scratch(name: &str) -> PathBuf {
        let folder =
            std::env::temp_dir().join(format!("mathimator-{name}-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&folder);
        fs::create_dir_all(&folder).unwrap();
        folder
    }

    /// Opens supervisor.err as a spawn does, and returns what it logged.
    fn open_logged(path: &Path, pid: u32) -> Vec<String> {
        let logs = RefCell::new(Vec::new());
        drop(open_stderr_log(path, pid, &|line| logs.borrow_mut().push(line.to_owned())).unwrap());
        logs.into_inner()
    }

    #[test]
    fn supervisor_err_gets_a_header_per_spawn_and_rotates_above_1_mib() {
        let folder = scratch("stderr-log");
        let path = folder.join("logs").join("supervisor.err");
        assert!(open_logged(&path, 11).is_empty());
        assert!(open_logged(&path, 12).is_empty());
        let text = fs::read_to_string(&path).unwrap();
        let headers: Vec<&str> = text.lines().collect();
        assert_eq!(headers.len(), 2, "{text}");
        assert!(headers[0].starts_with("--- supervisor pid=11 started "));
        assert!(headers[1].starts_with("--- supervisor pid=12 started "));

        let large = vec![b'x'; STDERR_LOG_LIMIT as usize + 1];
        fs::write(&path, &large).unwrap();
        assert!(open_logged(&path, 13).is_empty());
        let rotated = fs::read(folder.join("logs").join("supervisor.err.1")).unwrap();
        let current = fs::read_to_string(&path).unwrap();
        let _ = fs::remove_dir_all(&folder);
        assert_eq!(rotated, large);
        assert!(
            current.starts_with("--- supervisor pid=13 started "),
            "{current}"
        );
        assert_eq!(current.lines().count(), 1);
    }

    #[test]
    fn supervisor_err_is_appended_to_when_it_cannot_be_rotated() {
        let folder = scratch("stderr-rotation");
        let path = folder.join("supervisor.err");
        let large = vec![b'x'; STDERR_LOG_LIMIT as usize + 1];
        fs::write(&path, &large).unwrap();
        // A folder that is not empty cannot be replaced by the rotation.
        fs::create_dir_all(folder.join("supervisor.err.1").join("kept")).unwrap();
        let logs = open_logged(&path, 14);
        let text = fs::read(&path).unwrap();
        let _ = fs::remove_dir_all(&folder);
        let (kept, appended) = text.split_at(large.len());
        assert_eq!(kept, large);
        assert!(
            appended.starts_with(b"--- supervisor pid=14 started "),
            "{}",
            String::from_utf8_lossy(appended)
        );
        assert_eq!(logs.len(), 1, "{logs:?}");
        assert!(
            logs[0].starts_with(&format!("Could not rotate {}: ", path.display())),
            "{logs:?}"
        );
    }

    #[test]
    fn stderr_is_copied_as_bytes_line_by_line() {
        let stderr: &[u8] =
            b"Traceback (most recent call last):\n  bad \xff byte\nlast line without newline";
        let mut copied = Vec::new();
        drain_stderr(Some(&mut copied), stderr);
        assert_eq!(copied, stderr);
        let mut unread = stderr;
        drain_stderr(None::<Vec<u8>>, &mut unread);
        assert!(unread.is_empty(), "stderr was not read without a log");
    }

    #[test]
    fn a_second_dialog_is_refused_while_one_is_pending() {
        let dialogs = Dialogs::default();
        let turn = dialogs.begin();
        assert!(turn.is_some());
        assert!(dialogs.begin().is_none());
        drop(turn);
        assert!(dialogs.begin().is_some());
    }
}
