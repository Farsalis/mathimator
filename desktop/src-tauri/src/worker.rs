//! The worker as the shell runs it: start and handshake (contract C6), the request gate and the
//! per-generation pending replies (C9.1, C9.2), routing worker stdout (C9.4), and exit and
//! respawn (C8.6). Every worker-bound command of C9.1 has a method on [`Worker`].

use std::collections::HashMap;
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::mem;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, SendError, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::async_runtime;

use crate::protocol::{ErrorCode, Request, ShellError, PROTOCOL_VERSION};
use crate::{lock, unix_millis};

const RPC_TIMEOUT: Duration = Duration::from_secs(30);
const CLEAR_CACHE_TIMEOUT: Duration = Duration::from_secs(120);
const SAVE_AS_TIMEOUT: Duration = Duration::from_secs(600);
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(30);
/// How long the router waits for the exit code once a worker's stdout has closed (C8.6).
const EXIT_CODE_WAIT: Duration = Duration::from_secs(1);
/// How long the shell waits for a worker it ended to exit (C8.6).
const STOP_WAIT: Duration = Duration::from_secs(5);

/// What the shell does with what the worker reports (C9.4, C9.5).
pub trait Host: Send + Sync + 'static {
    /// Sends one `worker-event` to the UI.
    fn emit(&self, event: Value);
    /// Grants the asset scope for `project` and records it as `lastProject`. Called whenever a
    /// `state` line reports a folder other than the last one seen.
    fn bind(&self, project: &Path);
    /// Appends one line to shell.log.
    fn log(&self, message: &str);
    /// Adds a notice for the UI.
    fn notice(&self, message: String);
}

/// A worker process just started: its pipes and the process itself.
pub struct Spawned {
    pub stdin: Box<dyn Write + Send>,
    pub stdout: Box<dyn Read + Send>,
    pub process: Box<dyn WorkerProcess>,
}

/// The process behind a worker.
pub trait WorkerProcess: Send {
    /// Waits up to `limit` for the process to exit, and returns its exit code if it did.
    fn exit_code(&mut self, limit: Duration) -> Option<i32>;
    /// Ends the process for good, and returns whether it exited within `limit`.
    fn end(&mut self, limit: Duration) -> bool;
}

/// Starts a worker on a project folder, with `--init` when the flag is set.
pub type Spawner = Box<dyn Fn(&Path, bool) -> io::Result<Spawned> + Send + Sync>;

/// The worker as the Tauri commands see it.
pub struct Worker {
    shared: Arc<Shared>,
    /// The request gate (C9.2). Its holder owns the worker: only it writes requests and starts or
    /// ends the process. The lock is fair, so requests are written in call order.
    status: Arc<async_runtime::Mutex<Status>>,
}

enum Status {
    /// No worker has been started yet.
    Stopped,
    Running(Link),
    /// Commands fail with `worker_unavailable` and this reason.
    Unavailable(String),
}

/// One running worker, one generation.
struct Link {
    generation: u64,
    stdin: Box<dyn Write + Send>,
    process: Arc<Process>,
}

/// The process of one generation, shared with its stdout router.
struct Process {
    child: Mutex<Box<dyn WorkerProcess>>,
    /// Set when the shell ends the process, so that its exit is not reported as a crash.
    stopping: AtomicBool,
}

#[derive(Clone, Copy)]
enum Start {
    /// The first worker, on a new temp project (C8.5).
    Launch,
    /// After an exit, on the bound folder (C8.6).
    Respawn,
}

struct Shared {
    host: Arc<dyn Host>,
    spawn: Spawner,
    /// Where temp projects are created.
    temp_root: PathBuf,
    /// supervisor.err, which the "stopped during startup" reason points to.
    stderr_log: PathBuf,
    /// The one counter behind every request and job id (C2).
    next_id: AtomicU64,
    next_generation: AtomicU64,
    routes: Mutex<Routes>,
    bound: Mutex<Bound>,
}

/// Replies the router owes to callers.
#[derive(Default)]
struct Routes {
    /// The generation whose stdout is still open.
    open: Option<u64>,
    /// Pending RPCs by request id (C9.4).
    pending: HashMap<String, Pending>,
}

struct Pending {
    generation: u64,
    reply: Sender<Reply>,
}

/// What a pending RPC receives: its reply line, or why there is none.
type Reply = Result<Value, Failure>;

#[derive(Debug)]
enum Failure {
    /// The worker's stdout closed first; its exit code, when known.
    Exited(Option<i32>),
    Timeout,
    Write(io::Error),
}

impl From<Failure> for ShellError {
    fn from(failure: Failure) -> Self {
        match failure {
            Failure::Exited(code) => ShellError::new(
                ErrorCode::WorkerExited,
                format!("The worker stopped{}.", exit_code_text(code)),
            ),
            Failure::Timeout => {
                ShellError::new(ErrorCode::Timeout, "The worker did not answer in time.")
            }
            Failure::Write(err) => ShellError::new(
                ErrorCode::Io,
                format!("Could not write to the worker: {err}."),
            ),
        }
    }
}

/// The project folder as the shell knows it (C9.4).
#[derive(Default)]
struct Bound {
    /// The folder the worker was started on, then the `projectPath` of every `state` line.
    folder: Option<PathBuf>,
    /// The last `projectPath` a `state` line carried.
    seen: Option<PathBuf>,
}

/// The holder of the request gate.
struct Session<'a> {
    status: &'a mut Status,
    shared: &'a Arc<Shared>,
}

impl Worker {
    /// Starts the first worker in the background, on a new temp project in `temp_root`. Every
    /// worker-bound command waits until its handshake is done (C6).
    pub fn launch(
        host: Arc<dyn Host>,
        spawn: Spawner,
        temp_root: PathBuf,
        stderr_log: PathBuf,
    ) -> Self {
        let shared = Arc::new(Shared {
            host,
            spawn,
            temp_root,
            stderr_log,
            next_id: AtomicU64::new(1),
            next_generation: AtomicU64::new(1),
            routes: Mutex::default(),
            bound: Mutex::default(),
        });
        let status = Arc::new(async_runtime::Mutex::new(Status::Stopped));
        let mut gate = Arc::clone(&status)
            .try_lock_owned()
            .expect("a new request gate is free");
        let launching = Arc::clone(&shared);
        async_runtime::spawn_blocking(move || {
            // A failed launch leaves the worker unavailable, and every command reports why.
            let _ = Session {
                status: &mut gate,
                shared: &launching,
            }
            .ready();
        });
        Self { shared, status }
    }

    /// The next id from the one id counter, for example `preview-7`.
    pub fn next_id(&self, prefix: &str) -> String {
        self.shared.next_id(prefix)
    }

    /// The bound project folder (C9.4), once a worker has been started.
    pub fn project(&self) -> Option<PathBuf> {
        self.shared.project()
    }

    pub async fn get_state(&self) -> Result<Value, ShellError> {
        self.rpc(Request::GetState, RPC_TIMEOUT).await
    }

    pub async fn edit_document(&self, edit: Value) -> Result<Value, ShellError> {
        self.rpc(Request::Edit { edit }, RPC_TIMEOUT).await
    }

    pub async fn undo_document(&self) -> Result<Value, ShellError> {
        self.rpc(Request::Undo, RPC_TIMEOUT).await
    }

    pub async fn redo_document(&self) -> Result<Value, ShellError> {
        self.rpc(Request::Redo, RPC_TIMEOUT).await
    }

    pub async fn clear_cache(&self) -> Result<Value, ShellError> {
        self.rpc(Request::ClearCache, CLEAR_CACHE_TIMEOUT).await
    }

    pub async fn reload_project(&self) -> Result<Value, ShellError> {
        self.rpc(Request::Reload, RPC_TIMEOUT).await
    }

