use std::fs::{self, OpenOptions};
use std::io::{self, Write};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, Runtime, State, Window};
use tauri_plugin_dialog::{DialogExt, FileDialogBuilder, FilePath};

mod equation;
mod protocol;
mod win32_job;
mod worker;

use equation::GlyphPreview;
use protocol::{ErrorCode, ShellError};
use worker::{Host, Spawned, Worker, WorkerProcess};

fn repo_root() -> Option<PathBuf> {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .ok()
}

/// Locks a mutex, recovering the data if a panicking thread poisoned it.
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

fn unix_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

#[derive(Clone)]
struct ResolvedRuntime {
    python: PathBuf,
    worker_src: PathBuf,
    typst: Option<PathBuf>,
    tinytex_bin: Option<PathBuf>,
    sample_json: Option<PathBuf>,
}

fn bundled_runtime(app: &AppHandle) -> Option<PathBuf> {
    let resource = app.path().resource_dir().ok()?;
    let runtime = resource.join("runtime");
    if runtime.is_dir() {
        Some(runtime)
    } else {
        None
    }
}

fn first_file(root: &Path, relatives: &[&str]) -> Option<PathBuf> {
    for relative in relatives {
        let path = root.join(relative);
        if path.is_file() {
            return Some(path);
        }
    }
    None
}

fn find_tinytex_bin(runtime: &Path) -> Option<PathBuf> {
    let root = runtime.join("tinytex/bin");
    let entries = fs::read_dir(&root).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.join("latex").is_file() || path.join("latex.exe").is_file() {
            return Some(path);
        }
    }
    None
}

fn resolve_runtime(app: &AppHandle) -> Result<ResolvedRuntime, String> {
    let bundled = bundled_runtime(app);
    let repo = repo_root();
    let python = if let Some(value) = std::env::var_os("MATHIMATOR_PYTHON") {
        PathBuf::from(value)
    } else if let Some(path) = bundled.as_ref().and_then(|root| {
        first_file(
            root,
            &[
                "venv/bin/python",
                "venv/bin/python3",
                "venv/Scripts/python.exe",
            ],
        )
    }) {
        path
    } else if let Some(root) = repo.as_ref() {
        let unix = root.join(".venv/bin/python");
        let windows = root.join(".venv/Scripts/python.exe");
        if unix.is_file() {
            unix
        } else {
            windows
        }
    } else {
        return Err("Python interpreter not found".to_string());
    };
    if !python.is_file() {
        return Err(format!(
            "Python interpreter not found at {}",
            python.display()
        ));
    }
    let worker_src = if let Some(root) = bundled
        .as_ref()
        .filter(|root| root.join("worker/mathimator_worker").is_dir())
    {
        root.join("worker")
    } else if let Some(root) = repo.as_ref() {
        root.join("worker")
    } else {
        return Err("worker source was not found".to_string());
    };
    let typst = if let Some(value) = std::env::var_os("MATHIMATOR_TYPST") {
        let path = PathBuf::from(value);
        if !path.is_file() {
            return Err(format!(
                "MATHIMATOR_TYPST is not a file: {}",
                path.display()
            ));
        }
        Some(path)
    } else {
        bundled
            .as_ref()
            .and_then(|root| first_file(root, &["typst/typst", "typst/typst.exe"]))
    };
    let tinytex_bin = if let Some(value) = std::env::var_os("MATHIMATOR_TINYTEX_BIN") {
        let path = PathBuf::from(value);
        if !path.is_dir() {
            return Err(format!(
                "MATHIMATOR_TINYTEX_BIN is not a directory: {}",
                path.display()
            ));
        }
        Some(path)
    } else {
        bundled.as_ref().and_then(|root| find_tinytex_bin(root))
    };
    let sample_json = bundled
        .as_ref()
        .map(|root| root.join("samples/reference-60/project.json"))
        .filter(|path| path.is_file())
        .or_else(|| repo.map(|root| root.join("tests/fixtures/reference-60/project.json")))
        .filter(|path| path.is_file());
    Ok(ResolvedRuntime {
        python,
        worker_src,
        typst,
        tinytex_bin,
        sample_json,
    })
}

