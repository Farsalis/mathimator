//! The worker as the shell runs it: start and handshake (contract C6), the request gate and the
//! per-generation pending replies (C9.1, C9.2), routing worker stdout (C9.4), exit and respawn
//! (C8.6), the temp projects it is started on (C8.5), and the status the UI starts from (C9.5).
//! Every worker-bound command of C9.1 has a method on [`Worker`].

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
use crate::temp_project::{self, Marker};
use crate::{lock, sha256, unix_millis};

const RPC_TIMEOUT: Duration = Duration::from_secs(30);
const CLEAR_CACHE_TIMEOUT: Duration = Duration::from_secs(120);
const SAVE_AS_TIMEOUT: Duration = Duration::from_secs(600);
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(30);
/// How long the router waits for the exit code once a worker's stdout has closed (C8.6).
const EXIT_CODE_WAIT: Duration = Duration::from_secs(1);
/// How long the shell waits for a worker it ended to exit (C8.6).
const STOP_WAIT: Duration = Duration::from_secs(5);
/// Why no worker starts while an ended one is still alive (C8.6 step 4). Retry ends it again, and
/// starts a worker once it has exited.
const STUCK: &str =
    "The previous worker did not stop. Choose Retry to try again, or restart Mathimator.";

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
    gate: Arc<async_runtime::Mutex<Gate>>,
    /// Held for writing until the launch is done, so a read lock waits for the launch and not for
    /// the requests after it (C8.5 launch step 5, C9.1).
    launch: Arc<async_runtime::RwLock<()>>,
}

/// What the holder of the request gate owns.
#[derive(Default)]
struct Gate {
    status: Status,
    /// A worker that was ended but was still alive 5 s later. It is ended again before any worker
    /// starts, so a new worker never meets its project lock (C8.6 step 4).
    stuck: Option<Arc<Process>>,
}

#[derive(Default)]
enum Status {
    /// No worker runs: none has been started yet, or the last one was ended.
    #[default]
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
    child: Mutex<Child>,
    /// Set when the shell ends the process, so that its exit is not reported as a crash.
    stopping: AtomicBool,
}

/// A worker process, and whether an end has seen it exit.
struct Child {
    process: Box<dyn WorkerProcess>,
    exited: bool,
}

/// Where a worker starts (C8.5 launch step 2, C8.6 step 5).
#[derive(Clone, Copy)]
enum Start {
    /// The first worker, on the last project of the previous run.
    Reopen,
    /// On a new temp project, the only start with `--init`.
    New,
    /// After an exit, on the bound folder.
    Respawn,
}

struct Shared {
    host: Arc<dyn Host>,
    /// Starts a worker, or says why none can: the runtime has no worker (C9.6).
    spawn: Result<Spawner, String>,
    /// Where temp projects are created.
    temp_root: PathBuf,
    /// The last project of the previous run, until a worker takes it or, when it is missing, until
    /// a worker has started in its place (C8.5).
    reopen: Mutex<Option<PathBuf>>,
    /// supervisor.err, which the "stopped during startup" reason points to.
    stderr_log: PathBuf,
    /// The one counter behind every request and job id (C2).
    next_id: AtomicU64,
    next_generation: AtomicU64,
    routes: Mutex<Routes>,
    bound: Mutex<Bound>,
    /// Why the worker is unavailable, as the last holder of the request gate left it, so that
    /// `app_status` does not wait for the gate (C9.1, C9.2).
    unavailable: Mutex<Option<String>>,
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
    stuck: &'a mut Option<Arc<Process>>,
    shared: &'a Arc<Shared>,
}

impl Gate {
    /// Runs `work` as the holder of the gate, then mirrors the status it leaves for `app_status`.
    fn session<T>(&mut self, shared: &Arc<Shared>, work: impl FnOnce(&mut Session<'_>) -> T) -> T {
        let result = work(&mut Session {
            status: &mut self.status,
            stuck: &mut self.stuck,
            shared,
        });
        *lock(&shared.unavailable) = match &self.status {
            Status::Unavailable(reason) => Some(reason.clone()),
            Status::Stopped | Status::Running(_) => None,
        };
        result
    }
}

impl Process {
    fn new(process: Box<dyn WorkerProcess>) -> Self {
        Self {
            child: Mutex::new(Child {
                process,
                exited: false,
            }),
            stopping: AtomicBool::new(false),
        }
    }

    /// Ends the process for good, and returns whether it exited within 5 s (C8.6 step 4). A
    /// process that exited is not ended again, so a crashed worker that both the router and the
    /// next command end is ended once.
    fn end(&self) -> bool {
        let mut child = lock(&self.child);
        if !child.exited {
            child.exited = child.process.end(STOP_WAIT);
        }
        child.exited
    }

    fn exit_code(&self, limit: Duration) -> Option<i32> {
        lock(&self.child).process.exit_code(limit)
    }
}

impl Worker {
    /// Starts the first worker in the background: on `last_project` when it is still a project
    /// folder, else on a new temp project in `temp_root`, with a notice when the last project is
    /// missing (C8.5). Every worker-bound command waits until its handshake is done (C6). Without
    /// a spawner, every command fails with `worker_unavailable` and the reason (D31).
    pub fn launch(
        host: Arc<dyn Host>,
        spawn: Result<Spawner, String>,
        temp_root: PathBuf,
        last_project: Option<PathBuf>,
        stderr_log: PathBuf,
    ) -> Self {
        let shared = Arc::new(Shared {
            host,
            spawn,
            temp_root,
            reopen: Mutex::new(last_project),
            stderr_log,
            next_id: AtomicU64::new(1),
            next_generation: AtomicU64::new(1),
            routes: Mutex::default(),
            bound: Mutex::default(),
            unavailable: Mutex::default(),
        });
        let gate = Arc::new(async_runtime::Mutex::new(Gate::default()));
        let launch = Arc::new(async_runtime::RwLock::new(()));
        let mut held = Arc::clone(&gate)
            .try_lock_owned()
            .expect("a new request gate is free");
        let launching = Arc::clone(&launch)
            .try_write_owned()
            .expect("a new launch lock is free");
        let starter = Arc::clone(&shared);
        async_runtime::spawn_blocking(move || {
            // A failed launch leaves the worker unavailable, and every command reports why.
            let _ = held.session(&starter, |session| session.ready());
            drop(launching);
        });
        Self {
            shared,
            gate,
            launch,
        }
    }

