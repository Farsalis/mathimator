use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;

mod equation;
mod win32_job;

use equation::GlyphPreview;

struct Shared {
    pending: Mutex<HashMap<String, Sender<Value>>>,
}

struct Worker {
    app: AppHandle,
    shared: Arc<Shared>,
    python: PathBuf,
    worker_src: PathBuf,
    tinytex_bin: Option<PathBuf>,
    sample_json: Option<PathBuf>,
    stderr_log: PathBuf,
    project: Mutex<PathBuf>,
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    supervisor_job: Mutex<Option<win32_job::SupervisorJob>>,
    lifecycle: Mutex<()>,
    next_id: AtomicU64,
}

fn repo_root() -> Option<PathBuf> {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .ok()
}

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

fn launch_project() -> PathBuf {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0);
    std::env::temp_dir().join(format!("mathimator-{}-{millis}", std::process::id()))
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

fn spawn_supervisor(
    app: &AppHandle,
    shared: &Arc<Shared>,
    python: &Path,
    worker_src: &Path,
    project: &Path,
    stderr_log: &Path,
    tinytex_bin: Option<&Path>,
) -> Result<(Child, ChildStdin, Option<win32_job::SupervisorJob>), String> {
    fs::create_dir_all(project).map_err(|err| err.to_string())?;
    let mut command = Command::new(python);
    command
        .arg("-m")
        .arg("mathimator_worker")
        .arg("--project")
        .arg(project)
        .arg("--python")
        .arg(python)
        .arg("--init")
        .env("PYTHONPATH", worker_src)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console(&mut command);
    apply_tinytex(&mut command, tinytex_bin);
    if let Some(root) = repo_root() {
        command.current_dir(root);
    }
    let mut child = command
        .spawn()
        .map_err(|err| format!("failed to start supervisor: {err}"))?;
    let supervisor_job = win32_job::assign_supervisor(&child);
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "supervisor stdin was not piped".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "supervisor stdout was not piped".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "supervisor stderr was not piped".to_string())?;
    let app_handle = app.clone();
    let shared_reader = Arc::clone(shared);
    std::thread::spawn(move || read_stdout(app_handle, shared_reader, stdout));
    let stderr_log = stderr_log.to_path_buf();
    std::thread::spawn(move || drain_stderr(stderr_log, stderr));
    Ok((child, stdin, supervisor_job))
}

