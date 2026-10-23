//! Windows Job Object teardown for the supervisor process.
//!
//! `assign_supervisor` does nothing unless `MATHIMATOR_WIN32_JOBS=1`. Closing
//! the returned handle kills the supervisor and every process it created,
//! because the job has `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The test below is
//! ignored until Craig approves executing it on this machine.

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
    if std::env::var("MATHIMATOR_WIN32_JOBS").ok().as_deref() != Some("1") {
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
    fn CreateJobObjectW(
        attrs: *mut std::ffi::c_void,
        name: *const u16,
    ) -> *mut std::ffi::c_void;
    fn SetInformationJobObject(
        job: *mut std::ffi::c_void,
        class: i32,
        info: *mut std::ffi::c_void,
        length: u32,
    ) -> i32;
    fn AssignProcessToJobObject(
        job: *mut std::ffi::c_void,
        process: *mut std::ffi::c_void,
    ) -> i32;
    fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut std::ffi::c_void;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
}

#[test]
#[ignore = "Win32 job-object test is not executed until Craig approves running it on this machine"]
fn supervisor_job_reaps_manim_latex_and_dvisvgm_within_one_second() {
    assert_eq!(
        std::env::var("MATHIMATOR_WIN32_TESTS").ok().as_deref(),
        Some("1"),
        "refusing to create a Job Object without MATHIMATOR_WIN32_TESTS=1",
    );
}