    pub async fn overwrite_project(&self) -> Result<Value, ShellError> {
        self.rpc(Request::Overwrite, RPC_TIMEOUT).await
    }

    /// The generated scene source, for Export code.
    pub async fn generated_code(&self) -> Result<Value, ShellError> {
        self.rpc(Request::Code, RPC_TIMEOUT).await
    }

    pub async fn open_project(&self, folder: &Path) -> Result<Value, ShellError> {
        let path = path_text(folder)?;
        self.rpc(Request::OpenProject { path, init: false }, RPC_TIMEOUT)
            .await
    }

    pub async fn new_project(&self) -> Result<Value, ShellError> {
        self.run(|session| session.open_new_project()).await
    }

    pub async fn open_sample(&self, sample: PathBuf) -> Result<Value, ShellError> {
        self.run(move |session| session.open_sample(&sample)).await
    }

    pub async fn save_project_as(&self, folder: &Path) -> Result<Value, ShellError> {
        let path = path_text(folder)?;
        self.run(move |session| session.save_as(path)).await
    }

    pub async fn render_preview(&self, job_id: String) -> Result<(), ShellError> {
        self.run(move |session| session.send(&Request::RenderPreview, &job_id))
            .await
    }

    pub async fn export_movie(&self, job_id: String, force: bool) -> Result<(), ShellError> {
        self.run(move |session| session.send(&Request::Export { force }, &job_id))
            .await
    }

    pub async fn cancel_render(&self, job_id: String) -> Result<(), ShellError> {
        self.run(move |session| {
            let id = session.shared.next_id("cancel");
            session.send(&Request::Cancel { job_id }, &id)
        })
        .await
    }

    async fn rpc(&self, request: Request, timeout: Duration) -> Result<Value, ShellError> {
        self.run(move |session| session.rpc(&request, timeout))
            .await
    }

    /// Runs `work` holding the request gate, once the worker is ready. The work blocks on pipes
    /// and replies, so it runs on the blocking pool, never on the UI thread (D20).
    async fn run<T, F>(&self, work: F) -> Result<T, ShellError>
    where
        T: Send + 'static,
        F: FnOnce(&mut Session<'_>) -> Result<T, ShellError> + Send + 'static,
    {
        let mut gate = Arc::clone(&self.status).lock_owned().await;
        let shared = Arc::clone(&self.shared);
        async_runtime::spawn_blocking(move || {
            let mut session = Session {
                status: &mut gate,
                shared: &shared,
            };
            session.ready()?;
            work(&mut session)
        })
        .await
        .map_err(|err| {
            ShellError::new(ErrorCode::Io, format!("The worker request failed: {err}"))
        })?
    }
}

impl Session<'_> {
    /// Makes sure a worker is running, and starts one when none is or the last one exited.
    fn ready(&mut self) -> Result<(), ShellError> {
        match &*self.status {
            Status::Running(link) if self.shared.is_open(link.generation) => return Ok(()),
            Status::Unavailable(reason) => return Err(unavailable(reason)),
            Status::Running(_) | Status::Stopped => {}
        }
        self.start().map_err(|reason| {
            self.shared.host.log(&reason);
            let error = unavailable(&reason);
            *self.status = Status::Unavailable(reason);
            error
        })
    }

    /// Ends the old worker, then starts one on the bound folder, or at launch on a new temp
    /// project, and runs the handshake (C6). Returns the reason the worker is unavailable.
    fn start(&mut self) -> Result<(), String> {
        self.stop()?;
        let (folder, start) = match self.shared.project() {
            Some(folder) => (folder, Start::Respawn),
            None => (
                self.shared
                    .create_temp_project()
                    .map_err(|err| err.message)?,
                Start::Launch,
            ),
        };
        let init = matches!(start, Start::Launch);
        let spawned = (self.shared.spawn)(&folder, init)
            .map_err(|err| format!("Could not start the worker: {err}."))?;
        self.shared
            .host
            .log(&format!("Started a worker on {}.", folder.display()));
        self.shared.set_project(&folder);
        *self.status = Status::Running(self.shared.connect(spawned));
        let started = self
            .handshake()
            .and_then(|startup_error| match startup_error {
                Some(error) => self.recover(&folder, &error, start),
                None => Ok(()),
            });
        if started.is_err() {
            let _ = self.stop();
        }
        started
    }

    /// Ends the running worker for good and waits for it to exit (C8.6 step 4).
    fn stop(&mut self) -> Result<(), String> {
        let Status::Running(link) = mem::replace(self.status, Status::Stopped) else {
            return Ok(());
        };
        link.process.stopping.store(true, Ordering::SeqCst);
        drop(link.stdin);
        if lock(&link.process.child).end(STOP_WAIT) {
            Ok(())
        } else {
            Err("The previous worker did not stop. Restart Mathimator.".to_owned())
        }
    }

    /// Sends `status` and checks the reply (C6). Returns the worker's `startupError`.
    fn handshake(&mut self) -> Result<Option<Value>, String> {
        let id = self.shared.next_id("status");
        match self.request(&Request::Status, &id, HANDSHAKE_TIMEOUT) {
            Ok(reply) => check_ready(&reply),
            Err(Failure::Timeout) => Err("The worker did not start within 30 s.".to_owned()),
            Err(Failure::Exited(code)) => Err(self.shared.stopped_during_startup(code)),
            Err(Failure::Write(_)) => {
                let code = self.exit_code();
                Err(self.shared.stopped_during_startup(code))
            }
        }
    }

    /// Opens a new temp project when the worker could not bind its folder (C8.5 launch step 3,
    /// C8.6 step 5), and tells the user why.
    fn recover(
        &mut self,
        folder: &Path,
        startup_error: &Value,
        start: Start,
    ) -> Result<(), String> {
        let reply = self.open_new_project().map_err(|err| err.message)?;
        if event_of(&reply) != Some("state") {
            return Err(message_of(&reply));
        }
        let reason = message_of(startup_error);
        let reason = reason.trim_end_matches('.');
        let folder = folder.display();
        self.shared.host.notice(match start {
            Start::Launch => format!("Could not reopen {folder}: {reason}. A new project was started."),
            Start::Respawn => format!(
                "Could not reopen {folder} after the worker restarted: {reason}. A new project was started."
            ),
        });
        Ok(())
    }

    fn exit_code(&self) -> Option<i32> {
        match &*self.status {
            Status::Running(link) => lock(&link.process.child).exit_code(EXIT_CODE_WAIT),
            _ => None,
        }
    }

    fn rpc(&mut self, request: &Request, timeout: Duration) -> Result<Value, ShellError> {
        let id = self.shared.next_id("rpc");
        self.request(request, &id, timeout)
            .map_err(ShellError::from)
    }

    fn send(&mut self, request: &Request, id: &str) -> Result<(), ShellError> {
        self.write(request, id).map_err(ShellError::from)
    }

    /// Writes one RPC and waits for its reply: the router hands it over, or fails it at EOF.
    fn request(&mut self, request: &Request, id: &str, timeout: Duration) -> Reply {
        let Status::Running(link) = &*self.status else {
            return Err(Failure::Exited(None));
        };
        let (reply, replies) = mpsc::channel();
        self.shared.expect(id, link.generation, reply)?;
        if let Err(failure) = self.write(request, id) {
            self.shared.forget(id);
            return Err(failure);
        }
        self.shared.await_reply(id, &replies, timeout)
    }

    fn write(&mut self, request: &Request, id: &str) -> Result<(), Failure> {
        let Status::Running(link) = &mut *self.status else {
            return Err(Failure::Exited(None));
        };
        let line = request.line(id).map_err(|err| Failure::Write(err.into()))?;
        link.stdin
            .write_all(line.as_bytes())
            .and_then(|()| link.stdin.flush())
            .map_err(Failure::Write)
    }

