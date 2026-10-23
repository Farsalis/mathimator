//! Windows Job Object teardown for the supervisor process.
//!
//! `assign_supervisor` runs on Windows unless `MATHIMATOR_WIN32_JOBS=0`.
//! Closing the returned handle kills the supervisor and every process it
//! created, because the job has `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The test
//! below is ignored until Craig approves executing it on this machine.

use std::process::Child;

pub struct SupervisorJob {
    #[cfg(windows)]
    handle: isize,
}

impl Drop for SupervisorJob {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            CloseHandle(self.handle as *mut std::ffi::c_void);
        }
    }
}

pub fn assign_supervisor(child: &Child) -> Option<SupervisorJob> {
    if std::env::var("MATHIMATOR_WIN32_JOBS").ok().as_deref() == Some("0") {
        return None;
    }
    assign_enabled(child)
}

#[cfg(not(windows))]
fn assign_enabled(_child: &Child) -> Option<SupervisorJob> {
    let _ = _child;
    None
}

#[cfg(windows)]
fn assign_enabled(child: &Child) -> Option<SupervisorJob> {
    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x2000;
    const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: i32 = 9;
    const PROCESS_TERMINATE: u32 = 0x0001;
    const PROCESS_SET_QUOTA: u32 = 0x0100;
    unsafe {
        let job = CreateJobObjectW(std::ptr::null_mut(), std::ptr::null());
        if job.is_null() {
            return None;
        }
        let mut info: JobObjectExtendedLimitInformation = std::mem::zeroed();
        info.basic.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set = SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            &mut info as *mut JobObjectExtendedLimitInformation as *mut std::ffi::c_void,
            std::mem::size_of::<JobObjectExtendedLimitInformation>() as u32,
        );
        if set == 0 {
            CloseHandle(job);
            return None;
        }
        let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, child.id());
        if process.is_null() {
            CloseHandle(job);
            return None;
        }
        let assigned = AssignProcessToJobObject(job, process);
        CloseHandle(process);
        if assigned == 0 {
            CloseHandle(job);
            return None;
        }
        Some(SupervisorJob {
            handle: job as isize,
        })
    }
}

#[cfg(windows)]
#[repr(C)]
struct BasicLimitInformation {
    per_process_user_time: i64,
    per_job_user_time: i64,
    limit_flags: u32,
    _pad0: u32,
    minimum_working_set_size: usize,
    maximum_working_set_size: usize,
    active_process_limit: u32,
    _pad1: u32,
    affinity: usize,
    priority_class: u32,
    scheduling_class: u32,
}

#[cfg(windows)]
#[repr(C)]
struct IoCounters {
    read_operation_count: u64,
    write_operation_count: u64,
    other_operation_count: u64,
    read_transfer_count: u64,
    write_transfer_count: u64,
    other_transfer_count: u64,
}

#[cfg(windows)]
#[repr(C)]
struct JobObjectExtendedLimitInformation {
    basic: BasicLimitInformation,
    io: IoCounters,
    process_memory_limit: usize,
    job_memory_limit: usize,
    peak_process_memory_used: usize,
    peak_job_memory_used: usize,
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn CreateJobObjectW(attrs: *mut std::ffi::c_void, name: *const u16) -> *mut std::ffi::c_void;
    fn SetInformationJobObject(
        job: *mut std::ffi::c_void,
        class: i32,
        info: *mut std::ffi::c_void,
        length: u32,
    ) -> i32;
    fn AssignProcessToJobObject(job: *mut std::ffi::c_void, process: *mut std::ffi::c_void) -> i32;
    fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut std::ffi::c_void;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
}

#[cfg(all(windows, test))]
#[link(name = "kernel32")]
extern "system" {
    fn GetExitCodeProcess(process: *mut std::ffi::c_void, code: *mut u32) -> i32;
}

#[test]
#[ignore = "Win32 job-object test is not executed until Craig approves running it on this machine"]
fn supervisor_job_reaps_manim_latex_and_dvisvgm_within_one_second() {
    assert_eq!(
        std::env::var("MATHIMATOR_WIN32_TESTS").ok().as_deref(),
        Some("1"),
        "refusing to create a Job Object without MATHIMATOR_WIN32_TESTS=1",
    );
    #[cfg(windows)]
    reap_assigned_tree_within_one_second();
    #[cfg(not(windows))]
    panic!("the supervisor job is a Windows test");
}

