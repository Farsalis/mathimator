//! Windows Job Object teardown for the supervisor process.
//!
//! `assign_supervisor` runs on Windows unless a debug build has
//! `MATHIMATOR_WIN32_JOBS=0`; release builds read no such override (C9.6).
//! Dropping the returned job kills the supervisor and every process it created,
//! because the job has `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.

use std::io;
use std::process::Child;

/// The supervisor's Job Object. Dropping it closes the handle, which ends the tree.
pub struct SupervisorJob {
    #[cfg(windows)]
    _job: std::os::windows::io::OwnedHandle,
}

/// Puts the supervisor in a kill-on-close Job Object. `Ok(None)` means Job
/// Objects are off; an error carries the OS reason, for shell.log (F104).
pub fn assign_supervisor(child: &Child) -> io::Result<Option<SupervisorJob>> {
    if cfg!(debug_assertions) && std::env::var("MATHIMATOR_WIN32_JOBS").ok().as_deref() == Some("0")
    {
        return Ok(None);
    }
    assign_enabled(child)
}

#[cfg(not(windows))]
fn assign_enabled(_child: &Child) -> io::Result<Option<SupervisorJob>> {
    Ok(None)
}

#[cfg(windows)]
fn assign_enabled(child: &Child) -> io::Result<Option<SupervisorJob>> {
    use std::os::windows::io::{AsRawHandle, HandleOrNull, OwnedHandle};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    // SAFETY: CreateJobObjectW returns a new handle that nothing else owns, or null.
    let job = unsafe {
        HandleOrNull::from_raw_handle(CreateJobObjectW(std::ptr::null(), std::ptr::null()))
    };
    let job = OwnedHandle::try_from(job).map_err(|_| os_error("CreateJobObjectW"))?;
    // From here on, each error is read before `job` drops and closes the handle.
    let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    // SAFETY: `info` is the structure this class takes, and the length is its size.
    let set = unsafe {
        SetInformationJobObject(
            job.as_raw_handle(),
            JobObjectExtendedLimitInformation,
            (&raw const info).cast(),
            std::mem::size_of_val(&info) as u32,
        )
    };
    if set == 0 {
        return Err(os_error("SetInformationJobObject"));
    }
    // SAFETY: both handles stay open for the call; `child` owns the process handle.
    if unsafe { AssignProcessToJobObject(job.as_raw_handle(), child.as_raw_handle()) } == 0 {
        return Err(os_error("AssignProcessToJobObject"));
    }
    Ok(Some(SupervisorJob { _job: job }))
}

/// The last OS error, tagged with the Win32 call that failed (F101).
#[cfg(windows)]
fn os_error(api: &str) -> io::Error {
    let err = io::Error::last_os_error();
    io::Error::new(err.kind(), format!("{api} failed: {err}"))
}

/// The reap test creates a Job Object and real processes, so it runs only with
/// `--include-ignored` and `MATHIMATOR_WIN32_TESTS=1`.
#[cfg(all(test, windows))]
mod tests {
    use std::os::windows::io::{AsRawHandle, HandleOrNull, OwnedHandle};
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
    use std::{fs, thread};

    use windows_sys::Win32::Foundation::{SetLastError, ERROR_ACCESS_DENIED, STILL_ACTIVE};
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    use super::*;

    /// The C# stand-ins the Python reap tests compile too (D53).
    const JOB_TREE: &str = include_str!("../../../tests/fixtures/win32/job_tree.cs");
    const SLEEPER: &str = include_str!("../../../tests/fixtures/win32/sleeper.cs");

    #[test]
    fn a_failed_call_names_the_api_and_keeps_the_os_reason() {
        // SAFETY: SetLastError only sets this thread's last-error code.
        unsafe { SetLastError(ERROR_ACCESS_DENIED) };
        let err = os_error("AssignProcessToJobObject");
        assert_eq!(err.kind(), io::ErrorKind::PermissionDenied);
        let message = err.to_string();
        assert!(
            message.starts_with("AssignProcessToJobObject failed: ")
                && message.ends_with("(os error 5)"),
            "{message}"
        );
    }