    /// `reserve_job_id` (C9.1): the next id from the one id counter, for example `preview-7`. Only
    /// previews and exports reserve ids.
    pub fn reserve_job_id(&self, prefix: &str) -> Result<String, ShellError> {
        match prefix {
            "preview" | "export" => Ok(self.shared.next_id(prefix)),
            _ => Err(ShellError::new(
                ErrorCode::BadArgument,
                format!("Job ids are for previews and exports, not {prefix:?}."),
            )),
        }
    }

    /// The bound project folder (C9.4), once a worker has been started.
    pub fn project(&self) -> Option<PathBuf> {
        self.shared.project()
    }

    /// Waits until the launch handshake is done (C8.5 launch step 5).
    pub async fn launched(&self) {
        drop(self.launch.read().await);
    }

    /// Why worker-bound commands fail with `worker_unavailable`, or `None` when they reach a
    /// worker or start one (C9.5). Waits for the launch, but not for a request in flight, which
    /// may take minutes (C9.1).
    pub async fn unavailable(&self) -> Option<String> {
        self.launched().await;
        lock(&self.shared.unavailable).clone()
    }

    /// `restart_worker` (C9.1): makes sure a worker runs, retrying one that is unavailable, and
    /// returns why it still is. A worker that did not stop must exit before another starts.
    pub async fn restart(&self) -> Result<Option<String>, ShellError> {
        self.hold(|session| {
            if matches!(session.status, Status::Unavailable(_)) {
                *session.status = Status::Stopped;
            }
            Ok(session.ready().err().map(|error| error.message))
        })
        .await
    }

    /// Tells the running worker the message its later job starts refuse formulas with, or `None`
    /// once TeX works (C9.7, C14 Q4). Nothing running stops, and the document keeps its undo
    /// history. A worker that is not running is left alone: the next one starts with the shell's
    /// TeX error as MATHIMATOR_TEX_ERROR.
    pub async fn set_tex_error(&self, message: Option<String>) -> Result<(), ShellError> {
        self.hold(move |session| session.set_tex_error(message))
            .await
    }

    /// Whether leaving the bound project asks first: it is a touched temp project, and a worker
    /// can still save it elsewhere (C8.5, C14 Q2, Q8). The check holds the request gate, so it
    /// sees what the request in flight wrote, but it starts no worker.
    pub async fn is_touched_temp_project(&self) -> bool {
        self.hold(|session| Ok(session.is_touched_temp_project()))
            .await
            .unwrap_or(false)
    }

