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
    /// The message later job starts refuse formulas with, or `None` once TeX works (C9.7).
    SetTexError {
        message: Option<String>,
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

    /// The request a line of `requests.json` stands for, built from the line's own values.
    fn shared_request(line: &Value) -> Request {
        let text = |name: &str| line[name].as_str().expect(name).to_owned();
        match line["cmd"].as_str().unwrap() {
            "status" => Request::Status,
            "get_state" => Request::GetState,
            "edit" => Request::Edit {
                edit: line["edit"].clone(),
            },
            "undo" => Request::Undo,
            "redo" => Request::Redo,
            "code" => Request::Code,
            "clear_cache" => Request::ClearCache,
            "open_project" => Request::OpenProject {
                path: text("path"),
                init: line["init"] == true,
            },
            "reload" => Request::Reload,
            "overwrite" => Request::Overwrite,
            "save_as" => Request::SaveAs { path: text("path") },
            "render_preview" => Request::RenderPreview,
            "export" => Request::Export {
                force: line["force"] == true,
            },
            "cancel" => Request::Cancel {
                job_id: text("jobId"),
            },
            "set_tex_error" => Request::SetTexError {
                message: line["message"].as_str().map(str::to_owned),
            },
            other => panic!("no request {other}"),
        }
    }

    #[test]
    fn each_shared_request_line_is_the_line_the_shell_writes() {
        let shared: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol/requests.json"
        ))
        .unwrap();
        for row in shared["requests"].as_array().unwrap() {
            let line = &row["line"];
            // The shell gives every request an id; the line without one is the worker's to refuse.
            let Some(id) = line["id"].as_str() else {
                continue;
            };
            assert_eq!(
                parsed(&shared_request(line).line(id).unwrap()),
                *line,
                "{row}"
            );
        }
    }

    #[test]
    fn set_tex_error_lines_carry_the_message_or_null() {
        let failed = Request::SetTexError {
            message: Some("LaTeX is not working.".to_owned()),
        };
        assert_eq!(
            parsed(&failed.line("tex-7").unwrap()),
            json!({"cmd": "set_tex_error", "id": "tex-7", "message": "LaTeX is not working."})
        );
        let passed = Request::SetTexError { message: None };
        assert_eq!(
            parsed(&passed.line("tex-8").unwrap()),
            json!({"cmd": "set_tex_error", "id": "tex-8", "message": null})
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