    /// New (C8.5): opens a new temp project.
    fn open_new_project(&mut self) -> Result<Value, ShellError> {
        let folder = self.shared.create_temp_project()?;
        self.open_temp_project(&folder, true)
    }

    /// Sample (C8.5): opens a temp copy of the bundled sample.
    fn open_sample(&mut self, sample: &Path) -> Result<Value, ShellError> {
        let folder = self.shared.create_temp_project()?;
        if let Err(err) = fs::copy(sample, folder.join("project.json")) {
            self.shared.remove_folder(&folder);
            return Err(ShellError::new(
                ErrorCode::Io,
                format!("Could not copy the sample project: {err}."),
            ));
        }
        self.open_temp_project(&folder, false)
    }

    /// Opens a temp folder this session created. A refusal deletes the folder. A shell failure
    /// keeps it, because a late reply may still bind it (C8.5).
    fn open_temp_project(&mut self, folder: &Path, init: bool) -> Result<Value, ShellError> {
        let path = path_text(folder)?;
        let reply = self.rpc(&Request::OpenProject { path, init }, RPC_TIMEOUT)?;
        if event_of(&reply) == Some("rejected") {
            self.shared.remove_folder(folder);
        }
        Ok(reply)
    }

    /// Save As (C8.5). Once the worker has moved to the target, the old folder of a temp project
    /// is deleted, which makes Save As a move.
    fn save_as(&mut self, path: String) -> Result<Value, ShellError> {
        let old = self.shared.project();
        let reply = self.rpc(&Request::SaveAs { path }, SAVE_AS_TIMEOUT)?;
        let target = match event_of(&reply) {
            Some("state") => reply.get("projectPath").and_then(Value::as_str),
            _ => None,
        };
        if let (Some(old), Some(target)) = (old, target) {
            if self.shared.is_left_temp_project(&old, Path::new(target)) {
                self.shared.remove_folder(&old);
            }
        }
        Ok(reply)
    }
}

impl Shared {
    fn next_id(&self, prefix: &str) -> String {
        format!("{prefix}-{}", self.next_id.fetch_add(1, Ordering::Relaxed))
    }

    fn project(&self) -> Option<PathBuf> {
        lock(&self.bound).folder.clone()
    }

    fn set_project(&self, folder: &Path) {
        lock(&self.bound).folder = Some(folder.to_owned());
    }

    fn is_open(&self, generation: u64) -> bool {
        lock(&self.routes).open == Some(generation)
    }

    /// Starts the stdout router of a worker just spawned, as the next generation.
    fn connect(self: &Arc<Self>, spawned: Spawned) -> Link {
        let generation = self.next_generation.fetch_add(1, Ordering::Relaxed);
        lock(&self.routes).open = Some(generation);
        let process = Arc::new(Process {
            child: Mutex::new(spawned.process),
            stopping: AtomicBool::new(false),
        });
        let shared = Arc::clone(self);
        let routed = Arc::clone(&process);
        let stdout = spawned.stdout;
        thread::spawn(move || shared.route(generation, &routed, stdout));
        Link {
            generation,
            stdin: spawned.stdin,
            process,
        }
    }

    /// Registers a pending RPC of `generation`, unless that worker's stdout has already closed.
    fn expect(&self, id: &str, generation: u64, reply: Sender<Reply>) -> Result<(), Failure> {
        let mut routes = lock(&self.routes);
        if routes.open != Some(generation) {
            return Err(Failure::Exited(None));
        }
        routes
            .pending
            .insert(id.to_owned(), Pending { generation, reply });
        Ok(())
    }

    /// Drops a pending RPC, and returns whether it was still pending.
    fn forget(&self, id: &str) -> bool {
        lock(&self.routes).pending.remove(id).is_some()
    }

    fn await_reply(&self, id: &str, replies: &Receiver<Reply>, timeout: Duration) -> Reply {
        match replies.recv_timeout(timeout) {
            Ok(reply) => reply,
            // The entry goes, so a late reply is broadcast (C9.4).
            Err(RecvTimeoutError::Timeout) if self.forget(id) => Err(Failure::Timeout),
            // The router took the entry as the wait ended, so its reply is on the way.
            Err(_) => replies.recv().unwrap_or(Err(Failure::Exited(None))),
        }
    }

    /// Routes one worker's stdout until EOF, then handles its exit (C9.4, C8.6 steps 1 to 4).
    fn route(&self, generation: u64, process: &Process, stdout: impl Read) {
        for line in BufReader::new(stdout).split(b'\n') {
            let Ok(line) = line else { break };
            self.dispatch(&line);
        }
        let crashed = !process.stopping.load(Ordering::SeqCst);
        let exit_code = if crashed {
            lock(&process.child).exit_code(EXIT_CODE_WAIT)
        } else {
            None
        };
        self.close(generation, exit_code, crashed);
        if crashed {
            // Ending it for good also stops a Manim grandchild that outlived it. The next command
            // checks again that it has exited before it starts another worker.
            lock(&process.child).end(STOP_WAIT);
        }
    }

    /// Hands one stdout line to the RPC waiting for it, or broadcasts it.
    fn dispatch(&self, line: &[u8]) {
        if line.trim_ascii().is_empty() {
            return;
        }
        let value: Value = match serde_json::from_slice(line) {
            Ok(value) => value,
            Err(err) => {
                let text = String::from_utf8_lossy(line);
                let clipped: String = text.chars().take(200).collect();
                self.host.log(&format!(
                    "The worker wrote a line that is not JSON ({err}): {clipped}"
                ));
                return;
            }
        };
        if event_of(&value) == Some("state") {
            if let Some(project) = value.get("projectPath").and_then(Value::as_str) {
                self.report(Path::new(project));
            }
        }
        let claimed = value
            .get("jobId")
            .and_then(Value::as_str)
            .and_then(|id| lock(&self.routes).pending.remove(id));
        match claimed {
            Some(pending) => {
                if let Err(SendError(Ok(value))) = pending.reply.send(Ok(value)) {
                    self.host.emit(value);
                }
            }
            None => self.host.emit(value),
        }
    }

    /// Takes the `projectPath` of a `state` line, claimed or broadcast, as the bound project.
    fn report(&self, project: &Path) {
        {
            let mut bound = lock(&self.bound);
            bound.folder = Some(project.to_owned());
            if bound.seen.as_deref() == Some(project) {
                return;
            }
            bound.seen = Some(project.to_owned());
        }
        self.host.bind(project);
    }

    /// Fails the pending RPCs of a generation whose stdout closed, and reports a crash (C8.6).
    fn close(&self, generation: u64, exit_code: Option<i32>, crashed: bool) {
        let stranded: Vec<Pending> = {
            let mut routes = lock(&self.routes);
            if routes.open == Some(generation) {
                routes.open = None;
            }
            routes
                .pending
                .extract_if(|_, pending| pending.generation == generation)
                .map(|(_, pending)| pending)
                .collect()
        };
        for pending in stranded {
            let _ = pending.reply.send(Err(Failure::Exited(exit_code)));
        }
        if crashed {
            let message = format!("The worker stopped{}.", exit_code_text(exit_code));
            self.host.log(&message);
            self.host.emit(json!({
                "event": "worker_exited",
                "jobId": null,
                "message": message,
                "exitCode": exit_code,
            }));
        }
    }

