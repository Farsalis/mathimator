//! The worker line protocol as the shell speaks it (contract C2, C4, C9.1).

use serde::Serialize;
use serde_json::Value;

/// The protocol this shell speaks. The worker reports its own in `ready.protocol` (C6).
pub const PROTOCOL_VERSION: u64 = 2;

/// One request to the worker (C4), without the `id` the transport adds.
#[derive(Serialize)]
#[serde(tag = "cmd", rename_all = "snake_case")]
pub enum Request {
    Status,
    GetState,
    Edit {
        edit: Value,
    },
    Undo,
    Redo,
    Code,
    ClearCache,
    OpenProject {
        path: String,
        #[serde(skip_serializing_if = "is_false")]
        init: bool,
    },
    Reload,
    Overwrite,
    SaveAs {
        path: String,
    },
    RenderPreview,
    Export {
        force: bool,
    },
    Cancel {
        #[serde(rename = "jobId")]
        job_id: String,
    },
}

fn is_false(value: &bool) -> bool {
    !value
}

impl Request {
    /// The request as one line with its `id`, newline included.
    pub fn line(&self, id: &str) -> serde_json::Result<String> {
        #[derive(Serialize)]
        struct Line<'a> {
            id: &'a str,
            #[serde(flatten)]
            request: &'a Request,
        }

        let mut line = serde_json::to_string(&Line { id, request: self })?;
        line.push('\n');
        Ok(line)
    }
}

/// What a dialog command returns (C9.1): the worker's reply, passed through (D46), or what the
/// shell did itself.
#[derive(Debug, PartialEq, Serialize)]
#[serde(untagged)]
pub enum DialogReply {
    Worker(Value),
    /// `{cancelled: true}`: no folder or file was picked, or the user stayed.
    Cancelled {
        cancelled: bool,
    },
    /// `{path}`: where Export code wrote the scene.
    Written {
        path: String,
    },
}

impl DialogReply {
    pub const CANCELLED: Self = Self::Cancelled { cancelled: true };
}

/// A failure of the shell itself, rejected to the UI as `{code, message}` (C9.1, D45). Worker
/// replies, refusals included, are not `ShellError`s: they pass through as `Ok` (D46).
#[derive(Debug, PartialEq, Eq, Serialize)]
pub struct ShellError {
    pub code: ErrorCode,
    pub message: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    WorkerUnavailable,
    WorkerExited,
    Timeout,
    Io,
    /// The shell's own argument check; deliberately not the worker's `bad_request`.
    BadArgument,
}

impl ShellError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn parsed(line: &str) -> Value {
        assert!(line.ends_with('\n'), "{line:?} does not end the line");
        serde_json::from_str(line).unwrap()
    }

    #[test]
    fn request_lines_carry_cmd_id_and_fields() {
        assert_eq!(
            parsed(&Request::Reload.line("rpc-4").unwrap()),
            json!({"cmd": "reload", "id": "rpc-4"})
        );
        let open = Request::OpenProject {
            path: "C:\\p".to_owned(),
            init: false,
        };
        assert_eq!(
            parsed(&open.line("rpc-5").unwrap()),
            json!({"cmd": "open_project", "id": "rpc-5", "path": "C:\\p"})
        );
        let cancel = Request::Cancel {
            job_id: "preview-2".to_owned(),
        };
        assert_eq!(
            parsed(&cancel.line("cancel-6").unwrap()),
            json!({"cmd": "cancel", "id": "cancel-6", "jobId": "preview-2"})
        );
    }

    #[test]
    fn dialog_replies_serialize_as_the_contract_shapes() {
        let refused = json!({"event": "rejected", "jobId": "rpc-3", "code": "lock_failed"});
        let replies = [
            DialogReply::Worker(refused.clone()),
            DialogReply::CANCELLED,
            DialogReply::Written {
                path: "C:\\Lessons\\scene.py".to_owned(),
            },
        ]
        .map(|reply| serde_json::to_value(reply).unwrap());
        assert_eq!(
            replies,
            [
                refused,
                json!({"cancelled": true}),
                json!({"path": "C:\\Lessons\\scene.py"}),
            ]
        );
    }

    #[test]
    fn shell_error_serializes_as_code_and_message() {
        let error = ShellError::new(ErrorCode::BadArgument, "bad slot");
        assert_eq!(
            serde_json::to_value(error).unwrap(),
            json!({"code": "bad_argument", "message": "bad slot"})
        );
        let codes = [
            ErrorCode::WorkerUnavailable,
            ErrorCode::WorkerExited,
            ErrorCode::Timeout,
            ErrorCode::Io,
        ]
        .map(|code| serde_json::to_value(code).unwrap());
        assert_eq!(
            codes,
            ["worker_unavailable", "worker_exited", "timeout", "io"].map(Value::from)
        );
    }
}