    #[test]
    #[ignore = "creates a Job Object and processes; needs MATHIMATOR_WIN32_TESTS=1"]
    fn supervisor_job_reaps_manim_latex_and_dvisvgm_within_one_second() {
        assert_eq!(
            std::env::var("MATHIMATOR_WIN32_TESTS").ok().as_deref(),
            Some("1"),
            "refusing to create a Job Object without MATHIMATOR_WIN32_TESTS=1",
        );
        let root = std::env::temp_dir().join(format!(
            "mathimator-job-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|value| value.as_millis())
                .unwrap_or(0)
        ));
        fs::create_dir_all(&root).expect("temp dir");
        let manim = compile_stand_in(&root, "manim", JOB_TREE);
        let latex = compile_stand_in(&root, "latex", SLEEPER);
        let dvisvgm = compile_stand_in(&root, "dvisvgm", SLEEPER);
        let go = root.join("go");
        let pids = root.join("pids.txt");
        let mut child = Command::new(&manim)
            .env("MATHIMATOR_TEST_GO", &go)
            .env("MATHIMATOR_TEST_PIDS", &pids)
            .env("MATHIMATOR_TEST_LATEX", &latex)
            .env("MATHIMATOR_TEST_DVISVGM", &dvisvgm)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn manim stand-in");
        // Called directly, so a MATHIMATOR_WIN32_JOBS=0 in the environment cannot skip it.
        let job = assign_enabled(&child)
            .expect("the Job Object could not be set up")
            .expect("Job Objects are off");
        fs::write(&go, b"go").expect("go file");
        let (latex_pid, dvisvgm_pid) = wait_child_pids(&pids);
        assert!(
            process_alive(child.id()),
            "manim stand-in exited before the job closed"
        );
        assert!(process_alive(latex_pid), "latex was not alive");
        assert!(process_alive(dvisvgm_pid), "dvisvgm was not alive");
        let started = Instant::now();
        drop(job);
        let limit = Duration::from_secs(1);
        loop {
            let parent_done = child.try_wait().expect("wait").is_some();
            let children_done = !process_alive(latex_pid) && !process_alive(dvisvgm_pid);
            if parent_done && children_done {
                assert!(
                    started.elapsed() <= limit,
                    "reap took {:?}",
                    started.elapsed()
                );
                let _ = fs::remove_dir_all(&root);
                return;
            }
            if started.elapsed() > limit {
                let _ = child.kill();
                let _ = child.wait();
                panic!("manim, latex, or dvisvgm still alive after 1s");
            }
            thread::sleep(Duration::from_millis(20));
        }
    }

    fn process_alive(pid: u32) -> bool {
        // SAFETY: OpenProcess returns a new handle that nothing else owns, or null.
        let process = unsafe {
            HandleOrNull::from_raw_handle(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid))
        };
        let Ok(process) = OwnedHandle::try_from(process) else {
            return false;
        };
        let mut code = 0;
        // SAFETY: `process` stays open for the call, and `code` is a u32 to write.
        let read = unsafe { GetExitCodeProcess(process.as_raw_handle(), &mut code) };
        read != 0 && code == STILL_ACTIVE as u32
    }

    fn wait_child_pids(path: &Path) -> (u32, u32) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if let Ok(text) = fs::read_to_string(path) {
                let lines: Vec<&str> = text.lines().filter(|line| !line.is_empty()).collect();
                if lines.len() >= 2 {
                    let latex = lines[0].trim().parse().expect("latex pid");
                    let dvisvgm = lines[1].trim().parse().expect("dvisvgm pid");
                    return (latex, dvisvgm);
                }
            }
            thread::sleep(Duration::from_millis(20));
        }
        panic!("child pids were not written");
    }

    /// Compiles `source` to `<root>/<name>.exe` with the .NET Framework's csc.
    fn compile_stand_in(root: &Path, name: &str, source: &str) -> PathBuf {
        let source_path = root.join(format!("{name}.cs"));
        let exe = root.join(format!("{name}.exe"));
        fs::write(&source_path, source).expect("source");
        let windir = std::env::var("WINDIR").unwrap_or_else(|_| r"C:\Windows".to_string());
        let compiler = PathBuf::from(windir)
            .join("Microsoft.NET")
            .join("Framework64")
            .join("v4.0.30319")
            .join("csc.exe");
        let completed = Command::new(compiler)
            .arg("/nologo")
            .arg("/t:exe")
            .arg(format!("/out:{}", exe.display()))
            .arg(&source_path)
            .output()
            .expect("csc");
        if !completed.status.success() || !exe.is_file() {
            panic!(
                "csc failed: {}{}",
                String::from_utf8_lossy(&completed.stdout),
                String::from_utf8_lossy(&completed.stderr)
            );
        }
        exe
    }
}