    /// Creates `<temp>/mathimator-<pid>-<ms>` for a new temp project (C8.5).
    fn create_temp_project(&self) -> Result<PathBuf, ShellError> {
        let mut millis = unix_millis();
        loop {
            let folder = self
                .temp_root
                .join(format!("mathimator-{}-{millis}", std::process::id()));
            match fs::create_dir(&folder) {
                Ok(()) => return Ok(folder),
                Err(err) if err.kind() == io::ErrorKind::AlreadyExists => millis += 1,
                Err(err) => {
                    return Err(ShellError::new(
                        ErrorCode::Io,
                        format!(
                            "Could not create a project folder in {}: {err}.",
                            self.temp_root.display()
                        ),
                    ))
                }
            }
        }
    }

    /// Whether `old`, the folder Save As just moved away from, is a temp project the shell may
    /// delete (C8.5): a marked `<temp>/mathimator-<digits>-<digits>` folder, as pruning requires,
    /// that does not hold `target`. The paths are compared in canonical form, so an 8.3 %TEMP%
    /// still matches the long form the worker reports.
    fn is_left_temp_project(&self, old: &Path, target: &Path) -> bool {
        let (Ok(old), Ok(target), Ok(temp_root)) = (
            fs::canonicalize(old),
            fs::canonicalize(target),
            fs::canonicalize(&self.temp_root),
        ) else {
            return false;
        };
        old.parent() == Some(temp_root.as_path())
            && has_temp_name(&old)
            && !target.starts_with(&old)
            && is_temp_project(&old)
    }

    /// Deletes a folder the shell owns, best effort, and logs the outcome.
    fn remove_folder(&self, folder: &Path) {
        match fs::remove_dir_all(folder) {
            Ok(()) => self.host.log(&format!("Deleted {}.", folder.display())),
            Err(err) => self
                .host
                .log(&format!("Could not delete {}: {err}.", folder.display())),
        }
    }

    fn stopped_during_startup(&self, exit_code: Option<i32>) -> String {
        format!(
            "The worker stopped during startup{}. Details are in {}.",
            exit_code_text(exit_code),
            self.stderr_log.display()
        )
    }
}

/// Checks the reply to the handshake's `status` (C6 step 3), and returns its `startupError`.
fn check_ready(reply: &Value) -> Result<Option<Value>, String> {
    let protocol = reply.get("protocol").and_then(Value::as_u64).unwrap_or(1);
    if event_of(reply) != Some("ready") || protocol != PROTOCOL_VERSION {
        let advice = if cfg!(debug_assertions) {
            "Restart the dev build so it runs the repository worker."
        } else {
            "Reinstall Mathimator."
        };
        return Err(format!(
            "The worker is out of date (protocol {protocol}, this app needs {PROTOCOL_VERSION}). {advice}"
        ));
    }
    Ok(reply
        .get("startupError")
        .filter(|error| !error.is_null())
        .cloned())
}

fn exit_code_text(exit_code: Option<i32>) -> String {
    exit_code
        .map(|code| format!(" (exit code {code})"))
        .unwrap_or_default()
}

fn unavailable(reason: &str) -> ShellError {
    ShellError::new(ErrorCode::WorkerUnavailable, reason)
}

fn event_of(line: &Value) -> Option<&str> {
    line.get("event").and_then(Value::as_str)
}

fn message_of(line: &Value) -> String {
    line.get("message")
        .and_then(Value::as_str)
        .unwrap_or("The worker gave no reason.")
        .to_owned()
}

/// A path as the worker receives it. Request lines are JSON text, so the path must be Unicode.
fn path_text(path: &Path) -> Result<String, ShellError> {
    path.to_str().map(str::to_owned).ok_or_else(|| {
        ShellError::new(
            ErrorCode::BadArgument,
            format!("{} is not a Unicode path.", path.display()),
        )
    })
}

/// Whether a folder has the name the shell gives temp folders, `mathimator-<digits>-<digits>`
/// (C8.5).
fn has_temp_name(folder: &Path) -> bool {
    let digits = |text: &str| !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit());
    folder
        .file_name()
        .and_then(|name| name.to_str())
        .and_then(|name| name.strip_prefix("mathimator-"))
        .and_then(|rest| rest.split_once('-'))
        .is_some_and(|(pid, millis)| digits(pid) && digits(millis))
}

