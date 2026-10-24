use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ExitStatus, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::runtime::{hidden_command, Runtime};

/// Typst glyph preview. The shell owns this process. It is not the supervisor.
pub struct GlyphPreview {
    cancelled: AtomicU64,
    running: AtomicU64,
    child: Mutex<Option<Child>>,
    binary: Mutex<Option<PathBuf>>,
    /// The Typst the runtime resolved, or why there is none (C9.6).
    typst: Result<PathBuf, String>,
}

impl GlyphPreview {
    pub fn new(typst: Result<PathBuf, String>) -> Self {
        Self {
            cancelled: AtomicU64::new(0),
            running: AtomicU64::new(0),
            child: Mutex::new(None),
            binary: Mutex::new(None),
            typst,
        }
    }

    pub fn cancel(&self, generation: u64) {
        self.cancelled.fetch_max(generation, Ordering::SeqCst);
        if self.running.load(Ordering::SeqCst) == generation {
            self.kill_child();
        }
    }

    pub fn compile(
        &self,
        runtime: &Runtime,
        project: &Path,
        latex: &str,
        generation: u64,
    ) -> Result<Value, String> {
        if !self.claim(generation) {
            return Ok(cancelled());
        }
        self.kill_child();
        if self.stale(generation) {
            return Ok(cancelled());
        }

        let translated = match translate(runtime, latex) {
            Ok(translated) => translated,
            Err(message) => return Ok(failed(message)),
        };
        if self.stale(generation) {
            return Ok(cancelled());
        }
        let Some(document) = translated else {
            return Ok(json!({"status": "unsupported"}));
        };

        let directory = project.join(".mathimator").join("equation");
        fs::create_dir_all(&directory).map_err(|err| err.to_string())?;
        let typ_path = directory.join(format!("formula-{generation}.typ"));
        let svg_path = directory.join(format!("formula-{generation}.svg"));
        fs::write(&typ_path, document).map_err(|err| err.to_string())?;

        let binary = match self.typst_binary() {
            Ok(binary) => binary,
            Err(message) => return Ok(failed(message)),
        };
        if self.stale(generation) {
            return Ok(cancelled());
        }

        let mut child = spawn_typst(&binary, &typ_path, &svg_path)?;
        let stdout_pipe = child.stdout.take();
        let stderr_pipe = child.stderr.take();
        let stdout_thread = stdout_pipe.map(|pipe| thread::spawn(move || read_pipe(pipe)));
        let stderr_thread = stderr_pipe.map(|pipe| thread::spawn(move || read_pipe(pipe)));

        {
            let mut slot = self.child.lock().map_err(|err| err.to_string())?;
            if self.stale(generation) {
                drop(slot);
                let _ = child.kill();
                let _ = child.wait();
                join_pipe(stdout_thread);
                join_pipe(stderr_thread);
                return Ok(cancelled());
            }
            *slot = Some(child);
        }

        let started = Instant::now();
        let status = loop {
            let polled = {
                let mut slot = self.child.lock().map_err(|err| err.to_string())?;
                let waited = slot.as_mut().map(|child| child.try_wait());
                match waited {
                    None => Poll::Missing,
                    Some(Ok(Some(status))) => {
                        let _ = slot.take();
                        Poll::Finished(status)
                    }
                    Some(Ok(None)) => Poll::Running,
                    Some(Err(err)) => {
                        let _ = slot.take();
                        Poll::Failed(err.to_string())
                    }
                }
            };
            match polled {
                Poll::Finished(status) => break status,
                Poll::Missing => {
                    join_pipe(stdout_thread);
                    join_pipe(stderr_thread);
                    return Ok(cancelled());
                }
                Poll::Failed(message) => {
                    join_pipe(stdout_thread);
                    join_pipe(stderr_thread);
                    return Ok(failed(message));
                }
                Poll::Running => {}
            }
            if started.elapsed() > Duration::from_secs(10) {
                if !self.stale(generation) {
                    self.kill_child();
                    join_pipe(stdout_thread);
                    join_pipe(stderr_thread);
                    return Ok(failed("typst compile timed out".to_string()));
                }
                join_pipe(stdout_thread);
                join_pipe(stderr_thread);
                return Ok(cancelled());
            }
            thread::sleep(Duration::from_millis(5));
        };

        let stdout = join_pipe(stdout_thread);
        let stderr = join_pipe(stderr_thread);
        if self.stale(generation) {
            return Ok(cancelled());
        }
        if !status.success() {
            return Ok(failed(typst_failure(&status, &stderr, &stdout)));
        }
        let metadata = fs::metadata(&svg_path).map_err(|err| err.to_string())?;
        if !metadata.is_file() || metadata.len() == 0 {
            return Ok(failed("typst did not write an SVG".to_string()));
        }
        Ok(json!({
            "status": "ok",
            "svgPath": svg_path,
        }))
    }