/// The folder for shell.log and supervisor.err (C9.8).
fn log_dir() -> PathBuf {
    repo_root()
        .unwrap_or_else(std::env::temp_dir)
        .join("out")
        .join("logs")
}

fn apply_tinytex(command: &mut Command, tinytex_bin: Option<&Path>) {
    let Some(bin) = tinytex_bin else {
        return;
    };
    let mut paths = vec![bin.to_path_buf()];
    if let Some(current) = std::env::var_os("PATH") {
        paths.extend(std::env::split_paths(&current));
    }
    if let Ok(joined) = std::env::join_paths(paths) {
        command.env("PATH", joined);
    }
    for key in [
        "TEXINPUTS",
        "TEXMFHOME",
        "TEXMFLOCAL",
        "TEXMFCNF",
        "TEXMFVAR",
        "TEXMFCONFIG",
    ] {
        command.env_remove(key);
    }
}

fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = command;
    }
}

fn allow_project(app: &AppHandle, path: &Path) -> Result<(), String> {
    let scope = app.asset_protocol_scope();
    scope
        .allow_directory(path, true)
        .map_err(|err| err.to_string())?;
    scope
        .allow_directory(path.join(".mathimator"), true)
        .map_err(|err| err.to_string())?;
    Ok(())
}

/// Starts the Python supervisor on a project folder. `--init` is only for new temp projects.
fn spawn_supervisor(
    runtime: &ResolvedRuntime,
    stderr_log: &Path,
    project: &Path,
    init: bool,
) -> io::Result<Spawned> {
    let mut command = Command::new(&runtime.python);
    command
        .arg("-m")
        .arg("mathimator_worker")
        .arg("--project")
        .arg(project)
        .arg("--python")
        .arg(&runtime.python);
    if init {
        command.arg("--init");
    }
    command
        .env("PYTHONPATH", &runtime.worker_src)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console(&mut command);
    apply_tinytex(&mut command, runtime.tinytex_bin.as_deref());
    if let Some(root) = repo_root() {
        command.current_dir(root);
    }
    let mut child = command.spawn()?;
    let job = win32_job::assign_supervisor(&child);
    let (Some(stdin), Some(stdout), Some(stderr)) =
        (child.stdin.take(), child.stdout.take(), child.stderr.take())
    else {
        return Err(io::Error::other("the supervisor pipes were not set up"));
    };
    let stderr_log = stderr_log.to_path_buf();
    thread::spawn(move || drain_stderr(stderr_log, stderr));
    Ok(Spawned {
        stdin: Box::new(stdin),
        stdout: Box::new(stdout),
        process: Box::new(Supervisor { child, job }),
    })
}