/// A temp project is a folder with the shell's marker (C8.5).
fn is_temp_project(folder: &Path) -> bool {
    fs::read(folder.join(".mathimator").join("temp-project.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .is_some_and(|marker| marker.get("kind").and_then(Value::as_str) == Some("mathimator-temp"))
}

#[cfg(test)]
mod tests {
    use std::io::pipe;
    use std::sync::atomic::AtomicUsize;
    use std::time::Instant;

    use tauri::async_runtime::block_on;

    use super::*;

    const FAKE_EXIT_CODE: i32 = 3;

    /// How a fake worker answers one request: the lines to write, or `None` to exit instead.
    type Answer = dyn Fn(&Value) -> Option<Vec<String>> + Send + Sync;

    /// Records what the shell is told.
    #[derive(Default)]
    struct Recorder {
        events: Mutex<Vec<Value>>,
        binds: Mutex<Vec<PathBuf>>,
        logs: Mutex<Vec<String>>,
        notices: Mutex<Vec<String>>,
    }

    impl Host for Recorder {
        fn emit(&self, event: Value) {
            lock(&self.events).push(event);
        }

        fn bind(&self, project: &Path) {
            lock(&self.binds).push(project.to_owned());
        }

        fn log(&self, message: &str) {
            lock(&self.logs).push(message.to_owned());
        }

        fn notice(&self, message: String) {
            lock(&self.notices).push(message);
        }
    }

    /// A worker launched on fake processes, in a scratch folder that stands in for %TEMP%.
    struct Test {
        worker: Worker,
        host: Arc<Recorder>,
        /// Every request line the fakes read, in order.
        requests: Arc<Mutex<Vec<Value>>>,
        /// `spawn`, `in <cmd>`, `out <id>` and `exited` entries in order, and `end` once a call
        /// to end a fake has returned.
        history: Arc<Mutex<Vec<String>>>,
        /// The folder and `--init` flag of every spawn.
        spawns: Arc<Mutex<Vec<(PathBuf, bool)>>>,
        root: PathBuf,
    }

    impl Test {
        fn start(answer: impl Fn(&Value) -> Option<Vec<String>> + Send + Sync + 'static) -> Self {
            Self::start_with_exit(Exit::WithStdout, answer)
        }

        fn start_with_exit(
            exit: Exit,
            answer: impl Fn(&Value) -> Option<Vec<String>> + Send + Sync + 'static,
        ) -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let root = std::env::temp_dir().join(format!(
                "mathimator-shell-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir_all(&root).unwrap();
            let host = Arc::new(Recorder::default());
            let fake = Fake {
                answer: Arc::new(answer),
                exit,
                requests: Arc::default(),
                history: Arc::default(),
                spawns: Arc::default(),
            };
            let (requests, history, spawns) = (
                Arc::clone(&fake.requests),
                Arc::clone(&fake.history),
                Arc::clone(&fake.spawns),
            );
            let spawn: Spawner =
                Box::new(move |folder: &Path, init: bool| fake.spawn(folder, init));
            let worker = Worker::launch(
                host.clone(),
                spawn,
                root.clone(),
                root.join("supervisor.err"),
            );
            Self {
                worker,
                host,
                requests,
                history,
                spawns,
                root,
            }
        }

        /// The request with this index, once a fake has read it. Index 0 is the handshake.
        fn request(&self, index: usize) -> Value {
            wait_for(|| lock(&self.requests).get(index).cloned())
        }

        fn events(&self) -> Vec<Value> {
            lock(&self.host.events).clone()
        }

        fn history(&self) -> Vec<String> {
            lock(&self.history).clone()
        }

        fn position(&self, entry: &str) -> usize {
            self.history()
                .iter()
                .position(|seen| seen == entry)
                .unwrap()
        }

        fn launch_folder(&self) -> PathBuf {
            wait_for(|| lock(&self.spawns).first().map(|(folder, _)| folder.clone()))
        }
    }

    impl Drop for Test {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    /// When a fake worker's process exits.
    #[derive(Clone, Copy)]
    enum Exit {
        /// As its stdout closes: once its stdin has closed, or when its answer says so.
        WithStdout,
        /// Only once the shell ends it, a moment after it is told to.
        WhenEnded,
        Never,
    }

    struct Fake {
        answer: Arc<Answer>,
        exit: Exit,
        requests: Arc<Mutex<Vec<Value>>>,
        history: Arc<Mutex<Vec<String>>>,
        spawns: Arc<Mutex<Vec<(PathBuf, bool)>>>,
    }

    impl Fake {
        /// A fake worker: one thread reads requests as they arrive, another answers them, so a
        /// slow answer never delays reading the next request.
        fn spawn(&self, folder: &Path, init: bool) -> io::Result<Spawned> {
            lock(&self.spawns).push((folder.to_owned(), init));
            lock(&self.history).push("spawn".to_owned());
            let (stdin, stdin_writer) = pipe()?;
            let (stdout_reader, mut stdout) = pipe()?;
            let (to_answer, inbox) = mpsc::channel::<Value>();
            let (requests, history) = (Arc::clone(&self.requests), Arc::clone(&self.history));
            thread::spawn(move || {
                for line in BufReader::new(stdin).lines() {
                    let Ok(line) = line else { break };
                    let request: Value = serde_json::from_str(&line).unwrap();
                    lock(&history).push(format!("in {}", request["cmd"].as_str().unwrap()));
                    lock(&requests).push(request.clone());
                    if to_answer.send(request).is_err() {
                        break;
                    }
                }
            });
            let exited = Arc::new(AtomicBool::new(false));
            let (answer, exit, history, done) = (
                Arc::clone(&self.answer),
                self.exit,
                Arc::clone(&self.history),
                Arc::clone(&exited),
            );
            thread::spawn(move || {
                for request in inbox {
                    let Some(lines) = answer(&request) else { break };
                    lock(&history).push(format!("out {}", request["id"].as_str().unwrap()));
                    for line in lines {
                        writeln!(stdout, "{line}").unwrap();
                    }
                }
                // stdout closes when this thread ends, after a fake that exits with it has
                // recorded the exit.
                if matches!(exit, Exit::WithStdout) {
                    record_exit(&done, &history);
                }
            });
            Ok(Spawned {
                stdin: Box::new(stdin_writer),
                stdout: Box::new(stdout_reader),
                process: Box::new(FakeProcess {
                    exit: self.exit,
                    exited,
                    history: Arc::clone(&self.history),
                }),
            })
        }
    }

    /// Records a fake's exit, once. The entry comes before the flag, so anything recorded by a
    /// thread that saw the flag comes after `exited`.
    fn record_exit(exited: &AtomicBool, history: &Mutex<Vec<String>>) {
        let mut history = lock(history);
        if !exited.load(Ordering::SeqCst) {
            history.push("exited".to_owned());
            exited.store(true, Ordering::SeqCst);
        }
    }

    struct FakeProcess {
        exit: Exit,
        exited: Arc<AtomicBool>,
        history: Arc<Mutex<Vec<String>>>,
    }

    impl FakeProcess {
        fn wait(&self, limit: Duration) -> bool {
            // A process that never exits is not waited for, which keeps its tests fast.
            if matches!(self.exit, Exit::Never) {
                return false;
            }
            let deadline = Instant::now() + limit;
            while !self.exited.load(Ordering::SeqCst) {
                if Instant::now() >= deadline {
                    return false;
                }
                thread::sleep(Duration::from_millis(5));
            }
            true
        }
    }

    impl WorkerProcess for FakeProcess {
        fn exit_code(&mut self, limit: Duration) -> Option<i32> {
            self.wait(limit).then_some(FAKE_EXIT_CODE)
        }

        fn end(&mut self, limit: Duration) -> bool {
            if matches!(self.exit, Exit::WhenEnded) {
                let (exited, history) = (Arc::clone(&self.exited), Arc::clone(&self.history));
                thread::spawn(move || {
                    thread::sleep(Duration::from_millis(100));
                    record_exit(&exited, &history);
                });
            }
            let exited = self.wait(limit);
            lock(&self.history).push("end".to_owned());
            exited
        }
    }

    fn wait_for<T>(found: impl Fn() -> Option<T>) -> T {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(value) = found() {
                return value;
            }
            assert!(Instant::now() < deadline, "timed out waiting");
            thread::sleep(Duration::from_millis(5));
        }
    }

    fn cmd(request: &Value) -> &str {
        request["cmd"].as_str().unwrap()
    }

    fn ready_line(request: &Value) -> String {
        json!({"event": "ready", "jobId": request["id"], "protocol": 2, "startupError": null})
            .to_string()
    }

    /// A `state` reply. A request that names a folder gets it back as `projectPath`.
    fn state_line(request: &Value) -> String {
        let mut state = json!({"event": "state", "jobId": request["id"]});
        if let Some(path) = request.get("path") {
            state["projectPath"] = path.clone();
        }
        state.to_string()
    }

    fn rejected_line(request: &Value, code: &str) -> String {
        json!({"event": "rejected", "jobId": request["id"], "code": code, "message": "Refused."})
            .to_string()
    }

    /// A `ready` from a worker that could not bind the folder it was started on.
    fn unbound_ready_line(request: &Value) -> String {
        let startup_error = json!({
            "code": "not_a_project",
            "message": "That folder is not a Mathimator project.",
        });
        json!({"event": "ready", "jobId": request["id"], "protocol": 2, "startupError": startup_error})
            .to_string()
    }

    /// Protocol 2 to `status`, `state` to every RPC, and nothing to job commands.
    fn answer(request: &Value) -> Option<Vec<String>> {
        Some(match cmd(request) {
            "status" => vec![ready_line(request)],
            "render_preview" | "export" | "cancel" => vec![],
            _ => vec![state_line(request)],
        })
    }

    /// Like `answer`, but crashes on `undo`: the fake answers nothing more and its stdout closes.
    fn crash_on_undo(request: &Value) -> Option<Vec<String>> {
        if cmd(request) == "undo" {
            None
        } else {
            answer(request)
        }
    }

    /// Writes the temp-project marker into `folder`, creating the folder (C8.5).
    fn mark_temp(folder: &Path) {
        fs::create_dir_all(folder.join(".mathimator")).unwrap();
        fs::write(
            folder.join(".mathimator").join("temp-project.json"),
            json!({"kind": "mathimator-temp", "seedSha256": "0".repeat(64), "createdMs": 1})
                .to_string(),
        )
        .unwrap();
    }

    fn launch_with_ready(ready: Value) -> (Test, ShellError) {
        let test = Test::start(move |request| match cmd(request) {
            "status" => {
                let mut reply = ready.clone();
                reply["jobId"] = request["id"].clone();
                Some(vec![reply.to_string()])
            }
            _ => answer(request),
        });
        let error = block_on(test.worker.get_state()).unwrap_err();
        (test, error)
    }

    #[test]
    fn handshake_accepts_protocol_2_and_claims_its_reply() {
        let test = Test::start(answer);
        let reply = block_on(test.worker.get_state()).unwrap();
        assert_eq!(reply, json!({"event": "state", "jobId": "rpc-2"}));
        assert_eq!(test.request(0), json!({"cmd": "status", "id": "status-1"}));
        let folder = test.launch_folder();
        assert_eq!(lock(&test.spawns).as_slice(), [(folder, true)]);
        assert!(test.events().is_empty(), "claimed replies were broadcast");
    }

    #[test]
    fn handshake_refuses_a_worker_without_a_protocol() {
        let (test, error) = launch_with_ready(json!({"event": "ready"}));
        assert_eq!(error.code, ErrorCode::WorkerUnavailable);
        assert!(
            error
                .message
                .starts_with("The worker is out of date (protocol 1, this app needs 2). "),
            "{}",
            error.message
        );
        assert_eq!(
            test.history(),
            ["spawn", "in status", "out status-1", "exited", "end"]
        );
        assert!(
            test.events().is_empty(),
            "a refused worker was reported as a crash"
        );
    }

    #[test]
    fn handshake_refuses_protocol_3() {
        let (test, error) =
            launch_with_ready(json!({"event": "ready", "protocol": 3, "startupError": null}));
        assert_eq!(error.code, ErrorCode::WorkerUnavailable);
        assert!(
            error
                .message
                .starts_with("The worker is out of date (protocol 3, this app needs 2). "),
            "{}",
            error.message
        );
        assert_eq!(
            lock(&test.requests).len(),
            1,
            "a request reached the refused worker"
        );
    }

    #[test]
    fn a_worker_that_exits_during_startup_is_unavailable() {
        let test = Test::start(|request| {
            if cmd(request) == "status" {
                None
            } else {
                answer(request)
            }
        });
        let error = block_on(test.worker.get_state()).unwrap_err();
        assert_eq!(error.code, ErrorCode::WorkerUnavailable);
        assert!(
            error
                .message
                .starts_with("The worker stopped during startup (exit code 3). Details are in "),
            "{}",
            error.message
        );
    }

    #[test]
    fn startup_error_at_launch_opens_a_new_temp_project_with_a_notice() {
        let test = Test::start(|request| match cmd(request) {
            "status" => Some(vec![unbound_ready_line(request)]),
            _ => answer(request),
        });
        block_on(test.worker.get_state()).unwrap();
        let launched = test.launch_folder();
        let opened = test.request(1);
        let folder = PathBuf::from(opened["path"].as_str().unwrap());
        assert_eq!(
            opened,
            json!({"cmd": "open_project", "id": "rpc-2", "path": opened["path"], "init": true})
        );
        assert_ne!(folder, launched);
        assert!(folder.starts_with(&test.root) && folder.is_dir());
        assert_eq!(test.worker.project(), Some(folder));
        assert_eq!(
            lock(&test.host.notices).as_slice(),
            [format!(
                "Could not reopen {}: That folder is not a Mathimator project. A new project was started.",
                launched.display()
            )]
        );
    }

    #[test]
    fn startup_error_at_respawn_opens_a_new_temp_project_with_a_notice() {
        // The first worker binds its folder; the one started after the crash cannot.
        let test = Test::start(|request| match cmd(request) {
            "status" if request["id"] != "status-1" => Some(vec![unbound_ready_line(request)]),
            _ => crash_on_undo(request),
        });
        block_on(test.worker.undo_document()).unwrap_err();
        block_on(test.worker.get_state()).unwrap();
        let launched = test.launch_folder();
        let opened = test.request(3);
        assert_eq!(
            opened,
            json!({"cmd": "open_project", "id": "rpc-4", "path": opened["path"], "init": true})
        );
        let folder = PathBuf::from(opened["path"].as_str().unwrap());
        assert_ne!(folder, launched);
        assert_eq!(test.worker.project(), Some(folder));
        assert_eq!(
            lock(&test.host.notices).as_slice(),
            [format!(
                "Could not reopen {} after the worker restarted: That folder is not a Mathimator project. A new project was started.",
                launched.display()
            )]
        );
    }

    #[test]
    fn a_refused_fallback_leaves_the_worker_stopped_and_unavailable() {
        let test = Test::start(|request| match cmd(request) {
            "status" => Some(vec![unbound_ready_line(request)]),
            "open_project" => Some(vec![rejected_line(request, "lock_failed")]),
            _ => answer(request),
        });
        let error = block_on(test.worker.get_state()).unwrap_err();
        assert_eq!(
            error,
            ShellError::new(ErrorCode::WorkerUnavailable, "Refused.")
        );
        assert!(test.history().contains(&"end".to_owned()));
        assert!(test.events().is_empty(), "{:?}", test.events());
        assert!(lock(&test.host.notices).is_empty());
    }

    #[test]
    fn a_refusal_is_returned_as_ok_and_not_broadcast() {
        let test = Test::start(|request| match cmd(request) {
            "edit" => Some(vec![rejected_line(request, "project_changed")]),
            _ => answer(request),
        });
        let reply = block_on(
            test.worker
                .edit_document(json!({"op": "set_project_name", "name": "Waves"})),
        )
        .unwrap();
        assert_eq!(
            reply,
            json!({"event": "rejected", "jobId": "rpc-2", "code": "project_changed", "message": "Refused."})
        );
        assert!(test.events().is_empty());
    }

    #[test]
    fn unclaimed_lines_are_broadcast_and_non_json_lines_only_logged() {
        let beat = json!({"event": "beat_ready", "jobId": "preview-7", "beatIndex": 0, "path": "C:/p/b0.mp4"});
        let line = beat.to_string();
        let test = Test::start(move |request| match cmd(request) {
            "get_state" => Some(vec![
                line.clone(),
                "Traceback (most recent call last):".to_owned(),
                state_line(request),
            ]),
            _ => answer(request),
        });
        block_on(test.worker.get_state()).unwrap();
        assert_eq!(test.events(), [beat]);
        assert!(lock(&test.host.logs).iter().any(|log| log
            .starts_with("The worker wrote a line that is not JSON")
            && log.ends_with("Traceback (most recent call last):")));
    }

    #[test]
    fn state_lines_bind_each_new_project_path_once() {
        let test = Test::start(|request| match cmd(request) {
            "get_state" => Some(vec![
                json!({"event": "state", "jobId": request["id"], "projectPath": "C:/a"})
                    .to_string(),
            ]),
            // A late reply: its RPC is no longer pending, so it is broadcast.
            "render_preview" => Some(vec![
                json!({"event": "state", "jobId": "rpc-99", "projectPath": "C:/b"}).to_string(),
            ]),
            _ => answer(request),
        });
        block_on(test.worker.get_state()).unwrap();
        block_on(test.worker.get_state()).unwrap();
        assert_eq!(lock(&test.host.binds).as_slice(), [PathBuf::from("C:/a")]);
        block_on(test.worker.render_preview("preview-4".to_owned())).unwrap();
        let late = wait_for(|| test.events().into_iter().next());
        assert_eq!(late["jobId"], "rpc-99");
        assert_eq!(
            lock(&test.host.binds).as_slice(),
            [PathBuf::from("C:/a"), PathBuf::from("C:/b")]
        );
        assert_eq!(test.worker.project(), Some(PathBuf::from("C:/b")));
    }

    #[test]
    fn a_worker_exit_fails_the_pending_rpc_at_once() {
        let test = Test::start(crash_on_undo);
        let error = block_on(test.worker.undo_document()).unwrap_err();
        assert_eq!(
            error,
            ShellError::new(ErrorCode::WorkerExited, "The worker stopped (exit code 3).")
        );
        let exited = wait_for(|| {
            test.events()
                .into_iter()
                .find(|event| event["event"] == "worker_exited")
        });
        assert_eq!(
            exited,
            json!({"event": "worker_exited", "jobId": null, "message": "The worker stopped (exit code 3).", "exitCode": 3})
        );
        assert!(lock(&test.host.logs).contains(&"The worker stopped (exit code 3).".to_owned()));
    }

    #[test]
    fn eof_fails_only_the_pending_rpcs_of_its_own_generation() {
        let root = std::env::temp_dir();
        let shared = Shared {
            host: Arc::new(Recorder::default()),
            spawn: Box::new(|_: &Path, _: bool| -> io::Result<Spawned> {
                Err(io::Error::other("no spawn"))
            }),
            temp_root: root.clone(),
            stderr_log: root,
            next_id: AtomicU64::new(1),
            next_generation: AtomicU64::new(1),
            routes: Mutex::default(),
            bound: Mutex::default(),
        };
        let (old_reply, old) = mpsc::channel();
        let (new_reply, new) = mpsc::channel();
        {
            let mut routes = lock(&shared.routes);
            routes.open = Some(2);
            routes.pending.insert(
                "rpc-1".to_owned(),
                Pending {
                    generation: 1,
                    reply: old_reply,
                },
            );
            routes.pending.insert(
                "rpc-2".to_owned(),
                Pending {
                    generation: 2,
                    reply: new_reply,
                },
            );
        }
        shared.close(1, Some(FAKE_EXIT_CODE), true);
        assert!(matches!(
            old.try_recv(),
            Ok(Err(Failure::Exited(Some(FAKE_EXIT_CODE))))
        ));
        assert!(new.try_recv().is_err());
        let routes = lock(&shared.routes);
        assert_eq!(routes.open, Some(2));
        assert!(routes.pending.contains_key("rpc-2"));
    }

    #[test]
    fn the_next_command_respawns_on_the_bound_folder_once_the_old_worker_exited() {
        // The crashed worker closes its stdout but runs on until the shell ends it.
        let test = Test::start_with_exit(Exit::WhenEnded, crash_on_undo);
        block_on(test.worker.get_state()).unwrap();
        block_on(test.worker.undo_document()).unwrap_err();
        block_on(test.worker.get_state()).unwrap();
        let folder = test.launch_folder();
        assert_eq!(
            lock(&test.spawns).as_slice(),
            [(folder.clone(), true), (folder, false)]
        );
        let history = test.history();
        let respawn = history.iter().rposition(|entry| entry == "spawn").unwrap();
        assert!(
            history[..respawn].contains(&"exited".to_owned()),
            "the new worker started before the old one exited: {history:?}"
        );
        assert_eq!(test.request(3), json!({"cmd": "status", "id": "status-4"}));
    }

    #[test]
    fn an_old_worker_that_does_not_exit_leaves_the_worker_unavailable() {
        let test = Test::start_with_exit(Exit::Never, crash_on_undo);
        block_on(test.worker.undo_document()).unwrap_err();
        let error = block_on(test.worker.get_state()).unwrap_err();
        assert_eq!(
            error,
            ShellError::new(
                ErrorCode::WorkerUnavailable,
                "The previous worker did not stop. Restart Mathimator."
            )
        );
        assert_eq!(lock(&test.spawns).len(), 1, "a second worker was started");
    }

    #[test]
    fn a_timed_out_rpc_fails_and_its_late_reply_is_broadcast() {
        let test = Test::start(|request| {
            if cmd(request) == "get_state" {
                thread::sleep(Duration::from_millis(300));
            }
            answer(request)
        });
        let error = block_on(
            test.worker
                .rpc(Request::GetState, Duration::from_millis(50)),
        )
        .unwrap_err();
        assert_eq!(
            error,
            ShellError::new(ErrorCode::Timeout, "The worker did not answer in time.")
        );
        let late = wait_for(|| {
            test.events()
                .into_iter()
                .find(|event| event["jobId"] == "rpc-2")
        });
        assert_eq!(late["event"], "state");
    }

    #[test]
    fn a_request_is_written_only_after_the_outstanding_reply() {
        let test = Test::start(|request| {
            if cmd(request) == "get_state" {
                thread::sleep(Duration::from_millis(300));
            }
            answer(request)
        });
        let target = test.root.join("saved");
        thread::scope(|scope| {
            scope.spawn(|| block_on(test.worker.get_state()).unwrap());
            wait_for(|| {
                test.history()
                    .contains(&"in get_state".to_owned())
                    .then_some(())
            });
            scope.spawn(|| block_on(test.worker.save_project_as(&target)).unwrap());
        });
        assert!(
            test.position("out rpc-2") < test.position("in save_as"),
            "{:?}",
            test.history()
        );
    }

    /// Calls the worker method behind a worker-bound Tauri command, past its dialog if it has one.
    fn call(test: &Test, command: &str) -> Result<(), ShellError> {
        let worker = &test.worker;
        let folder = test.root.join("picked");
        match command {
            "get_state" => block_on(worker.get_state()).map(drop),
            "edit_document" => block_on(worker.edit_document(json!({"op": "undo"}))).map(drop),
            "undo_document" => block_on(worker.undo_document()).map(drop),
            "redo_document" => block_on(worker.redo_document()).map(drop),
            "clear_cache" => block_on(worker.clear_cache()).map(drop),
            "reload_project" => block_on(worker.reload_project()).map(drop),
            "overwrite_project" => block_on(worker.overwrite_project()).map(drop),
            "new_project" => block_on(worker.new_project()).map(drop),
            "open_sample" => {
                let sample = test.root.join("sample.json");
                fs::write(&sample, b"{}").unwrap();
                block_on(worker.open_sample(sample)).map(drop)
            }
            "open_project_dialog" | "open_project_path" => {
                block_on(worker.open_project(&folder)).map(drop)
            }
            "save_project_as_dialog" => block_on(worker.save_project_as(&folder)).map(drop),
            "export_code_dialog" => block_on(worker.generated_code()).map(drop),
            "render_preview" => block_on(worker.render_preview("preview-9".to_owned())),
            "export_movie" => block_on(worker.export_movie("export-9".to_owned(), false)),
            "cancel_render" => block_on(worker.cancel_render("preview-9".to_owned())),
            _ => panic!("no worker method for {command}"),
        }
    }

    #[test]
    fn each_worker_bound_command_sends_the_cmd_commands_json_names() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol/commands.json"
        ))
        .unwrap();
        for entry in fixture["tauriCommands"].as_array().unwrap() {
            let Some(cmd) = entry["worker"].as_str() else {
                continue;
            };
            let test = Test::start(answer);
            call(&test, entry["name"].as_str().unwrap()).unwrap();
            assert_eq!(test.request(1)["cmd"], cmd, "{entry}");
        }
    }

    #[test]
    fn get_state_writes_get_state() {
        let test = Test::start(answer);
        block_on(test.worker.get_state()).unwrap();
        assert_eq!(test.request(1), json!({"cmd": "get_state", "id": "rpc-2"}));
    }

    #[test]
    fn edit_document_writes_edit() {
        let test = Test::start(answer);
        let edit = json!({"op": "set_beat_duration", "beatId": "beat_1", "duration": 2.5});
        block_on(test.worker.edit_document(edit.clone())).unwrap();
        assert_eq!(
            test.request(1),
            json!({"cmd": "edit", "id": "rpc-2", "edit": edit})
        );
    }

    #[test]
    fn undo_document_writes_undo() {
        let test = Test::start(answer);
        block_on(test.worker.undo_document()).unwrap();
        assert_eq!(test.request(1), json!({"cmd": "undo", "id": "rpc-2"}));
    }

    #[test]
    fn redo_document_writes_redo() {
        let test = Test::start(answer);
        block_on(test.worker.redo_document()).unwrap();
        assert_eq!(test.request(1), json!({"cmd": "redo", "id": "rpc-2"}));
    }

    #[test]
    fn clear_cache_writes_clear_cache() {
        let test = Test::start(answer);
        block_on(test.worker.clear_cache()).unwrap();
        assert_eq!(
            test.request(1),
            json!({"cmd": "clear_cache", "id": "rpc-2"})
        );
    }

    #[test]
    fn reload_project_writes_reload() {
        let test = Test::start(answer);
        block_on(test.worker.reload_project()).unwrap();
        assert_eq!(test.request(1), json!({"cmd": "reload", "id": "rpc-2"}));
    }

    #[test]
    fn overwrite_project_writes_overwrite() {
        let test = Test::start(answer);
        block_on(test.worker.overwrite_project()).unwrap();
        assert_eq!(test.request(1), json!({"cmd": "overwrite", "id": "rpc-2"}));
    }

    #[test]
    fn export_code_writes_code() {
        let test = Test::start(answer);
        block_on(test.worker.generated_code()).unwrap();
        assert_eq!(test.request(1), json!({"cmd": "code", "id": "rpc-2"}));
    }

    #[test]
    fn open_project_writes_open_project_without_init() {
        let test = Test::start(answer);
        let folder = test.root.join("lesson");
        block_on(test.worker.open_project(&folder)).unwrap();
        assert_eq!(
            test.request(1),
            json!({"cmd": "open_project", "id": "rpc-2", "path": folder.to_str().unwrap()})
        );
    }

    #[test]
    fn new_project_writes_open_project_with_init_on_a_new_temp_folder() {
        let test = Test::start(answer);
        block_on(test.worker.new_project()).unwrap();
        let request = test.request(1);
        assert_eq!(
            request,
            json!({"cmd": "open_project", "id": "rpc-2", "path": request["path"], "init": true})
        );
        let folder = PathBuf::from(request["path"].as_str().unwrap());
        assert!(folder.is_dir() && folder.parent() == Some(test.root.as_path()));
        assert_ne!(folder, test.launch_folder());
    }

    #[test]
    fn open_sample_writes_open_project_on_a_copy_of_the_sample() {
        let test = Test::start(answer);
        let sample = test.root.join("sample.json");
        fs::write(&sample, b"{\"name\": \"sample\"}").unwrap();
        block_on(test.worker.open_sample(sample)).unwrap();
        let request = test.request(1);
        assert_eq!(
            request,
            json!({"cmd": "open_project", "id": "rpc-2", "path": request["path"]})
        );
        let folder = PathBuf::from(request["path"].as_str().unwrap());
        assert_eq!(
            fs::read(folder.join("project.json")).unwrap(),
            b"{\"name\": \"sample\"}"
        );
    }

    #[test]
    fn save_project_as_writes_save_as() {
        let test = Test::start(answer);
        let target = test.root.join("saved");
        block_on(test.worker.save_project_as(&target)).unwrap();
        assert_eq!(
            test.request(1),
            json!({"cmd": "save_as", "id": "rpc-2", "path": target.to_str().unwrap()})
        );
    }

    #[test]
    fn render_preview_writes_render_preview_with_the_job_id() {
        let test = Test::start(answer);
        block_on(test.worker.render_preview("preview-5".to_owned())).unwrap();
        assert_eq!(
            test.request(1),
            json!({"cmd": "render_preview", "id": "preview-5"})
        );
    }

    #[test]
    fn export_movie_writes_export_with_force() {
        let test = Test::start(answer);
        block_on(test.worker.export_movie("export-3".to_owned(), true)).unwrap();
        block_on(test.worker.export_movie("export-4".to_owned(), false)).unwrap();
        assert_eq!(
            test.request(1),
            json!({"cmd": "export", "id": "export-3", "force": true})
        );
        assert_eq!(
            test.request(2),
            json!({"cmd": "export", "id": "export-4", "force": false})
        );
    }

    #[test]
    fn cancel_render_writes_cancel_with_its_own_id() {
        let test = Test::start(answer);
        block_on(test.worker.cancel_render("preview-2".to_owned())).unwrap();
        assert_eq!(
            test.request(1),
            json!({"cmd": "cancel", "id": "cancel-2", "jobId": "preview-2"})
        );
    }

    #[test]
    fn new_project_deletes_its_folder_when_refused() {
        let test = Test::start(|request| match cmd(request) {
            "open_project" => Some(vec![rejected_line(request, "lock_failed")]),
            _ => answer(request),
        });
        let reply = block_on(test.worker.new_project()).unwrap();
        assert_eq!(reply["event"], "rejected");
        assert!(!Path::new(test.request(1)["path"].as_str().unwrap()).exists());
    }

    #[test]
    fn new_project_keeps_its_folder_unmarked_when_the_worker_stops() {
        let test = Test::start(|request| {
            if cmd(request) == "open_project" {
                None
            } else {
                answer(request)
            }
        });
        let error = block_on(test.worker.new_project()).unwrap_err();
        assert_eq!(error.code, ErrorCode::WorkerExited);
        let folder = PathBuf::from(test.request(1)["path"].as_str().unwrap());
        assert!(folder.is_dir());
        assert!(!is_temp_project(&folder));
    }

    #[test]
    fn open_sample_deletes_its_folder_when_refused() {
        let test = Test::start(|request| match cmd(request) {
            "open_project" => Some(vec![rejected_line(request, "invalid_document")]),
            _ => answer(request),
        });
        let sample = test.root.join("sample.json");
        fs::write(&sample, b"{}").unwrap();
        let reply = block_on(test.worker.open_sample(sample)).unwrap();
        assert_eq!(reply["event"], "rejected");
        assert!(!Path::new(test.request(1)["path"].as_str().unwrap()).exists());
    }

    #[test]
    fn save_as_deletes_the_old_folder_of_a_temp_project() {
        let test = Test::start(answer);
        let old = test.launch_folder();
        mark_temp(&old);
        let target = test.root.join("saved");
        fs::create_dir(&target).unwrap();
        let reply = block_on(test.worker.save_project_as(&target)).unwrap();
        assert_eq!(reply["projectPath"], target.to_str().unwrap());
        assert!(!old.exists());
        assert_eq!(test.worker.project(), Some(target));
    }

    #[test]
    fn save_as_keeps_the_old_folder_of_a_saved_project() {
        let test = Test::start(answer);
        let old = test.launch_folder();
        let target = test.root.join("saved");
        fs::create_dir(&target).unwrap();
        block_on(test.worker.save_project_as(&target)).unwrap();
        assert!(old.is_dir());
    }

    #[test]
    fn save_as_into_a_folder_inside_the_temp_project_keeps_both() {
        let test = Test::start(answer);
        let old = test.launch_folder();
        mark_temp(&old);
        let target = old.join("saved");
        fs::create_dir(&target).unwrap();
        block_on(test.worker.save_project_as(&target)).unwrap();
        assert!(target.is_dir());
        assert_eq!(test.worker.project(), Some(target));
    }

    #[test]
    fn save_as_keeps_a_marked_folder_the_shell_did_not_make() {
        let test = Test::start(answer);
        // Copies of a temp project: one kept outside %TEMP%, one beside it under another name.
        let copies = [
            test.root.join("lessons").join("mathimator-1-2"),
            test.root.join("mathimator-1-2 - Copy"),
        ];
        for (index, old) in copies.iter().enumerate() {
            mark_temp(old);
            block_on(test.worker.open_project(old)).unwrap();
            let target = test.root.join(format!("saved-{index}"));
            fs::create_dir(&target).unwrap();
            block_on(test.worker.save_project_as(&target)).unwrap();
            assert!(old.is_dir(), "{} was deleted", old.display());
        }
    }

    #[test]
    fn temp_folder_names_are_mathimator_digits_digits() {
        for name in ["mathimator-1-2", "mathimator-4321-1761234567890"] {
            assert!(has_temp_name(Path::new(name)), "{name}");
        }
        for name in [
            "mathimator-1-2 - Copy",
            "mathimator-1-2-3",
            "mathimator-shell-test-1-2",
            "mathimator--2",
            "mathimator-1-",
            "mathimator-12",
            "Mathimator-1-2",
        ] {
            assert!(!has_temp_name(Path::new(name)), "{name}");
        }
    }
}