#[cfg(all(windows, test))]
fn reap_assigned_tree_within_one_second() {
    // SAFETY: this ignored test is the only writer, and cargo test does not
    // run it unless --ignored is passed together with MATHIMATOR_WIN32_TESTS=1.
    unsafe {
        std::env::set_var("MATHIMATOR_WIN32_JOBS", "1");
    }
    let root = std::env::temp_dir().join(format!(
        "mathimator-job-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_millis())
            .unwrap_or(0)
    ));
    std::fs::create_dir_all(&root).expect("temp dir");
    let manim = compile_sleeper(&root, "manim.exe", tree_parent_source());
    let latex = compile_sleeper(&root, "latex.exe", sleeper_source());
    let dvisvgm = compile_sleeper(&root, "dvisvgm.exe", sleeper_source());
    let go = root.join("go");
    let pids = root.join("pids.txt");
    let mut child = std::process::Command::new(&manim)
        .env("MATHIMATOR_TEST_GO", &go)
        .env("MATHIMATOR_TEST_PIDS", &pids)
        .env("MATHIMATOR_TEST_LATEX", &latex)
        .env("MATHIMATOR_TEST_DVISVGM", &dvisvgm)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .expect("spawn manim stand-in");
    let job = assign_supervisor(&child).expect("supervisor was not assigned to a job");
    std::fs::write(&go, b"go").expect("go file");
    let (latex_pid, dvisvgm_pid) = wait_child_pids(&pids);
    assert!(
        process_alive(child.id()),
        "manim stand-in exited before the job closed"
    );
    assert!(process_alive(latex_pid), "latex was not alive");
    assert!(process_alive(dvisvgm_pid), "dvisvgm was not alive");
    let started = std::time::Instant::now();
    drop(job);
    let limit = std::time::Duration::from_secs(1);
    loop {
        let parent_done = child.try_wait().expect("wait").is_some();
        let children_done = !process_alive(latex_pid) && !process_alive(dvisvgm_pid);
        if parent_done && children_done {
            assert!(
                started.elapsed() <= limit,
                "reap took {:?}",
                started.elapsed()
            );
            let _ = child.wait();
            return;
        }
        if started.elapsed() > limit {
            let _ = child.kill();
            let _ = child.wait();
            panic!("manim, latex, or dvisvgm still alive after 1s");
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
}

#[cfg(all(windows, test))]
fn process_alive(pid: u32) -> bool {
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const STILL_ACTIVE: u32 = 259;
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return false;
        }
        let mut code = 0u32;
        let ok = GetExitCodeProcess(handle, &mut code);
        CloseHandle(handle);
        ok != 0 && code == STILL_ACTIVE
    }
}

#[cfg(all(windows, test))]
fn wait_child_pids(path: &std::path::Path) -> (u32, u32) {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while std::time::Instant::now() < deadline {
        if let Ok(text) = std::fs::read_to_string(path) {
            let lines: Vec<&str> = text.lines().filter(|line| !line.is_empty()).collect();
            if lines.len() >= 2 {
                let latex = lines[0].trim().parse().expect("latex pid");
                let dvisvgm = lines[1].trim().parse().expect("dvisvgm pid");
                return (latex, dvisvgm);
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    panic!("child pids were not written");
}

#[cfg(all(windows, test))]
fn compile_sleeper(root: &std::path::Path, name: &str, source: &str) -> std::path::PathBuf {
    let stem = name.trim_end_matches(".exe");
    let source_path = root.join(format!("{stem}.cs"));
    let exe = root.join(name);
    std::fs::write(&source_path, source).expect("source");
    let windir = std::env::var("WINDIR").unwrap_or_else(|_| r"C:\Windows".to_string());
    let compiler = std::path::PathBuf::from(windir)
        .join("Microsoft.NET")
        .join("Framework64")
        .join("v4.0.30319")
        .join("csc.exe");
    let completed = std::process::Command::new(compiler)
        .arg("/nologo")
        .arg("/t:exe")
        .arg(format!("/out:{}", exe.display()))
        .arg(&source_path)
        .output()
        .expect("csc");
    if !completed.status.success() || !exe.is_file() {
        panic!("csc failed: {}", String::from_utf8_lossy(&completed.stderr));
    }
    exe
}

#[cfg(all(windows, test))]
fn sleeper_source() -> &'static str {
    "class Sleeper { static void Main() { System.Threading.Thread.Sleep(30000); } }\n"
}

#[cfg(all(windows, test))]
fn tree_parent_source() -> &'static str {
    r#"using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
class JobTree {
    static int Main() {
        string go = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_GO");
        string pids = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_PIDS");
        string latex = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_LATEX");
        string dvi = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_DVISVGM");
        if (go == null || pids == null || latex == null || dvi == null) return 2;
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while (!File.Exists(go)) {
            if (DateTime.UtcNow > deadline) return 3;
            Thread.Sleep(20);
        }
        var latexProc = StartHidden(latex);
        var dviProc = StartHidden(dvi);
        File.WriteAllText(pids, latexProc.Id + "\n" + dviProc.Id + "\n");
        Thread.Sleep(30000);
        return 0;
    }
    static Process StartHidden(string path) {
        var info = new ProcessStartInfo();
        info.FileName = path;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        return Process.Start(info);
    }
}
"#
}