fn drain_stderr(path: PathBuf, mut stderr: std::process::ChildStderr) {
    let mut buffer = Vec::new();
    let _ = std::io::Read::read_to_end(&mut stderr, &mut buffer);
    if buffer.is_empty() {
        return;
    }
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = fs::write(path, buffer);
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

/// The shell side of worker output: `worker-event`s, the asset scope, `lastProject`, shell.log
/// and notices (C8.5, C9.4, C9.5, C9.8).
struct Shell {
    app: AppHandle,
    log_file: PathBuf,
    state_file: Option<PathBuf>,
    notices: Mutex<Notices>,
}

impl Shell {
    fn new(app: AppHandle, log_dir: &Path) -> Self {
        let state_file = app
            .path()
            .app_config_dir()
            .ok()
            .map(|folder| folder.join("state.json"));
        Self {
            app,
            log_file: log_dir.join("shell.log"),
            state_file,
            notices: Mutex::default(),
        }
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
        if let Some(folder) = self.log_file.parent() {
            let _ = fs::create_dir_all(folder);
        }
        if let Ok(mut log) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_file)
        {
            let _ = writeln!(log, "{} {message}", unix_millis());
        }
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

/// The notices not yet dismissed (C9.5).
#[derive(Default)]
struct Notices {
    last_id: u64,
    open: Vec<Notice>,
}

#[derive(Clone)]
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

/// Rewrites state.json with `project` as `lastProject`, atomically (C8.5).
fn write_last_project(file: &Path, project: &Path) -> io::Result<()> {
    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct AppState<'a> {
        last_project: &'a Path,
    }

    let text = serde_json::to_string(&AppState {
        last_project: project,
    })?;
    if let Some(folder) = file.parent() {
        fs::create_dir_all(folder)?;
    }
    let temp = file.with_extension("json.tmp");
    fs::write(&temp, text)?;
    fs::rename(&temp, file)
}

/// One native dialog at a time (F130). A dialog command that arrives while another is pending
/// is answered `{cancelled: true}`.
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
fn parented<R: Runtime>(builder: FileDialogBuilder<R>, window: &Window<R>) -> FileDialogBuilder<R> {
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

fn cancelled() -> Value {
    json!({"cancelled": true})
}

#[tauri::command]
fn dismiss_notice(shell: State<'_, Arc<Shell>>, id: u64) {
    lock(&shell.notices).dismiss(id);
}

#[tauri::command]
fn reserve_job_id(worker: State<'_, Worker>, prefix: String) -> Result<String, ShellError> {
    match prefix.as_str() {
        "preview" | "export" => Ok(worker.next_id(&prefix)),
        _ => Err(ShellError::new(
            ErrorCode::BadArgument,
            format!("Job ids are for previews and exports, not {prefix:?}."),
        )),
    }
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
async fn new_project(worker: State<'_, Worker>) -> Result<Value, ShellError> {
    worker.new_project().await
}

#[tauri::command]
async fn open_sample(
    worker: State<'_, Worker>,
    runtime: State<'_, ResolvedRuntime>,
) -> Result<Value, ShellError> {
    let sample = runtime
        .sample_json
        .clone()
        .ok_or_else(|| ShellError::new(ErrorCode::Io, "The sample project was not found."))?;
    worker.open_sample(sample).await
}

#[tauri::command]
async fn open_project_dialog(
    app: AppHandle,
    window: Window,
    worker: State<'_, Worker>,
    dialogs: State<'_, Dialogs>,
) -> Result<Value, ShellError> {
    let Some(_turn) = dialogs.begin() else {
        return Ok(cancelled());
    };
    let picked = parented(app.dialog().file(), &window)
        .set_title("Open Mathimator project")
        .blocking_pick_folder();
    let Some(folder) = picked else {
        return Ok(cancelled());
    };
    worker.open_project(&picked_path(folder)?).await
}

#[tauri::command]
async fn save_project_as_dialog(
    app: AppHandle,
    window: Window,
    worker: State<'_, Worker>,
    dialogs: State<'_, Dialogs>,
) -> Result<Value, ShellError> {
    let Some(_turn) = dialogs.begin() else {
        return Ok(cancelled());
    };
    let picked = parented(app.dialog().file(), &window)
        .set_title("Save project as")
        .blocking_pick_folder();
    let Some(folder) = picked else {
        return Ok(cancelled());
    };
    worker.save_project_as(&picked_path(folder)?).await
}

#[tauri::command]
async fn export_code_dialog(
    app: AppHandle,
    window: Window,
    worker: State<'_, Worker>,
    dialogs: State<'_, Dialogs>,
) -> Result<Value, ShellError> {
    let Some(_turn) = dialogs.begin() else {
        return Ok(cancelled());
    };
    let reply = worker.generated_code().await?;
    if reply.get("event").and_then(Value::as_str) != Some("code") {
        return Ok(reply);
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
        return Ok(cancelled());
    };
    let path = picked_path(file)?;
    fs::write(&path, source).map_err(|err| {
        ShellError::new(
            ErrorCode::Io,
            format!("Could not write {}: {err}.", path.display()),
        )
    })?;
    Ok(json!({"path": path.display().to_string()}))
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

#[tauri::command]
async fn open_project_path(worker: State<'_, Worker>, path: String) -> Result<Value, ShellError> {
    worker.open_project(Path::new(&path)).await
}

#[tauri::command]
fn cancel_equation_preview(glyph: State<'_, GlyphPreview>, generation: u64) {
    glyph.cancel(generation);
}

#[tauri::command]
fn compile_equation_preview(
    worker: State<'_, Worker>,
    runtime: State<'_, ResolvedRuntime>,
    glyph: State<'_, GlyphPreview>,
    latex: String,
    generation: u64,
) -> Result<Value, String> {
    let project = worker
        .project()
        .ok_or_else(|| "No project is open.".to_string())?;
    glyph.compile(
        &runtime.python,
        &runtime.worker_src,
        &project,
        &latex,
        generation,
    )
}

#[tauri::command]
fn note_shell_ready() -> Result<(), String> {
    let path = log_dir().join("shell-ready.txt");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    fs::write(&path, format!("ready {}\n", unix_millis())).map_err(|err| err.to_string())?;
    Ok(())
}

/// Registers the Tauri commands, and keeps their names for the `commands.json` check (C9.1).
macro_rules! commands {
    ($($command:ident),* $(,)?) => {
        #[cfg(test)]
        const COMMANDS: &[&str] = &[$(stringify!($command)),*];

        fn invoke_handler() -> impl Fn(tauri::ipc::Invoke) -> bool + Send + Sync + 'static {
            tauri::generate_handler![$($command),*]
        }
    };
}

commands![
    dismiss_notice,
    reserve_job_id,
    get_state,
    edit_document,
    undo_document,
    redo_document,
    clear_cache,
    reload_project,
    overwrite_project,
    new_project,
    open_sample,
    open_project_dialog,
    save_project_as_dialog,
    export_code_dialog,
    render_preview,
    export_movie,
    cancel_render,
    open_project_path,
    cancel_equation_preview,
    compile_equation_preview,
    note_shell_ready,
];

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let runtime = resolve_runtime(app.handle()).map_err(std::io::Error::other)?;
            let logs = log_dir();
            let stderr_log = logs.join("supervisor.err");
            let shell = Arc::new(Shell::new(app.handle().clone(), &logs));
            let spawn_runtime = runtime.clone();
            let spawn_log = stderr_log.clone();
            let worker = Worker::launch(
                shell.clone(),
                Box::new(move |project: &Path, init: bool| {
                    spawn_supervisor(&spawn_runtime, &spawn_log, project, init)
                }),
                std::env::temp_dir(),
                stderr_log,
            );
            app.manage(GlyphPreview::new(runtime.typst.clone()));
            app.manage(runtime);
            app.manage(worker);
            app.manage(shell);
            app.manage(Dialogs::default());
            Ok(())
        })
        .invoke_handler(invoke_handler())
        .run(tauri::generate_context!())
        .expect("error while running Mathimator");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_registered_commands_are_the_commands_json_list() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol/commands.json"
        ))
        .unwrap();
        let listed: Vec<&str> = fixture["tauriCommands"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|entry| cfg!(debug_assertions) || entry["debugOnly"] != true)
            .map(|entry| entry["name"].as_str().unwrap())
            .collect();
        let missing: Vec<&str> = listed
            .iter()
            .copied()
            .filter(|name| !COMMANDS.contains(name))
            .collect();
        let unlisted: Vec<&str> = COMMANDS
            .iter()
            .copied()
            .filter(|name| !listed.contains(name))
            .collect();
        // app_status and restart_worker come with AppStatus (contract C9.5), a later shell step.
        assert_eq!(missing, ["app_status", "restart_worker"]);
        assert!(unlisted.is_empty(), "not in commands.json: {unlisted:?}");
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
        let folder =
            std::env::temp_dir().join(format!("mathimator-state-test-{}", std::process::id()));
        let file = folder.join("config").join("state.json");
        write_last_project(&file, Path::new("C:\\Users\\me\\Lessons\\waves")).unwrap();
        write_last_project(&file, Path::new("C:\\Users\\me\\Lessons\\sine")).unwrap();
        let saved: Value = serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
        let _ = fs::remove_dir_all(&folder);
        assert_eq!(
            saved,
            json!({"lastProject": "C:\\Users\\me\\Lessons\\sine"})
        );
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