    /// Deletes the temp projects that pruning allows, once the launch is done (C8.5 launch step
    /// 5). The bound project is kept, and so is a last project no worker has taken yet.
    pub async fn prune_temp_projects(&self) {
        self.launched().await;
        let shared = Arc::clone(&self.shared);
        let _ = async_runtime::spawn_blocking(move || {
            let keep: Vec<PathBuf> = [shared.project(), lock(&shared.reopen).clone()]
                .into_iter()
                .flatten()
                .collect();
            temp_project::prune(&shared.temp_root, &keep, unix_millis(), &|line| {
                shared.host.log(line);
            });
        })
        .await;
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

    /// Runs `work` holding the request gate, once the worker is ready.
    async fn run<T, F>(&self, work: F) -> Result<T, ShellError>
    where
        T: Send + 'static,
        F: FnOnce(&mut Session<'_>) -> Result<T, ShellError> + Send + 'static,
    {
        self.hold(move |session| {
            session.ready()?;
            work(session)
        })
        .await
    }

    /// Runs `work` holding the request gate. The work blocks on pipes and replies, so it runs on
    /// the blocking pool, never on the UI thread (D20).
    async fn hold<T, F>(&self, work: F) -> Result<T, ShellError>
    where
        T: Send + 'static,
        F: FnOnce(&mut Session<'_>) -> Result<T, ShellError> + Send + 'static,
    {
        let mut gate = Arc::clone(&self.gate).lock_owned().await;
        let shared = Arc::clone(&self.shared);
        async_runtime::spawn_blocking(move || gate.session(&shared, work))
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

    /// Ends the old worker, then starts one and runs the handshake (C6). The worker starts on the
    /// bound folder after an exit (C8.6), on the last project at launch, and otherwise on a new
    /// temp project (C8.5). Returns the reason the worker is unavailable.
    fn start(&mut self) -> Result<(), String> {
        self.stop()?;
        let shared = self.shared;
        let spawn = shared.spawn.as_ref().map_err(String::clone)?;
        let (folder, start) = match shared.project() {
            Some(folder) => (folder, Start::Respawn),
            None => match shared.take_reopen() {
                Some(folder) => (folder, Start::Reopen),
                None => (
                    shared.create_temp_project().map_err(|err| err.message)?,
                    Start::New,
                ),
            },
        };
        let init = matches!(start, Start::New);
        // Bound before the spawn: a folder the worker could not start on stays bound, so a retry
        // starts there again, unless it is a new one, which is abandoned.
        shared.set_project(&folder);
        let spawned = spawn(&folder, init).map_err(|err| {
            if init {
                shared.abandon(&folder);
            }
            format!("Could not start the worker: {err}.")
        })?;
        shared
            .host
            .log(&format!("Started a worker on {}.", folder.display()));
        *self.status = Status::Running(shared.connect(spawned));
        let started = self
            .handshake()
            .and_then(|startup_error| match startup_error {
                Some(error) => self.recover(&folder, &error, start),
                None => {
                    // C8.5 launch step 4, before the commands waiting for the launch go on.
                    if init {
                        shared.mark(&folder, temp_project::seed_of(&folder));
                    }
                    shared.report(&folder);
                    Ok(())
                }
            });
        if started.is_ok() {
            shared.report_missing_last_project();
        } else {
            if let Err(reason) = self.stop() {
                shared.host.log(&reason);
            }
            if init {
                // No worker took the launch folder, so a retry launches afresh.
                shared.abandon(&folder);
            }
        }
        started
    }

    /// Ends the running worker, or the one that did not stop before, for good and waits for it to
    /// exit (C8.6 step 4). A worker still alive after 5 s is kept as stuck.
    fn stop(&mut self) -> Result<(), String> {
        let process = match mem::replace(self.status, Status::Stopped) {
            Status::Running(Link { stdin, process, .. }) => {
                // Set before its stdin closes, so that the router never takes the exit that
                // follows for a crash and ends the process too.
                process.stopping.store(true, Ordering::SeqCst);
                drop(stdin);
                process
            }
            Status::Stopped | Status::Unavailable(_) => match self.stuck.take() {
                Some(process) => process,
                None => return Ok(()),
            },
        };
        if !process.end() {
            *self.stuck = Some(process);
            return Err(STUCK.to_owned());
        }
        let exit_code = process.exit_code(Duration::ZERO);
        self.shared
            .host
            .log(&format!("Stopped the worker{}.", exit_code_text(exit_code)));
        Ok(())
    }

    /// Sends `set_tex_error` to a running worker whose stdout is still open (C9.7).
    fn set_tex_error(&mut self, message: Option<String>) -> Result<(), ShellError> {
        match &*self.status {
            Status::Running(link) if self.shared.is_open(link.generation) => {}
            _ => return Ok(()),
        }
        let id = self.shared.next_id("tex");
        self.send(&Request::SetTexError { message }, &id)
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
    /// C8.6 step 5), and tells the user why. A new temp folder it could not bind held no work:
    /// it is deleted, and only shell.log hears of it.
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
        let shown = folder.display();
        let host = &self.shared.host;
        match start {
            Start::Reopen => host.notice(format!(
                "Could not reopen {shown}: {reason}. A new project was started."
            )),
            Start::Respawn => host.notice(format!(
                "Could not reopen {shown} after the worker restarted: {reason}. A new project was started."
            )),
            Start::New => {
                host.log(&format!(
                    "The worker could not start a project in {shown}: {reason}."
                ));
                self.shared.remove_folder(folder);
            }
        }
        Ok(())
    }

    fn exit_code(&self) -> Option<i32> {
        match &*self.status {
            Status::Running(link) => link.process.exit_code(EXIT_CODE_WAIT),
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

    /// New (C8.5): opens a new temp project, which the worker fills with an empty project.
    fn open_new_project(&mut self) -> Result<Value, ShellError> {
        let folder = self.shared.create_temp_project()?;
        self.open_temp_project(&folder, None)
    }

    /// Sample (C8.5): opens a temp copy of the bundled sample.
    fn open_sample(&mut self, sample: &Path) -> Result<Value, ShellError> {
        let folder = self.shared.create_temp_project()?;
        let copied = fs::read(sample).and_then(|bytes| {
            fs::write(folder.join("project.json"), &bytes)?;
            Ok(sha256::hex_digest(&bytes))
        });
        match copied {
            Ok(seed) => self.open_temp_project(&folder, Some(seed)),
            Err(err) => {
                self.shared.remove_folder(&folder);
                Err(ShellError::new(
                    ErrorCode::Io,
                    format!("Could not copy the sample project: {err}."),
                ))
            }
        }
    }

    /// Opens a temp folder this session created (C8.5). `seed` is the SHA-256 of the project.json
    /// copied into it. Without one, the worker writes an empty project there (`init`), and the
    /// seed is read from that. Once the reply confirms the bind, the folder is marked. A refusal
    /// deletes it. A shell failure keeps it unmarked, because a late reply may still bind it.
    fn open_temp_project(
        &mut self,
        folder: &Path,
        seed: Option<String>,
    ) -> Result<Value, ShellError> {
        let path = path_text(folder)?;
        let init = seed.is_none();
        let reply = self.rpc(&Request::OpenProject { path, init }, RPC_TIMEOUT)?;
        match event_of(&reply) {
            Some("state") => {
                let seed = seed.map_or_else(|| temp_project::seed_of(folder), Ok);
                self.shared.mark(folder, seed);
            }
            Some("rejected") => self.shared.remove_folder(folder),
            _ => {}
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

    /// Whether the bound project is a touched temp project (C8.5), while the worker is not
    /// unavailable. An unmarked temp folder, such as one a late reply bound, counts as touched:
    /// pruning may delete it whatever its project.json holds.
    fn is_touched_temp_project(&self) -> bool {
        if matches!(self.status, Status::Unavailable(_)) {
            return false;
        }
        self.shared.project().is_some_and(|folder| {
            self.shared.temp_folder(&folder).is_some_and(|folder| {
                Marker::read(&folder).is_none_or(|marker| marker.is_touched(&folder))
            })
        })
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
        let process = Arc::new(Process::new(spawned.process));
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
            process.exit_code(EXIT_CODE_WAIT)
        } else {
            None
        };
        self.close(generation, exit_code, crashed);
        if crashed {
            // Ending it for good also stops a Manim grandchild that outlived it. The next command
            // starts another worker only once it has exited, and ends it again only if it is
            // still alive after this end.
            process.end();
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

    /// The last project, when it is still a folder with project.json (C8.5 launch step 2). Only
    /// the first start is offered it. A missing one stays until a worker has started in its place.
    fn take_reopen(&self) -> Option<PathBuf> {
        lock(&self.reopen).take_if(|folder| folder.join("project.json").is_file())
    }

    /// Tells the user, once a worker has started in its place, that the last project was missing
    /// at launch (C8.5 launch step 2). A launch that fails tells it after the start that succeeds.
    fn report_missing_last_project(&self) {
        let missing = lock(&self.reopen).take();
        if let Some(missing) = missing {
            self.host.notice(format!(
                "Could not reopen {}: The folder or its project.json is missing. A new project was started.",
                missing.display()
            ));
        }
    }

    /// Marks a temp folder the worker has just bound (C8.5), with `seed`, the SHA-256 of the
    /// project.json it started from.
    fn mark(&self, folder: &Path, seed: io::Result<String>) {
        let marked = seed.and_then(|seed| Marker::new(seed, unix_millis()).write(folder));
        if let Err(err) = marked {
            self.host.log(&format!(
                "Could not mark {} as a temp project: {err}.",
                folder.display()
            ));
        }
    }

    /// `folder` in canonical form when it is a temp folder (C8.5): a `mathimator-<digits>-<digits>`
    /// folder in the temp root, as pruning requires, so that a copy kept elsewhere is never taken
    /// for one. The paths are compared in canonical form, so an 8.3 %TEMP% still matches the long
    /// form the worker reports.
    fn temp_folder(&self, folder: &Path) -> Option<PathBuf> {
        let folder = fs::canonicalize(folder).ok()?;
        let temp_root = fs::canonicalize(&self.temp_root).ok()?;
        let is_temp = folder.parent() == Some(temp_root.as_path())
            && temp_project::created_ms_in_name(&folder).is_some();
        is_temp.then_some(folder)
    }

    /// The marker of `folder` when it is a temp project the shell made (C8.5): a marked temp
    /// folder.
    fn temp_marker(&self, folder: &Path) -> Option<Marker> {
        Marker::read(&self.temp_folder(folder)?)
    }

    /// Whether `old`, the folder Save As just moved away from, is a temp project the shell may
    /// delete (C8.5): one that does not hold `target`.
    fn is_left_temp_project(&self, old: &Path, target: &Path) -> bool {
        let (Ok(old), Ok(target)) = (fs::canonicalize(old), fs::canonicalize(target)) else {
            return false;
        };
        !target.starts_with(&old) && self.temp_marker(&old).is_some()
    }

    /// Forgets a launch folder that no worker took, and deletes it.
    fn abandon(&self, folder: &Path) {
        {
            let mut bound = lock(&self.bound);
            if bound.folder.as_deref() == Some(folder) {
                bound.folder = None;
            }
        }
        self.remove_folder(folder);
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

pub fn event_of(line: &Value) -> Option<&str> {
    line.get("event").and_then(Value::as_str)
}

pub fn message_of(line: &Value) -> String {
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

#[cfg(test)]
mod tests {
    use std::io::pipe;
    use std::sync::atomic::AtomicUsize;
    use std::sync::OnceLock;
    use std::time::Instant;

    use tauri::async_runtime::block_on;

    use super::*;
    use crate::{spawner, tex_checked, Tex, TexStatus};

    const FAKE_EXIT_CODE: i32 = 3;
    const TEX_FAILED: &str = "Formulas and axis labels cannot render on this PC: latex failed (exit code: 1). Reinstall Mathimator for all users.";

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
            Self::launch(exit, answer, fake_spawner)
        }

        /// A worker whose runtime could not be resolved, so it has no spawner.
        fn without_runtime(reason: &str) -> Self {
            Self::launch(Exit::WithStdout, answer, |_| Err(reason.to_owned()))
        }

        /// A worker launched with the folder `name` in its root as the last project. The folder
        /// holds `project` as project.json, when there is one.
        fn reopening(
            name: &str,
            project: Option<&[u8]>,
            answer: impl Fn(&Value) -> Option<Vec<String>> + Send + Sync + 'static,
        ) -> Self {
            let root = scratch_root();
            let last = root.join(name);
            fs::create_dir(&last).unwrap();
            if let Some(project) = project {
                fs::write(last.join("project.json"), project).unwrap();
            }
            Self::launch_in(root, Some(last), Exit::WithStdout, answer, fake_spawner)
        }

        /// A worker whose spawner `spawner` makes from the fake.
        fn launch(
            exit: Exit,
            answer: impl Fn(&Value) -> Option<Vec<String>> + Send + Sync + 'static,
            spawner: impl FnOnce(Fake) -> Result<Spawner, String>,
        ) -> Self {
            Self::launch_in(scratch_root(), None, exit, answer, spawner)
        }

        /// A worker with `root` as its temp folder and `last_project` as the last project.
        fn launch_in(
            root: PathBuf,
            last_project: Option<PathBuf>,
            exit: Exit,
            answer: impl Fn(&Value) -> Option<Vec<String>> + Send + Sync + 'static,
            spawner: impl FnOnce(Fake) -> Result<Spawner, String>,
        ) -> Self {
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
            let worker = Worker::launch(
                host.clone(),
                spawner(fake),
                root.clone(),
                last_project,
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

        fn binds(&self) -> Vec<PathBuf> {
            lock(&self.host.binds).clone()
        }

        fn notices(&self) -> Vec<String> {
            lock(&self.host.notices).clone()
        }
    }

    /// A scratch folder that stands in for %TEMP%.
    fn scratch_root() -> PathBuf {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let root = std::env::temp_dir().join(format!(
            "mathimator-shell-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&root).unwrap();
        root
    }

    fn fake_spawner(fake: Fake) -> Result<Spawner, String> {
        Ok(Box::new(move |folder: &Path, init: bool| {
            fake.spawn(folder, init)
        }))
    }

    /// The project.json a fake worker writes for `init`.
    const EMPTY_PROJECT: &[u8] = b"{\"name\": \"Untitled\"}";

    /// Leaves in `folder` what a worker that binds it leaves: `.mathimator`, and with `init` an
    /// empty project. A folder that does not exist is left alone.
    fn bind(folder: &Path, init: bool) {
        if folder.is_dir() {
            fs::create_dir_all(folder.join(".mathimator")).unwrap();
            if init {
                fs::write(folder.join("project.json"), EMPTY_PROJECT).unwrap();
            }
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
        /// Like `WhenEnded`, but too late for the first end, which gives up at once.
        Late,
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
            bind(folder, init);
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
                    ended: false,
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
        /// Whether the shell has ended it before.
        ended: bool,
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
            if matches!(self.exit, Exit::WhenEnded | Exit::Late) {
                let (exited, history) = (Arc::clone(&self.exited), Arc::clone(&self.history));
                thread::spawn(move || {
                    thread::sleep(Duration::from_millis(100));
                    record_exit(&exited, &history);
                });
            }
            let gives_up = matches!(self.exit, Exit::Late) && !self.ended;
            self.ended = true;
            let exited = !gives_up && self.wait(limit);
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

    /// Protocol 2 to `status`, `state` to every RPC, and nothing to job commands, `cancel` and
    /// `set_tex_error`. `open_project` binds its folder first.
    fn answer(request: &Value) -> Option<Vec<String>> {
        Some(match cmd(request) {
            "status" => vec![ready_line(request)],
            "render_preview" | "export" | "cancel" | "set_tex_error" => vec![],
            "open_project" => {
                bind(
                    Path::new(request["path"].as_str().unwrap()),
                    request["init"] == true,
                );
                vec![state_line(request)]
            }
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

    /// Like `answer`, but the first worker is out of date: its `ready` has no protocol. The one a
    /// retry starts is not.
    fn out_of_date_first(request: &Value) -> Option<Vec<String>> {
        match cmd(request) {
            "status" if request["id"] == "status-1" => Some(vec![
                json!({"event": "ready", "jobId": request["id"]}).to_string(),
            ]),
            _ => answer(request),
        }
    }

    /// Writes the temp-project marker into `folder`, creating the folder (C8.5).
    fn mark_temp(folder: &Path) {
        fs::create_dir_all(folder.join(".mathimator")).unwrap();
        Marker::new("0".repeat(64), 1).write(folder).unwrap();
    }

    /// Whether `folder` has a marker whose seed is its project.json as it is now.
    fn is_marked_untouched(folder: &Path) -> bool {
        Marker::read(folder).is_some_and(|marker| !marker.is_touched(folder))
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

    /// A worker's stdin that closes at once, but whose drop returns only 50 ms later. The router
    /// sees the exit that follows well before the shell goes on.
    struct SlowClose(Option<Box<dyn Write + Send>>);

    impl Write for SlowClose {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.0.as_mut().unwrap().write(buf)
        }

        fn flush(&mut self) -> io::Result<()> {
            self.0.as_mut().unwrap().flush()
        }
    }

    impl Drop for SlowClose {
        fn drop(&mut self) {
            drop(self.0.take());
            thread::sleep(Duration::from_millis(50));
        }
    }

    #[test]
    fn a_stopped_worker_is_not_taken_for_a_crash_while_its_stdin_closes() {
        let test = Test::launch(Exit::WithStdout, out_of_date_first, |fake| {
            Ok(Box::new(move |folder: &Path, init: bool| {
                let mut spawned = fake.spawn(folder, init)?;
                spawned.stdin = Box::new(SlowClose(Some(spawned.stdin)));
                Ok(spawned)
            }))
        });
        assert!(block_on(test.worker.unavailable()).is_some());
        assert!(test.events().is_empty(), "{:?}", test.events());
        assert_eq!(
            test.history(),
            ["spawn", "in status", "out status-1", "exited", "end"]
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
        // The router and the failed handshake both end it, but only the first end reaches it.
        assert_eq!(test.history(), ["spawn", "in status", "exited", "end"]);
    }

    #[test]
    fn launch_marks_its_new_temp_project_before_commands_go_on() {
        let test = Test::start(answer);
        assert_eq!(block_on(test.worker.unavailable()), None);
        let folder = test.launch_folder();
        assert_eq!(lock(&test.spawns).as_slice(), [(folder.clone(), true)]);
        assert!(is_marked_untouched(&folder));
        assert_eq!(test.binds(), std::slice::from_ref(&folder));
        fs::write(folder.join("project.json"), b"{\"name\": \"Waves\"}").unwrap();
        assert!(!is_marked_untouched(&folder));
    }

    #[test]
    fn launch_reopens_the_last_project_without_init() {
        let test = Test::reopening("lesson", Some(b"{}"), answer);
        block_on(test.worker.get_state()).unwrap();
        let lesson = test.root.join("lesson");
        assert_eq!(lock(&test.spawns).as_slice(), [(lesson.clone(), false)]);
        assert_eq!(test.worker.project(), Some(lesson.clone()));
        assert_eq!(test.binds(), std::slice::from_ref(&lesson));
        assert_eq!(Marker::read(&lesson), None);
        assert!(test.notices().is_empty());
    }

    /// The notice for a last project that is missing at launch.
    fn missing_notice(folder: &Path) -> String {
        format!(
            "Could not reopen {}: The folder or its project.json is missing. A new project was started.",
            folder.display()
        )
    }

    #[test]
    fn launch_starts_a_new_project_with_a_notice_when_the_last_one_has_no_project_json() {
        let test = Test::reopening("lesson", None, answer);
        // The notice is there before the commands waiting for the launch go on.
        assert_eq!(block_on(test.worker.unavailable()), None);
        let lesson = test.root.join("lesson");
        assert_eq!(test.notices(), [missing_notice(&lesson)]);
        let spawns = lock(&test.spawns).clone();
        let [(folder, true)] = spawns.as_slice() else {
            panic!("{spawns:?}");
        };
        assert_eq!(folder.parent(), Some(test.root.as_path()));
        assert_ne!(folder, &lesson);
        assert!(is_marked_untouched(folder));
    }

    #[test]
    fn launch_starts_a_new_project_with_a_notice_when_the_last_folder_is_gone() {
        let root = scratch_root();
        let gone = root.join("lesson");
        let last = Some(gone.clone());
        let test = Test::launch_in(root, last, Exit::WithStdout, answer, fake_spawner);
        assert_eq!(block_on(test.worker.unavailable()), None);
        assert_eq!(test.notices(), [missing_notice(&gone)]);
        let spawns = lock(&test.spawns).clone();
        let [(folder, true)] = spawns.as_slice() else {
            panic!("{spawns:?}");
        };
        assert_eq!(folder.parent(), Some(test.root.as_path()));
        assert!(!gone.exists(), "the missing folder was made");
    }

    #[test]
    fn a_missing_last_project_is_told_once_a_retry_has_started_a_new_one() {
        let test = Test::reopening("lesson", None, out_of_date_first);
        assert!(block_on(test.worker.unavailable()).is_some());
        assert!(test.notices().is_empty(), "no new project was started");
        assert_eq!(block_on(test.worker.restart()).unwrap(), None);
        assert_eq!(test.notices(), [missing_notice(&test.root.join("lesson"))]);
    }

    #[test]
    fn a_last_project_the_worker_cannot_bind_gives_way_to_a_new_one_with_a_notice() {
        let test = Test::reopening("lesson", Some(b"{}"), |request| match cmd(request) {
            "status" => Some(vec![unbound_ready_line(request)]),
            _ => answer(request),
        });
        block_on(test.worker.get_state()).unwrap();
        let lesson = test.root.join("lesson");
        let opened = test.request(1);
        assert_eq!(
            opened,
            json!({"cmd": "open_project", "id": "rpc-2", "path": opened["path"], "init": true})
        );
        let folder = PathBuf::from(opened["path"].as_str().unwrap());
        assert_eq!(folder.parent(), Some(test.root.as_path()));
        assert_eq!(test.worker.project(), Some(folder.clone()));
        assert!(is_marked_untouched(&folder));
        assert!(
            lesson.join("project.json").is_file(),
            "the last project was changed"
        );
        assert_eq!(
            test.notices(),
            [format!(
                "Could not reopen {}: That folder is not a Mathimator project. A new project was started.",
                lesson.display()
            )]
        );
    }

    #[test]
    fn a_new_launch_folder_the_worker_cannot_bind_gives_way_without_a_notice() {
        let test = Test::start(|request| match cmd(request) {
            "status" => Some(vec![unbound_ready_line(request)]),
            _ => answer(request),
        });
        block_on(test.worker.get_state()).unwrap();
        let launched = test.launch_folder();
        let opened = test.request(1);
        assert_eq!(
            opened,
            json!({"cmd": "open_project", "id": "rpc-2", "path": opened["path"], "init": true})
        );
        let folder = PathBuf::from(opened["path"].as_str().unwrap());
        assert_ne!(folder, launched);
        assert!(folder.starts_with(&test.root) && folder.is_dir());
        assert_eq!(test.worker.project(), Some(folder));
        assert!(!launched.exists(), "the refused launch folder was kept");
        assert!(test.notices().is_empty());
        assert!(lock(&test.host.logs).contains(&format!(
            "The worker could not start a project in {}: That folder is not a Mathimator project.",
            launched.display()
        )));
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
        assert_eq!(test.worker.project(), Some(folder.clone()));
        assert!(is_marked_untouched(&folder));
        assert!(launched.is_dir(), "the folder the worker lost was deleted");
        let spawns = lock(&test.spawns).clone();
        assert_eq!(
            spawns,
            [(launched.clone(), true), (launched.clone(), false)]
        );
        assert_eq!(
            test.notices(),
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
        assert!(!test.launch_folder().exists(), "the launch folder was kept");
        assert_eq!(test.worker.project(), None);
    }

    #[test]
    fn a_worker_without_a_runtime_is_unavailable_and_makes_no_folder() {
        let reason = "The installed runtime is incomplete (python). Reinstall Mathimator.";
        let test = Test::without_runtime(reason);
        assert_eq!(block_on(test.worker.unavailable()), Some(reason.to_owned()));
        assert_eq!(
            block_on(test.worker.get_state()).unwrap_err(),
            ShellError::new(ErrorCode::WorkerUnavailable, reason)
        );
        assert_eq!(
            block_on(test.worker.restart()).unwrap(),
            Some(reason.to_owned())
        );
        assert_eq!(fs::read_dir(&test.root).unwrap().count(), 0);
        assert!(lock(&test.spawns).is_empty());
        assert!(lock(&test.host.logs).contains(&reason.to_owned()));
    }

    #[test]
    fn restart_retries_an_unavailable_worker_on_a_new_launch_folder() {
        let test = Test::start(out_of_date_first);
        let reason = block_on(test.worker.unavailable()).unwrap();
        assert!(reason.starts_with("The worker is out of date"), "{reason}");
        assert_eq!(block_on(test.worker.restart()).unwrap(), None);
        assert_eq!(block_on(test.worker.unavailable()), None);
        block_on(test.worker.get_state()).unwrap();
        let spawns = lock(&test.spawns).clone();
        let [(first, true), (second, true)] = spawns.as_slice() else {
            panic!("{spawns:?}");
        };
        assert_ne!(first, second);
        assert!(!first.exists() && second.is_dir());
        assert_eq!(test.worker.project().as_ref(), Some(second));
    }

    #[test]
    fn restart_leaves_a_ready_worker_alone() {
        let test = Test::start(answer);
        assert_eq!(block_on(test.worker.restart()).unwrap(), None);
        assert_eq!(lock(&test.spawns).len(), 1);
        assert_eq!(lock(&test.requests).len(), 1);
    }

    #[test]
    fn unavailable_waits_for_the_launch_but_not_for_a_request_in_flight() {
        // Each `status` and `get_state` answer waits for one release.
        let (release, held) = mpsc::channel::<()>();
        let held = Mutex::new(held);
        let test = Test::start(move |request| {
            if matches!(cmd(request), "status" | "get_state") {
                let _ = lock(&held).recv();
            }
            answer(request)
        });
        let read = |entry: &str| test.history().contains(&entry.to_owned()).then_some(());
        thread::scope(|scope| {
            let launching = scope.spawn(|| block_on(test.worker.unavailable()));
            wait_for(|| read("in status"));
            thread::sleep(Duration::from_millis(50));
            assert!(!launching.is_finished(), "it did not wait for the launch");
            release.send(()).unwrap();
            assert_eq!(launching.join().unwrap(), None);

            let request = scope.spawn(|| block_on(test.worker.get_state()));
            wait_for(|| read("in get_state"));
            assert_eq!(block_on(test.worker.unavailable()), None);
            assert!(!request.is_finished());
            release.send(()).unwrap();
            request.join().unwrap().unwrap();
        });
    }

    #[test]
    fn set_tex_error_does_nothing_without_a_running_worker() {
        let test = Test::without_runtime("No runtime.");
        block_on(test.worker.set_tex_error(Some(TEX_FAILED.to_owned()))).unwrap();
        assert!(lock(&test.requests).is_empty());
        assert!(test.events().is_empty());
    }

    /// A worker spawned through the shell's own spawner, and the TeX error each spawn saw.
    fn launch_with_tex(
        tex: &Arc<Tex>,
        answer: impl Fn(&Value) -> Option<Vec<String>> + Send + Sync + 'static,
    ) -> (Test, Arc<Mutex<Vec<Option<String>>>>) {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let (tex, recorded) = (Arc::clone(tex), Arc::clone(&seen));
        let test = Test::launch(Exit::WithStdout, answer, move |fake| {
            Ok(spawner(
                tex,
                move |folder: &Path, init: bool, tex_error: Option<&str>| {
                    lock(&recorded).push(tex_error.map(str::to_owned));
                    fake.spawn(folder, init)
                },
            ))
        });
        (test, seen)
    }

    #[test]
    fn a_failed_tex_check_tells_the_running_worker_and_stops_nothing() {
        let tex = Arc::new(Tex::new(TexStatus::at_launch(None)));
        let (test, seen) = launch_with_tex(&tex, answer);
        let export = test.worker.reserve_job_id("export").unwrap();
        block_on(test.worker.export_movie(export, false)).unwrap();
        block_on(tex_checked(
            &test.worker,
            &tex,
            &*test.host,
            Err(TEX_FAILED.to_owned()),
        ));
        block_on(test.worker.undo_document()).unwrap();
        let sent = lock(&test.requests).clone();
        let cmds: Vec<&str> = sent.iter().map(cmd).collect();
        assert_eq!(cmds, ["status", "export", "set_tex_error", "undo"]);
        assert_eq!(
            sent[2],
            json!({"cmd": "set_tex_error", "id": "tex-3", "message": TEX_FAILED})
        );
        // One worker, never ended: the export runs on, and the undo reaches its history.
        assert_eq!(lock(&seen).as_slice(), [None]);
        assert!(!test.history().contains(&"end".to_owned()));
        assert_eq!(
            test.events(),
            [
                json!({"event": "tex_status", "jobId": null, "state": "failed", "message": TEX_FAILED})
            ]
        );
    }

    #[test]
    fn a_passed_tex_check_clears_the_tex_error_of_the_running_worker() {
        let tex = Arc::new(Tex::new(TexStatus::at_launch(None)));
        let (test, seen) = launch_with_tex(&tex, answer);
        block_on(tex_checked(&test.worker, &tex, &*test.host, Ok(())));
        assert_eq!(
            test.request(1),
            json!({"cmd": "set_tex_error", "id": "tex-2", "message": null})
        );
        assert_eq!(lock(&seen).as_slice(), [None]);
        assert_eq!(
            test.events(),
            [json!({"event": "tex_status", "jobId": null, "state": "ok", "message": null})]
        );
    }

    #[test]
    fn a_worker_started_after_a_failed_tex_check_gets_the_tex_error_at_spawn() {
        let tex = Arc::new(Tex::new(TexStatus::at_launch(None)));
        let (test, seen) = launch_with_tex(&tex, crash_on_undo);
        block_on(test.worker.undo_document()).unwrap_err();
        block_on(tex_checked(
            &test.worker,
            &tex,
            &*test.host,
            Err(TEX_FAILED.to_owned()),
        ));
        block_on(test.worker.get_state()).unwrap();
        assert_eq!(lock(&seen).as_slice(), [None, Some(TEX_FAILED.to_owned())]);
        let sent = lock(&test.requests).clone();
        let cmds: Vec<&str> = sent.iter().map(cmd).collect();
        assert_eq!(cmds, ["status", "undo", "status", "get_state"]);
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
    fn crlf_terminated_lines_are_read_like_lf_terminated_ones() {
        // The worker writes text-mode stdout, so on Windows each line ends in CRLF (C2).
        let beat = json!({"event": "beat_ready", "jobId": "preview-3", "beatIndex": 0, "path": "C:/p/b0.mp4"});
        let line = beat.to_string();
        let test = Test::start(move |request| {
            let lines = match cmd(request) {
                "render_preview" => vec![line.clone()],
                _ => answer(request)?,
            };
            Some(lines.into_iter().map(|line| format!("{line}\r")).collect())
        });
        let reply = block_on(test.worker.get_state()).unwrap();
        assert_eq!(reply, json!({"event": "state", "jobId": "rpc-2"}));
        block_on(test.worker.render_preview("preview-3".to_owned())).unwrap();
        assert_eq!(wait_for(|| test.events().into_iter().next()), beat);
        let logs = lock(&test.host.logs);
        assert!(!logs.iter().any(|log| log.contains("not JSON")), "{logs:?}");
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
        let launched = test.launch_folder();
        assert_eq!(test.binds(), [launched.clone(), PathBuf::from("C:/a")]);
        block_on(test.worker.render_preview("preview-4".to_owned())).unwrap();
        let late = wait_for(|| test.events().into_iter().next());
        assert_eq!(late["jobId"], "rpc-99");
        assert_eq!(
            test.binds(),
            [launched, PathBuf::from("C:/a"), PathBuf::from("C:/b")]
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
            spawn: Err("no spawn".to_owned()),
            temp_root: root.clone(),
            reopen: Mutex::default(),
            stderr_log: root,
            next_id: AtomicU64::new(1),
            next_generation: AtomicU64::new(1),
            routes: Mutex::default(),
            bound: Mutex::default(),
            unavailable: Mutex::default(),
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
        let ends = history.iter().filter(|entry| *entry == "end").count();
        assert_eq!(ends, 1, "{history:?}");
        assert_eq!(test.request(3), json!({"cmd": "status", "id": "status-4"}));
    }

    #[test]
    fn a_process_is_ended_again_only_if_it_outlived_its_last_end() {
        let history = Arc::default();
        let process = Process::new(Box::new(FakeProcess {
            exit: Exit::Late,
            exited: Arc::default(),
            ended: false,
            history: Arc::clone(&history),
        }));
        assert!(!process.end(), "the first end waited for the late exit");
        assert!(process.end());
        assert!(process.end());
        assert_eq!(lock(&history).as_slice(), ["end", "exited", "end"]);
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
                "The previous worker did not stop. Choose Retry to try again, or restart Mathimator."
            )
        );
        assert_eq!(
            block_on(test.worker.restart()).unwrap(),
            Some(error.message)
        );
        assert_eq!(lock(&test.spawns).len(), 1, "a second worker was started");
    }

    #[test]
    fn restart_starts_a_worker_once_the_one_that_did_not_stop_has_exited() {
        // The first worker is out of date, and still runs when the shell gives up ending it.
        let test = Test::start_with_exit(Exit::Late, out_of_date_first);
        assert!(block_on(test.worker.unavailable()).is_some());
        assert!(lock(&test.host.logs).contains(&STUCK.to_owned()));
        assert_eq!(block_on(test.worker.restart()).unwrap(), None);
        assert_eq!(block_on(test.worker.unavailable()), None);
        let history = test.history();
        let respawn = history.iter().rposition(|entry| entry == "spawn").unwrap();
        assert!(
            history[..respawn].contains(&"exited".to_owned()),
            "the new worker started before the old one exited: {history:?}"
        );
        assert_eq!(lock(&test.spawns).len(), 2);
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
    fn only_previews_and_exports_reserve_ids_from_the_one_counter() {
        let test = Test::start(answer);
        block_on(test.worker.get_state()).unwrap();
        assert_eq!(test.worker.reserve_job_id("preview").unwrap(), "preview-3");
        assert_eq!(test.worker.reserve_job_id("export").unwrap(), "export-4");
        let refused = test.worker.reserve_job_id("rpc").unwrap_err();
        assert_eq!(refused.code, ErrorCode::BadArgument);
        assert_eq!(test.worker.reserve_job_id("preview").unwrap(), "preview-5");
    }

    #[cfg(windows)]
    #[test]
    fn a_folder_that_is_not_unicode_is_a_bad_argument_and_sends_nothing() {
        use std::os::windows::ffi::OsStringExt;

        let test = Test::start(answer);
        let folder = test.root.join(std::ffi::OsString::from_wide(&[0xD800]));
        let opened = block_on(test.worker.open_project(&folder)).unwrap_err();
        let saved = block_on(test.worker.save_project_as(&folder)).unwrap_err();
        assert_eq!(
            [opened.code, saved.code],
            [ErrorCode::BadArgument, ErrorCode::BadArgument]
        );
        block_on(test.worker.get_state()).unwrap();
        assert_eq!(test.request(1)["cmd"], "get_state");
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
        assert_eq!(Marker::read(&folder), None);
    }

    #[test]
    fn new_project_marks_its_folder_with_the_project_the_worker_wrote() {
        let test = Test::start(answer);
        let reply = block_on(test.worker.new_project()).unwrap();
        let folder = PathBuf::from(reply["projectPath"].as_str().unwrap());
        assert_eq!(
            fs::read(folder.join("project.json")).unwrap(),
            EMPTY_PROJECT
        );
        assert!(is_marked_untouched(&folder));
    }

    #[test]
    fn open_sample_marks_its_copy_with_the_sample_as_the_seed() {
        let test = Test::start(answer);
        let sample = test.root.join("sample.json");
        fs::write(&sample, b"{\"name\": \"sample\"}").unwrap();
        let reply = block_on(test.worker.open_sample(sample)).unwrap();
        let folder = PathBuf::from(reply["projectPath"].as_str().unwrap());
        assert!(is_marked_untouched(&folder));
    }

    #[test]
    fn open_sample_keeps_its_folder_unmarked_when_the_worker_stops() {
        let test = Test::start(|request| {
            if cmd(request) == "open_project" {
                None
            } else {
                answer(request)
            }
        });
        let sample = test.root.join("sample.json");
        fs::write(&sample, b"{}").unwrap();
        let error = block_on(test.worker.open_sample(sample)).unwrap_err();
        assert_eq!(error.code, ErrorCode::WorkerExited);
        let folder = PathBuf::from(test.request(1)["path"].as_str().unwrap());
        assert!(folder.join("project.json").is_file());
        assert_eq!(Marker::read(&folder), None);
    }

    #[test]
    fn only_a_touched_temp_project_asks_before_it_is_left() {
        let test = Test::start(answer);
        block_on(test.worker.unavailable());
        assert!(!block_on(test.worker.is_touched_temp_project()));
        let folder = test.launch_folder();
        fs::write(folder.join("project.json"), b"{\"name\": \"Waves\"}").unwrap();
        assert!(block_on(test.worker.is_touched_temp_project()));
        // A project the user opened is never a temp project, whatever it holds.
        let lesson = test.root.join("lesson");
        fs::create_dir(&lesson).unwrap();
        block_on(test.worker.open_project(&lesson)).unwrap();
        assert!(!block_on(test.worker.is_touched_temp_project()));
    }

    #[test]
    fn an_unmarked_temp_folder_asks_before_it_is_left() {
        // A temp folder bound by a late reply, which the shell never marked. Pruning may delete it
        // whatever its project.json holds.
        let test = Test::start(answer);
        let late = test.root.join("mathimator-1-2");
        fs::create_dir(&late).unwrap();
        block_on(test.worker.open_project(&late)).unwrap();
        assert!(block_on(test.worker.is_touched_temp_project()));
        // A folder with that name elsewhere is one the user keeps.
        let kept = test.root.join("lessons").join("mathimator-1-2");
        fs::create_dir_all(&kept).unwrap();
        block_on(test.worker.open_project(&kept)).unwrap();
        assert!(!block_on(test.worker.is_touched_temp_project()));
    }

    #[test]
    fn an_unavailable_worker_never_asks_before_its_project_is_left() {
        // The worker crashes and does not exit, which leaves it unavailable.
        let test = Test::start_with_exit(Exit::Never, crash_on_undo);
        block_on(test.worker.unavailable());
        fs::write(test.launch_folder().join("project.json"), b"{}").unwrap();
        assert!(block_on(test.worker.is_touched_temp_project()));
        block_on(test.worker.undo_document()).unwrap_err();
        block_on(test.worker.get_state()).unwrap_err();
        assert!(block_on(test.worker.unavailable()).is_some());
        assert!(!block_on(test.worker.is_touched_temp_project()));
    }

    #[test]
    fn the_touched_check_sees_the_edit_in_flight() {
        // The worker saves an edit a moment after it has read it.
        let launched = Arc::new(OnceLock::<PathBuf>::new());
        let folder = Arc::clone(&launched);
        let test = Test::start(move |request| {
            if cmd(request) == "edit" {
                thread::sleep(Duration::from_millis(200));
                let project = folder.get().unwrap().join("project.json");
                fs::write(project, b"{\"name\": \"Waves\"}").unwrap();
            }
            answer(request)
        });
        launched.set(test.launch_folder()).unwrap();
        thread::scope(|scope| {
            scope.spawn(|| {
                let edit = json!({"op": "set_project_name", "name": "Waves"});
                block_on(test.worker.edit_document(edit)).unwrap()
            });
            wait_for(|| test.history().contains(&"in edit".to_owned()).then_some(()));
            assert!(block_on(test.worker.is_touched_temp_project()));
        });
    }

    /// Makes `folder` an untouched temp project created long ago, which pruning may delete.
    fn mark_old(folder: &Path) {
        fs::create_dir_all(folder.join(".mathimator")).unwrap();
        fs::write(folder.join("project.json"), EMPTY_PROJECT).unwrap();
        let seed = temp_project::seed_of(folder).unwrap();
        Marker::new(seed, 1).write(folder).unwrap();
    }

    #[test]
    fn pruning_keeps_the_bound_project() {
        let test = Test::start(answer);
        block_on(test.worker.unavailable());
        let bound = test.launch_folder();
        mark_old(&bound);
        let stale = test.root.join("mathimator-1-1");
        mark_old(&stale);
        block_on(test.worker.prune_temp_projects());
        assert!(bound.is_dir());
        assert!(!stale.exists());
        let pruned = format!("Pruned the temp project {}.", stale.display());
        assert!(lock(&test.host.logs).contains(&pruned));
    }

    #[test]
    fn pruning_keeps_a_last_project_no_worker_has_reopened() {
        let root = scratch_root();
        let last = root.join("mathimator-1-1");
        mark_old(&last);
        let stale = root.join("mathimator-1-2");
        mark_old(&stale);
        let test = Test::launch_in(root, Some(last.clone()), Exit::WithStdout, answer, |_| {
            Err("No runtime.".to_owned())
        });
        block_on(test.worker.prune_temp_projects());
        assert!(last.is_dir());
        assert!(!stale.exists());
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
        let target = test.root.join("saved");
        fs::create_dir(&target).unwrap();
        let reply = block_on(test.worker.save_project_as(&target)).unwrap();
        assert_eq!(reply["projectPath"], target.to_str().unwrap());
        assert!(!old.exists());
        assert_eq!(test.worker.project(), Some(target));
    }

    #[test]
    fn a_refused_save_as_keeps_the_temp_project_and_its_binding() {
        for code in ["export_running", "save_as_failed"] {
            let test = Test::start(move |request| match cmd(request) {
                "save_as" => Some(vec![rejected_line(request, code)]),
                _ => answer(request),
            });
            let old = test.launch_folder();
            let target = test.root.join("saved");
            fs::create_dir(&target).unwrap();
            let reply = block_on(test.worker.save_project_as(&target)).unwrap();
            assert_eq!(
                reply,
                json!({"event": "rejected", "jobId": "rpc-2", "code": code, "message": "Refused."})
            );
            assert!(is_marked_untouched(&old), "{code}");
            assert_eq!(test.worker.project(), Some(old));
        }
    }

    #[test]
    fn save_as_keeps_an_unmarked_old_folder() {
        // A temp folder bound by a late reply, which the shell never marked.
        let test = Test::start(answer);
        let old = test.root.join("mathimator-1-2");
        fs::create_dir(&old).unwrap();
        block_on(test.worker.open_project(&old)).unwrap();
        let target = test.root.join("saved");
        fs::create_dir(&target).unwrap();
        block_on(test.worker.save_project_as(&target)).unwrap();
        assert!(old.is_dir());
    }

    #[test]
    fn save_as_into_a_folder_inside_the_temp_project_keeps_both() {
        let test = Test::start(answer);
        let old = test.launch_folder();
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
}