    fn claim(&self, generation: u64) -> bool {
        if generation <= self.cancelled.load(Ordering::SeqCst) {
            return false;
        }
        let current = self.running.load(Ordering::SeqCst);
        if generation < current {
            return false;
        }
        self.running.store(generation, Ordering::SeqCst);
        self.running.load(Ordering::SeqCst) == generation
            && generation > self.cancelled.load(Ordering::SeqCst)
    }

    fn stale(&self, generation: u64) -> bool {
        generation <= self.cancelled.load(Ordering::SeqCst)
            || self.running.load(Ordering::SeqCst) != generation
    }

    fn kill_child(&self) {
        let child = self.child.lock().ok().and_then(|mut slot| slot.take());
        if let Some(mut child) = child {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    fn typst_binary(&self) -> Result<PathBuf, String> {
        if let Some(path) = self.binary.lock().map_err(|err| err.to_string())?.clone() {
            return Ok(path);
        }
        let path = self.typst.clone()?;
        let output = hidden_command(&path)
            .arg("--version")
            .output()
            .map_err(|err| format!("failed to run typst --version: {err}"))?;
        if !output.status.success() {
            return Err(format!(
                "typst --version failed: {}",
                clip(&String::from_utf8_lossy(&output.stderr), 400)
            ));
        }
        let text = String::from_utf8_lossy(&output.stdout);
        let line = text.lines().next().unwrap_or("").trim();
        if !line.starts_with("typst 0.15.0") {
            return Err(format!("Typst 0.15.0 is required, found {line}"));
        }
        if let Ok(mut slot) = self.binary.lock() {
            *slot = Some(path.clone());
        }
        Ok(path)
    }
}

enum Poll {
    Running,
    Finished(ExitStatus),
    Missing,
    Failed(String),
}

fn cancelled() -> Value {
    json!({"status": "cancelled"})
}

fn failed(message: String) -> Value {
    json!({"status": "error", "message": message})
}

fn translate(runtime: &Runtime, latex: &str) -> Result<Option<String>, String> {
    let files = runtime.worker.as_ref().map_err(String::clone)?;
    let mut command = runtime.python_command(files, "mathimator_worker.equation");
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|err| format!("failed to start the equation translator: {err}"))?;
    let request = serde_json::to_string(&json!({"latex": latex})).map_err(|err| err.to_string())?;
    {
        let mut stdin = child
            .stdin
            .take()
            .ok_or_else(|| "translator stdin was not piped".to_string())?;
        stdin
            .write_all(request.as_bytes())
            .map_err(|err| err.to_string())?;
    }
    let output = child
        .wait_with_output()
        .map_err(|err| format!("equation translator failed: {err}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "equation translator failed: {}",
            clip(stderr.trim(), 800)
        ));
    }
    let payload: Value = serde_json::from_slice(&output.stdout)
        .map_err(|err| format!("equation translator returned non-json: {err}"))?;
    if payload.get("supported").and_then(Value::as_bool) != Some(true) {
        return Ok(None);
    }
    let document = payload
        .get("document")
        .and_then(Value::as_str)
        .ok_or_else(|| "equation translator omitted the Typst document".to_string())?;
    Ok(Some(document.to_string()))
}

fn spawn_typst(binary: &Path, source: &Path, svg: &Path) -> Result<Child, String> {
    hidden_command(binary)
        .arg("compile")
        .arg(source)
        .arg(svg)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("failed to start typst: {err}"))
}

fn read_pipe<R: Read>(mut pipe: R) -> String {
    let mut buffer = Vec::new();
    let _ = pipe.read_to_end(&mut buffer);
    String::from_utf8_lossy(&buffer).into_owned()
}

fn join_pipe(handle: Option<thread::JoinHandle<String>>) -> String {
    handle
        .and_then(|handle| handle.join().ok())
        .unwrap_or_default()
}

fn typst_failure(status: &ExitStatus, stderr: &str, stdout: &str) -> String {
    let text = if stderr.trim().is_empty() {
        stdout.trim()
    } else {
        stderr.trim()
    };
    let clipped = clip(text, 800);
    if clipped.is_empty() {
        format!("typst exited with {status}")
    } else {
        clipped
    }
}

fn clip(text: &str, limit: usize) -> String {
    let mut clipped = String::new();
    for character in text.chars() {
        if clipped.len() + character.len_utf8() > limit {
            clipped.push('…');
            break;
        }
        clipped.push(character);
    }
    clipped
}
