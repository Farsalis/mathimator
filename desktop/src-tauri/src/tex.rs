//! The TeX self-test (contract C9.7, C13.2 E4): at every launch, one line through `latex` and
//! `dvisvgm` with the worker's TeX environment. Its failure blocks formulas, as a missing TinyTeX
//! does (C14 Q4).

use std::fs;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use crate::runtime::Runtime;
use crate::wait_child;

/// Why formulas cannot render when a release build has no TinyTeX (C9.7).
pub const MISSING: &str =
    "Formulas and axis labels cannot render: the bundled TeX is missing. Reinstall Mathimator.";

/// How long the whole self-test may take.
const LIMIT: Duration = Duration::from_secs(30);

const CHECK_TEX: &str = r"\documentclass[preview]{standalone}
\usepackage[english]{babel}
\usepackage{amsmath}
\usepackage{amssymb}
\begin{document}
$y = \sin(x)$
\end{document}
";

/// Runs the self-test in a fresh `%TEMP%\mathimator-texcheck-<pid>` folder, which it deletes
/// afterwards. A failure is the message formulas are then refused with.
pub fn self_test(runtime: &Runtime) -> Result<(), String> {
    let folder = std::env::temp_dir().join(format!("mathimator-texcheck-{}", std::process::id()));
    let _ = fs::remove_dir_all(&folder);
    let result = fs::create_dir(&folder)
        .map_err(|err| format!("could not create {}: {err}", folder.display()))
        .and_then(|()| compile(runtime, &folder));
    let _ = fs::remove_dir_all(&folder);
    result.map_err(|reason| {
        format!(
            "Formulas and axis labels cannot render on this PC: {}. Reinstall Mathimator for all users.",
            reason.trim_end_matches('.')
        )
    })
}

/// Compiles the check document to an SVG, and returns why it did not.
fn compile(runtime: &Runtime, folder: &Path) -> Result<(), String> {
    fs::write(folder.join("check.tex"), CHECK_TEX)
        .map_err(|err| format!("could not write check.tex: {err}"))?;
    let deadline = Instant::now() + LIMIT;
    let mut latex = runtime.command(runtime.tex_program("latex"));
    latex.args([
        "-interaction=batchmode",
        "-halt-on-error",
        "-no-shell-escape",
        "check.tex",
    ]);
    run(latex, "latex", folder, deadline)
        .map_err(|reason| first_error(&folder.join("check.log")).unwrap_or(reason))?;
    let mut dvisvgm = runtime.command(runtime.tex_program("dvisvgm"));
    dvisvgm.args(["--no-fonts", "check.dvi"]);
    run(dvisvgm, "dvisvgm", folder, deadline)?;
    if folder.join("check.svg").is_file() {
        Ok(())
    } else {
        Err("dvisvgm wrote no SVG".to_owned())
    }
}

/// Runs one TeX program in `folder`, and stops it at `deadline`.
fn run(mut command: Command, name: &str, folder: &Path, deadline: Instant) -> Result<(), String> {
    let mut child = command
        .current_dir(folder)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| format!("could not start {name}: {err}"))?;
    match wait_child(
        &mut child,
        deadline.saturating_duration_since(Instant::now()),
    ) {
        Some(status) if status.success() => Ok(()),
        Some(status) => Err(format!("{name} failed ({status})")),
        None => {
            let _ = child.kill();
            let _ = child.wait();
            Err(format!(
                "{name} did not finish within {} s",
                LIMIT.as_secs()
            ))
        }
    }
}

/// The first `!` line of a TeX log, which names the error that stopped TeX.
fn first_error(log: &Path) -> Option<String> {
    let bytes = fs::read(log).ok()?;
    String::from_utf8_lossy(&bytes)
        .lines()
        .find(|line| line.starts_with('!'))
        .map(str::to_owned)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::NoticesFile;

    #[test]
    fn the_first_bang_line_of_the_log_is_the_error() {
        let folder =
            std::env::temp_dir().join(format!("mathimator-texlog-test-{}", std::process::id()));
        fs::create_dir_all(&folder).unwrap();
        let log = folder.join("check.log");
        fs::write(
            &log,
            b"This is pdfTeX\r\n(./check.tex\r\n! LaTeX Error: File `standalone.cls' not found.\r\n\r\n! Emergency stop.\r\n\xff\r\n",
        )
        .unwrap();
        let error = first_error(&log);
        let missing = first_error(&folder.join("missing.log"));
        let _ = fs::remove_dir_all(&folder);
        assert_eq!(
            error.as_deref(),
            Some("! LaTeX Error: File `standalone.cls' not found.")
        );
        assert_eq!(missing, None);
    }

    #[test]
    fn a_tinytex_without_latex_fails_with_the_reinstall_message_and_leaves_no_folder() {
        let empty =
            std::env::temp_dir().join(format!("mathimator-tinytex-test-{}", std::process::id()));
        fs::create_dir_all(&empty).unwrap();
        let runtime = Runtime {
            worker: Err("none".to_owned()),
            typst: Err("none".to_owned()),
            tinytex_bin: Some(empty.clone()),
            tex_error: None,
            work_dir: std::env::temp_dir(),
            notices: NoticesFile {
                path: None,
                missing: "none".to_owned(),
            },
        };
        let result = self_test(&runtime);
        let _ = fs::remove_dir_all(&empty);
        let message = result.unwrap_err();
        assert!(
            message.starts_with(
                "Formulas and axis labels cannot render on this PC: could not start latex: "
            ),
            "{message}"
        );
        assert!(
            message.ends_with(". Reinstall Mathimator for all users."),
            "{message}"
        );
        let checked =
            std::env::temp_dir().join(format!("mathimator-texcheck-{}", std::process::id()));
        assert!(!checked.exists());
    }
}