fn read_stdout(app: AppHandle, shared: Arc<Shared>, stdout: std::process::ChildStdout) {
    let reader = BufReader::new(stdout);
    for line in reader.lines() {
        let line = match line {
            Ok(line) => line,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<Value>(&line) {
            Ok(value) => {
                if let Some(id) = value.get("jobId").and_then(Value::as_str) {
                    let sender = shared
                        .pending
                        .lock()
                        .ok()
                        .and_then(|mut pending| pending.remove(id));
                    if let Some(sender) = sender {
                        let _ = sender.send(value.clone());
                    }
                }
                let _ = app.emit("worker-event", value);
            }
            Err(err) => {
                let _ = app.emit(
                    "worker-event",
                    json!({
                        "event": "error",
                        "jobId": null,
                        "message": format!("supervisor sent non-json: {err}"),
                    }),
                );
            }
        }
    }
    let _ = app.emit(
        "worker-event",
        json!({
            "event": "error",
            "jobId": null,
            "message": "supervisor exited",
        }),
    );
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

impl Worker {
    fn start(app: AppHandle) -> Result<Self, String> {
        let runtime = resolve_runtime(&app)?;
        let project = launch_project();
        allow_project(&app, &project)?;
        let log_root = repo_root().unwrap_or_else(std::env::temp_dir);
        let stderr_log = log_root.join("out/logs/supervisor.err");
        let shared = Arc::new(Shared {
            pending: Mutex::new(HashMap::new()),
        });
        let (child, stdin, supervisor_job) = spawn_supervisor(
            &app,
            &shared,
            &runtime.python,
            &runtime.worker_src,
            &project,
            &stderr_log,
            runtime.tinytex_bin.as_deref(),
        )?;
        Ok(Self {
            app,
            shared,
            python: runtime.python,
            worker_src: runtime.worker_src,
            tinytex_bin: runtime.tinytex_bin,
            sample_json: runtime.sample_json,
            stderr_log,
            project: Mutex::new(project),
            stdin: Mutex::new(Some(stdin)),
            child: Mutex::new(Some(child)),
            supervisor_job: Mutex::new(supervisor_job),
            lifecycle: Mutex::new(()),
            next_id: AtomicU64::new(1),
        })
    }

    fn ensure_running(&self) -> Result<(), String> {
        let _lifecycle = self.lifecycle.lock().map_err(|err| err.to_string())?;
        let alive = {
            let mut slot = self.child.lock().map_err(|err| err.to_string())?;
            match slot.as_mut() {
                Some(child) => matches!(child.try_wait(), Ok(None)),
                None => false,
            }
        };
        if alive {
            return Ok(());
        }
        let project = self.project.lock().map_err(|err| err.to_string())?.clone();
        let (child, stdin, supervisor_job) = spawn_supervisor(
            &self.app,
            &self.shared,
            &self.python,
            &self.worker_src,
            &project,
            &self.stderr_log,
            self.tinytex_bin.as_deref(),
        )?;
        let mut job_slot = self.supervisor_job.lock().map_err(|err| err.to_string())?;
        let mut child_slot = self.child.lock().map_err(|err| err.to_string())?;
        if let Some(mut old) = child_slot.take() {
            let old_job = job_slot.take();
            let assigned = old_job.is_some();
            drop(old_job);
            if !assigned {
                let _ = old.kill();
            }
            let _ = old.wait();
        }
        *child_slot = Some(child);
        *job_slot = supervisor_job;
        drop(child_slot);
        let mut stdin_slot = self.stdin.lock().map_err(|err| err.to_string())?;
        *stdin_slot = Some(stdin);
        Ok(())
    }

    fn send(&self, value: &Value) -> Result<(), String> {
        self.ensure_running()?;
        let mut slot = self.stdin.lock().map_err(|err| err.to_string())?;
        let stdin = slot
            .as_mut()
            .ok_or_else(|| "supervisor is not running".to_string())?;
        let mut line = serde_json::to_string(value).map_err(|err| err.to_string())?;
        line.push('\n');
        stdin
            .write_all(line.as_bytes())
            .map_err(|err| err.to_string())?;
        stdin.flush().map_err(|err| err.to_string())?;
        Ok(())
    }

    fn next_job_id(&self, prefix: &str) -> String {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        format!("{prefix}-{id}")
    }

    fn project_path(&self) -> Result<PathBuf, String> {
        self.project
            .lock()
            .map(|slot| slot.clone())
            .map_err(|err| err.to_string())
    }

    fn python_path(&self) -> PathBuf {
        self.python.clone()
    }

    fn worker_src_path(&self) -> PathBuf {
        self.worker_src.clone()
    }

    fn sample_json(&self) -> Result<PathBuf, String> {
        self.sample_json
            .clone()
            .ok_or_else(|| "the reference-60 sample was not found".to_string())
    }

    fn call(&self, mut value: Value) -> Result<Value, String> {
        let id = self.next_job_id("rpc");
        value["id"] = json!(id);
        let (sender, receiver): (Sender<Value>, Receiver<Value>) = mpsc::channel();
        self.shared
            .pending
            .lock()
            .map_err(|err| err.to_string())?
            .insert(id.clone(), sender);
        if let Err(err) = self.send(&value) {
            let _ = self
                .shared
                .pending
                .lock()
                .map(|mut pending| pending.remove(&id));
            return Err(err);
        }
        match receiver.recv_timeout(Duration::from_secs(30)) {
            Ok(message) => Ok(message),
            Err(_) => {
                let _ = self
                    .shared
                    .pending
                    .lock()
                    .map(|mut pending| pending.remove(&id));
                Err("supervisor did not answer".to_string())
            }
        }
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        let job = self
            .supervisor_job
            .lock()
            .ok()
            .and_then(|mut slot| slot.take());
        let assigned = job.is_some();
        drop(job);
        if let Ok(mut slot) = self.child.lock() {
            if let Some(mut child) = slot.take() {
                if !assigned {
                    let _ = child.kill();
                }
                let _ = child.wait();
            }
        }
    }
}

fn open_project_at(app: &AppHandle, worker: &Worker, path: PathBuf) -> Result<Value, String> {
    let value = worker.call(json!({"cmd": "open_project", "path": path}))?;
    if value.get("event").and_then(Value::as_str) == Some("state") {
        allow_project(app, &path)?;
        let mut slot = worker.project.lock().map_err(|err| err.to_string())?;
        *slot = path;
    }
    Ok(value)
}

#[tauri::command]
fn worker_status(worker: State<'_, Worker>) -> Result<(), String> {
    let id = worker.next_job_id("status");
    worker.send(&json!({"cmd": "status", "id": id}))
}

#[tauri::command]
fn get_state(worker: State<'_, Worker>) -> Result<Value, String> {
    worker.call(json!({"cmd": "get_state"}))
}

#[tauri::command]
fn edit_document(worker: State<'_, Worker>, edit: Value) -> Result<Value, String> {
    worker.call(json!({"cmd": "edit", "edit": edit}))
}

#[tauri::command]
fn undo_document(worker: State<'_, Worker>) -> Result<Value, String> {
    worker.call(json!({"cmd": "undo"}))
}

#[tauri::command]
fn redo_document(worker: State<'_, Worker>) -> Result<Value, String> {
    worker.call(json!({"cmd": "redo"}))
}

#[tauri::command]
fn reserve_job_id(worker: State<'_, Worker>, prefix: String) -> String {
    worker.next_job_id(&prefix)
}

#[tauri::command]
fn render_preview(worker: State<'_, Worker>, job_id: String) -> Result<(), String> {
    worker.send(&json!({"cmd": "render_preview", "id": job_id}))
}

#[tauri::command]
fn export_movie(worker: State<'_, Worker>, job_id: String) -> Result<(), String> {
    worker.send(&json!({"cmd": "export", "id": job_id}))
}

#[tauri::command]
fn clear_cache(worker: State<'_, Worker>) -> Result<Value, String> {
    worker.call(json!({"cmd": "clear_cache"}))
}

#[tauri::command]
fn cancel_render(worker: State<'_, Worker>, job_id: String) -> Result<(), String> {
    let id = worker.next_job_id("cancel");
    worker.send(&json!({"cmd": "cancel", "id": id, "jobId": job_id}))
}

#[tauri::command]
fn open_sample(app: AppHandle, worker: State<'_, Worker>) -> Result<Value, String> {
    let sample = worker.sample_json()?;
    let dest = launch_project();
    fs::create_dir_all(&dest).map_err(|err| err.to_string())?;
    fs::copy(&sample, dest.join("project.json")).map_err(|err| err.to_string())?;
    open_project_at(&app, &worker, dest)
}

#[tauri::command]
fn view_code(worker: State<'_, Worker>) -> Result<Value, String> {
    worker.call(json!({"cmd": "code"}))
}

#[tauri::command]
fn open_project_path(
    app: AppHandle,
    worker: State<'_, Worker>,
    path: String,
) -> Result<Value, String> {
    open_project_at(&app, &worker, PathBuf::from(path))
}

#[tauri::command]
async fn open_project_dialog(app: AppHandle, worker: State<'_, Worker>) -> Result<Value, String> {
    let picked = app
        .dialog()
        .file()
        .set_title("Open Mathimator project")
        .blocking_pick_folder();
    let Some(folder) = picked else {
        return Ok(json!({"cancelled": true}));
    };
    let path = folder.into_path().map_err(|err| err.to_string())?;
    open_project_at(&app, &worker, path)
}

#[tauri::command]
async fn export_code_dialog(app: AppHandle, worker: State<'_, Worker>) -> Result<Value, String> {
    let code = worker.call(json!({"cmd": "code"}))?;
    if code.get("event").and_then(Value::as_str) != Some("code") {
        return Ok(code);
    }
    let source = code
        .get("source")
        .and_then(Value::as_str)
        .ok_or_else(|| "generated code was empty".to_string())?
        .to_string();
    let picked = app
        .dialog()
        .file()
        .set_title("Export code")
        .set_file_name("scene.py")
        .add_filter("Python", &["py"])
        .blocking_save_file();
    let Some(file) = picked else {
        return Ok(json!({"cancelled": true}));
    };
    let path = file.into_path().map_err(|err| err.to_string())?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    fs::write(&path, source).map_err(|err| err.to_string())?;
    Ok(json!({"path": path}))
}

#[tauri::command]
fn cancel_equation_preview(glyph: State<'_, GlyphPreview>, generation: u64) {
    glyph.cancel(generation);
}

#[tauri::command]
fn compile_equation_preview(
    worker: State<'_, Worker>,
    glyph: State<'_, GlyphPreview>,
    latex: String,
    generation: u64,
) -> Result<Value, String> {
    glyph.compile(
        &worker.python_path(),
        &worker.worker_src_path(),
        &worker.project_path()?,
        &latex,
        generation,
    )
}

#[tauri::command]
fn note_shell_ready() -> Result<(), String> {
    let path = repo_root()
        .unwrap_or_else(std::env::temp_dir)
        .join("out/logs/shell-ready.txt");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|err| err.to_string())?
        .as_millis();
    fs::write(&path, format!("ready {millis}\n")).map_err(|err| err.to_string())?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let runtime = resolve_runtime(app.handle()).map_err(std::io::Error::other)?;
            let worker = Worker::start(app.handle().clone()).map_err(std::io::Error::other)?;
            app.manage(worker);
            app.manage(GlyphPreview::new(runtime.typst));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            worker_status,
            get_state,
            edit_document,
            undo_document,
            redo_document,
            reserve_job_id,
            render_preview,
            export_movie,
            clear_cache,
            cancel_render,
            open_sample,
            view_code,
            open_project_path,
            open_project_dialog,
            export_code_dialog,
            cancel_equation_preview,
            compile_equation_preview,
            note_shell_ready
        ])
        .run(tauri::generate_context!())
        .expect("error while running Mathimator");
}
