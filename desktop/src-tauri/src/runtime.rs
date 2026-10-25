//! The tools the shell runs and how it starts them (contract C9.6): Python with the worker, Typst
//! for the glyph preview, TinyTeX, and the sample; and the third-party notices the bundle ships
//! (D75). Debug builds use the repository; release builds use only the installed bundle, as its
//! runtime.json describes it (D30, D33).

use std::env;
use std::ffi::{OsStr, OsString};
use std::fs;
use std::io;
use std::iter;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde::Deserialize;
use serde_json::Value;
use tauri::{AppHandle, Manager};

use crate::{equation, tex};

const PYTHON_OVERRIDE: &str = "MATHIMATOR_PYTHON";
const TYPST_OVERRIDE: &str = "MATHIMATOR_TYPST";
const TINYTEX_OVERRIDE: &str = "MATHIMATOR_TINYTEX_BIN";
/// The prefix of the shell's and the worker's own variables. A release build ignores every
/// inherited one but [`SHELL_READY_FILE`] and passes none of them to its children (D30).
const OVERRIDE_PREFIX: &str = "MATHIMATOR_";
/// The file that gets the shell-ready marker: the one `MATHIMATOR_*` variable a release build
/// reads (D32).
pub const SHELL_READY_FILE: &str = "MATHIMATOR_SHELL_READY_FILE";
/// The variable that names a Windows venv launcher to the real interpreter it starts, so that the
/// venv applies there (D16).
const VENV_LAUNCHER: &str = "__PYVENV_LAUNCHER__";
/// The notices scripts/generate_notices.py writes into the bundle, which the installer ships as
/// runtime/THIRD_PARTY_NOTICES.txt (D75).
const NOTICES: &str = "THIRD_PARTY_NOTICES.txt";
/// Why a debug build has no notices to show.
const NOTICES_NOT_BUILT: &str = "The third-party notices are not built: packaging/bundle has no \
    THIRD_PARTY_NOTICES.txt. Build the bundle, then run scripts/generate_notices.py.";

/// What the shell runs, and the notices it ships. Each part is resolved on its own, so a missing
/// piece disables only what needs it (D31).
#[derive(Clone, Debug, PartialEq)]
pub struct Runtime {
    /// Python and the worker, or why the worker cannot start.
    pub worker: Result<WorkerFiles, String>,
    /// Typst for the glyph preview, or why there is none.
    pub typst: Result<PathBuf, String>,
    /// The TinyTeX bin folder. A debug build without one uses the TeX on PATH.
    pub tinytex_bin: Option<PathBuf>,
    /// Why formulas cannot render, when TeX is blocked from the start (C9.7).
    pub tex_error: Option<String>,
    /// The working directory of every child (C9.6).
    pub work_dir: PathBuf,
    /// The third-party notices the Notices panel shows (D75).
    pub notices: NoticesFile,
}

/// The third-party notices file and what the shell says when it is missing (D75). It is read each
/// time the Notices panel asks, so notices written while the app runs show without a restart.
#[derive(Clone, Debug, PartialEq)]
pub struct NoticesFile {
    /// Where the file is, or None when there is no bundle to look in.
    pub path: Option<PathBuf>,
    /// Why there is nothing to show when the file is missing.
    pub missing: String,
}

/// What starting the worker takes.
#[derive(Clone, Debug, PartialEq)]
pub struct WorkerFiles {
    /// The interpreter with Manim installed, which the worker gets as `--python`: the real one in
    /// a release build (D33), the repository's venv or `MATHIMATOR_PYTHON` in a debug build.
    pub python: PathBuf,
    /// The real interpreter that `python` starts as its child when it is a Windows venv launcher,
    /// which only a debug build runs (D16).
    pub base_python: Option<PathBuf>,
    /// The folder that holds `mathimator_worker`.
    pub source: PathBuf,
    /// The reference-60 project.json that Sample copies.
    pub sample: PathBuf,
}

/// The fields of runtime.json the shell uses (C9.6). The informational ones are only logged, and
/// unknown ones are ignored.
#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Manifest {
    platform: Option<String>,
    python: Option<String>,
    worker: Option<String>,
    sample: Option<String>,
    typst: Option<String>,
    tinytex_bin: Option<String>,
}

/// The `MATHIMATOR_*` tool overrides, which only debug builds honour (D30).
#[derive(Default)]
pub struct Overrides {
    python: Option<PathBuf>,
    typst: Option<PathBuf>,
    tinytex_bin: Option<PathBuf>,
}

impl Overrides {
    pub fn from_env() -> Self {
        let path = |name| {
            env::var_os(name)
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
        };
        Self {
            python: path(PYTHON_OVERRIDE),
            typst: path(TYPST_OVERRIDE),
            tinytex_bin: path(TINYTEX_OVERRIDE),
        }
    }
}

/// Finds the runtime and logs what it found (C9.8). It never fails: a missing part carries the
/// reason instead (D31).
pub fn resolve(app: &AppHandle, log: &dyn Fn(&str)) -> Runtime {
    let runtime = match repo_root() {
        Some(repo) => development(&repo, &Overrides::from_env()),
        None => {
            let work_dir = app
                .path()
                .app_local_data_dir()
                .unwrap_or_else(|_| env::temp_dir());
            if let Err(err) = fs::create_dir_all(&work_dir) {
                log(&format!("Could not create {}: {err}.", work_dir.display()));
            }
            let bundle = app
                .path()
                .resource_dir()
                .map(|resources| plain(resources).join("runtime"));
            let ignored = ignored_in_release(env::vars_os());
            installed(bundle, work_dir, &ignored, log)
        }
    };
    log(&runtime.describe());
    runtime
}

/// The repository, in debug builds only. Release builds neither use nor embed its path (D30,
/// F057).
#[cfg(debug_assertions)]
pub fn repo_root() -> Option<PathBuf> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map(Path::to_path_buf)
}

/// The repository, in debug builds only. Release builds neither use nor embed its path (D30,
/// F057).
#[cfg(not(debug_assertions))]
pub fn repo_root() -> Option<PathBuf> {
    None
}

/// Debug builds: the repository's worker, .venv and sample, the overrides, and the Typst, TinyTeX
/// and notices of packaging/bundle when it has them (C9.6, F055).
pub fn development(repo: &Path, overrides: &Overrides) -> Runtime {
    let bundle = repo.join("packaging").join("bundle");
    let manifest = read_manifest(&bundle)
        .map(|(manifest, _)| manifest)
        .unwrap_or_default();
    let python = overrides
        .python
        .clone()
        .unwrap_or_else(|| venv_python(repo));
    let worker = if python.is_file() {
        Ok(WorkerFiles {
            base_python: venv_base(&python),
            python,
            source: repo.join("worker"),
            sample: repo
                .join("tests")
                .join("fixtures")
                .join("reference-60")
                .join("project.json"),
        })
    } else {
        Err(format!("Python was not found at {}.", python.display()))
    };
    let typst = match &overrides.typst {
        Some(path) if path.is_file() => Ok(path.clone()),
        Some(path) => Err(format!(
            "{TYPST_OVERRIDE} is not a file: {}",
            path.display()
        )),
        None => file_in(&bundle, manifest.typst.as_deref())
            .or_else(|| on_path("typst"))
            .ok_or_else(|| format!("{} was not found on PATH", equation::VERSION)),
    };
    let (tinytex_bin, tex_error) = match &overrides.tinytex_bin {
        Some(path) if path.is_dir() => (Some(path.clone()), None),
        Some(path) => (
            None,
            Some(format!(
                "Formulas and axis labels cannot render: {TINYTEX_OVERRIDE} is not a folder ({}).",
                path.display()
            )),
        ),
        None => (folder_in(&bundle, manifest.tinytex_bin.as_deref()), None),
    };
    Runtime {
        worker,
        typst,
        tinytex_bin,
        tex_error,
        work_dir: repo.to_owned(),
        notices: NoticesFile {
            path: Some(bundle.join(NOTICES)),
            missing: NOTICES_NOT_BUILT.to_owned(),
        },
    }
}

/// Release builds: only the bundle, as its runtime.json describes it (C9.6, D30, D33). Without a
/// resource folder there is no bundle: nothing is looked up from the working directory (F124).
/// The notices need no runtime.json, so they stay readable when the worker cannot start. The
/// `ignored` variables are only named in shell.log.
fn installed(
    bundle: tauri::Result<PathBuf>,
    work_dir: PathBuf,
    ignored: &[String],
    log: &dyn Fn(&str),
) -> Runtime {
    for name in ignored {
        log(&format!(
            "Ignored {name}: release builds neither read it nor pass it to the worker."
        ));
    }
    let notices = NoticesFile {
        path: bundle.as_ref().ok().map(|bundle| bundle.join(NOTICES)),
        missing: incomplete(NOTICES),
    };
    let bundle = match &bundle {
        Ok(bundle) => bundle.as_path(),
        Err(err) => {
            log(&format!("Could not find the resource folder: {err}."));
            return Runtime::unusable(incomplete("runtime.json"), notices, work_dir);
        }
    };
    let Some((manifest, text)) = read_manifest(bundle) else {
        return Runtime::unusable(incomplete("runtime.json"), notices, work_dir);
    };
    log(&format!("runtime.json: {text}"));
    if manifest.platform != Some(platform()) {
        return Runtime::unusable(incomplete("platform"), notices, work_dir);
    }
    let python = file_in(bundle, manifest.python.as_deref()).ok_or_else(|| incomplete("python"));
    let source = folder_in(bundle, manifest.worker.as_deref())
        .filter(|folder| folder.join("mathimator_worker").is_dir())
        .ok_or_else(|| incomplete("worker"));
    let sample = file_in(bundle, manifest.sample.as_deref()).ok_or_else(|| incomplete("sample"));
    let worker = python.and_then(|python| {
        Ok(WorkerFiles {
            python,
            base_python: None,
            source: source?,
            sample: sample?,
        })
    });
    let typst = file_in(bundle, manifest.typst.as_deref())
        .ok_or_else(|| "The bundled Typst is missing. Reinstall Mathimator.".to_owned());
    let tinytex_bin = folder_in(bundle, manifest.tinytex_bin.as_deref());
    let tex_error = tinytex_bin.is_none().then(|| tex::MISSING.to_owned());
    Runtime {
        worker,
        typst,
        tinytex_bin,
        tex_error,
        work_dir,
        notices,
    }
}

/// What the shell says of an installed runtime that lacks `field` (C9.6).
fn incomplete(field: &str) -> String {
    format!("The installed runtime is incomplete ({field}). Reinstall Mathimator.")
}

impl Runtime {
    /// A runtime whose tools cannot be used at all, with the notices it may still have.
    fn unusable(reason: String, notices: NoticesFile, work_dir: PathBuf) -> Self {
        Self {
            worker: Err(reason.clone()),
            typst: Err(reason),
            tinytex_bin: None,
            tex_error: Some(tex::MISSING.to_owned()),
            work_dir,
            notices,
        }
    }

    /// A child with the worker's environment (C9.6, F124): no console window, the fixed working
    /// directory, the inherited `PYTHON*`, `TEXINPUTS` and `TEXMF*` variables removed (in a
    /// release build the `MATHIMATOR_*` ones too), and the TinyTeX bin folder first on PATH.
    pub fn command(&self, program: impl AsRef<OsStr>) -> Command {
        self.command_with(program, env::vars_os(), !cfg!(debug_assertions))
    }

    fn command_with(
        &self,
        program: impl AsRef<OsStr>,
        inherited: impl IntoIterator<Item = (OsString, OsString)>,
        release: bool,
    ) -> Command {
        let mut command = hidden_command(program);
        command.current_dir(&self.work_dir);
        let mut path = None;
        for (key, value) in inherited {
            if is_cleaned(&key, release) {
                command.env_remove(&key);
            } else if key.eq_ignore_ascii_case("PATH") {
                path = Some(value);
            }
        }
        if let Some(bin) = &self.tinytex_bin {
            let paths = iter::once(bin.clone()).chain(path.iter().flat_map(env::split_paths));
            if let Ok(joined) = env::join_paths(paths) {
                command.env("PATH", joined);
            }
        }
        command
    }

    /// `python -P -m <module>` with the worker package importable (C9.6). `-P` keeps the working
    /// directory off `sys.path` (F124). Behind a venv launcher the real interpreter runs, told the
    /// launcher as the launcher itself would tell it, so that the process the shell puts in its
    /// Job Object is the one that starts the worker's children (D16).
    pub fn python_command(&self, files: &WorkerFiles, module: &str) -> Command {
        let mut command = self.command(files.base_python.as_ref().unwrap_or(&files.python));
        if files.base_python.is_some() {
            command.env(VENV_LAUNCHER, &files.python);
        }
        command
            .arg("-P")
            .arg("-m")
            .arg(module)
            .env("PYTHONPATH", &files.source)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1");
        command
    }

    /// A TeX program: TinyTeX's when there is TinyTeX, else the one on PATH.
    pub fn tex_program(&self, name: &str) -> PathBuf {
        match &self.tinytex_bin {
            Some(bin) => bin.join(format!("{name}{}", env::consts::EXE_SUFFIX)),
            None => PathBuf::from(name),
        }
    }

    /// One shell.log line that names every tool (C9.8).
    fn describe(&self) -> String {
        let worker = match &self.worker {
            Ok(files) => format!(
                "python {}{}, worker {}, sample {}",
                files.python.display(),
                files
                    .base_python
                    .as_ref()
                    .map_or(String::new(), |base| format!(" (runs {})", base.display())),
                files.source.display(),
                files.sample.display()
            ),
            Err(reason) => format!("no worker ({reason})"),
        };
        let typst = match &self.typst {
            Ok(path) => path.display().to_string(),
            Err(reason) => format!("none ({reason})"),
        };
        let tex = match (&self.tinytex_bin, &self.tex_error) {
            (Some(bin), _) => bin.display().to_string(),
            (None, Some(error)) => format!("blocked ({error})"),
            (None, None) => "from PATH".to_owned(),
        };
        format!(
            "Runtime: {worker}; typst {typst}; TinyTeX {tex}; working folder {}.",
            self.work_dir.display()
        )
    }
}

impl NoticesFile {
    /// The text of the notices, or why there is none to show (D75).
    pub fn read(&self) -> Result<String, String> {
        let path = self.path.as_ref().ok_or_else(|| self.missing.clone())?;
        fs::read_to_string(path).map_err(|err| match err.kind() {
            io::ErrorKind::NotFound => self.missing.clone(),
            _ => format!("Could not read {}: {err}.", path.display()),
        })
    }
}

/// A command whose console window stays hidden (F105).
pub fn hidden_command(program: impl AsRef<OsStr>) -> Command {
    #[cfg_attr(not(windows), allow(unused_mut))]
    let mut command = Command::new(program);
    #[cfg(windows)]
    command.creation_flags(windows_sys::Win32::System::Threading::CREATE_NO_WINDOW);
    command
}

/// Whether children lose an inherited variable: the `PYTHON*` ones change how the interpreter
/// starts, `PYTHONHOME` among them, and `TEXINPUTS` and `TEXMF*` change which files kpathsea
/// finds (C9.6, F124, G2.6). A `release` build drops the `MATHIMATOR_*` ones too, so that no
/// development switch such as `MATHIMATOR_WIN32_JOBS=0` reaches the worker (D30).
fn is_cleaned(key: &OsStr, release: bool) -> bool {
    let key = key.to_string_lossy().to_ascii_uppercase();
    key.starts_with("PYTHON")
        || key.starts_with("TEXMF")
        || key == "TEXINPUTS"
        || (release && key.starts_with(OVERRIDE_PREFIX))
}

/// The `inherited` variables a release build ignores: every `MATHIMATOR_*` one but
/// [`SHELL_READY_FILE`] (D30, D32).
fn ignored_in_release(inherited: impl IntoIterator<Item = (OsString, OsString)>) -> Vec<String> {
    inherited
        .into_iter()
        .map(|(key, _)| key.to_string_lossy().into_owned())
        .filter(|key| {
            let key = key.to_ascii_uppercase();
            key.starts_with(OVERRIDE_PREFIX) && key != SHELL_READY_FILE
        })
        .collect()
}

/// The platform runtime.json must name, for example `windows-x86_64`.
fn platform() -> String {
    format!("{}-{}", env::consts::OS, env::consts::ARCH)
}

/// Reads runtime.json, and returns it with its text on one line for shell.log.
fn read_manifest(bundle: &Path) -> Option<(Manifest, String)> {
    let bytes = fs::read(bundle.join("runtime.json")).ok()?;
    let value: Value = serde_json::from_slice(&bytes).ok()?;
    let manifest = Manifest::deserialize(&value).ok()?;
    Some((manifest, value.to_string()))
}

/// A runtime.json path, which separates folders with `/`, inside the bundle.
fn bundled(bundle: &Path, relative: &str) -> PathBuf {
    relative
        .split('/')
        .fold(bundle.to_path_buf(), |path, part| path.join(part))
}

fn file_in(bundle: &Path, relative: Option<&str>) -> Option<PathBuf> {
    relative
        .map(|relative| bundled(bundle, relative))
        .filter(|path| path.is_file())
}

fn folder_in(bundle: &Path, relative: Option<&str>) -> Option<PathBuf> {
    relative
        .map(|relative| bundled(bundle, relative))
        .filter(|path| path.is_dir())
}

fn venv_python(repo: &Path) -> PathBuf {
    let venv = repo.join(".venv");
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// The real interpreter behind `python` when it is a Windows venv launcher: the file of the same
/// name in the `home` folder of the venv's pyvenv.cfg, read as the launcher reads it (D16). The
/// launcher takes the first pyvenv.cfg beside it, else one folder up, and the first lowercase
/// `home` key in it. A POSIX venv's python is a link to the real interpreter, not a launcher.
fn venv_base(python: &Path) -> Option<PathBuf> {
    if !cfg!(windows) {
        return None;
    }
    let config = python
        .parent()?
        .ancestors()
        .take(2)
        .find_map(|venv| fs::read_to_string(venv.join("pyvenv.cfg")).ok())?;
    let home = config.lines().find_map(|line| {
        let (key, value) = line.split_once('=')?;
        (key.trim() == "home").then(|| value.trim())
    })?;
    Some(Path::new(home).join(python.file_name()?)).filter(|base| base.is_file())
}

fn on_path(name: &str) -> Option<PathBuf> {
    let file = format!("{name}{}", env::consts::EXE_SUFFIX);
    env::split_paths(&env::var_os("PATH")?)
        .map(|folder| folder.join(&file))
        .find(|path| path.is_file())
}

/// Tauri's resource folder is canonical, so on Windows it starts with `\\?\`. Python, PATH
/// lookups and TeX need the plain form, which names the same folder.
fn plain(path: PathBuf) -> PathBuf {
    match path.to_str().and_then(|text| text.strip_prefix(r"\\?\")) {
        Some(rest) if rest.get(1..3) == Some(":\\") => PathBuf::from(rest),
        _ => path,
    }
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::process::Stdio;
    use std::sync::atomic::{AtomicUsize, Ordering};

    use serde_json::json;

    use super::*;

    /// A scratch folder, deleted when the test ends.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let folder = env::temp_dir().join(format!(
                "mathimator-runtime-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir_all(&folder).unwrap();
            Self(folder)
        }

        /// Creates a file and its folders.
        fn file(&self, relative: &str) -> PathBuf {
            let path = bundled(&self.0, relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, b"").unwrap();
            path
        }

        fn folder(&self, relative: &str) -> PathBuf {
            let path = bundled(&self.0, relative);
            fs::create_dir_all(&path).unwrap();
            path
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    /// An installed bundle with every file today's bundle scripts and generate_notices.py write,
    /// and runtime.json.
    fn bundle(manifest: &Value) -> Scratch {
        let scratch = Scratch::new();
        scratch.file("python/python.exe");
        scratch.folder("worker/mathimator_worker");
        scratch.file("samples/reference-60/project.json");
        scratch.file("typst/typst.exe");
        scratch.folder("tinytex/bin/windows");
        scratch.file(NOTICES);
        fs::write(scratch.0.join("runtime.json"), manifest.to_string()).unwrap();
        scratch
    }

    fn manifest() -> Value {
        json!({
            "python": "python/python.exe",
            "worker": "worker",
            "pythonVersion": "3.14.9",
            "pythonSelection": "CPython 3.14.9",
            "manim": "0.22.0",
            "lockSha256": "0".repeat(64),
            "typst": "typst/typst.exe",
            "typstVersion": "typst 0.15.0",
            "tinytexBin": "tinytex/bin/windows",
            "tinytexVersion": "tlmgr revision 80658",
            "tinytexRelease": "2026.10",
            "sample": "samples/reference-60/project.json",
            "platform": platform(),
        })
    }

    fn without(field: &str) -> Value {
        let mut manifest = manifest();
        manifest.as_object_mut().unwrap().remove(field);
        manifest
    }

    fn install(scratch: &Scratch) -> (Runtime, Vec<String>) {
        install_from(Ok(scratch.0.clone()), &[])
    }

    /// What an installed runtime knows of its notices before it reads them.
    fn installed_notices(path: Option<PathBuf>) -> NoticesFile {
        NoticesFile {
            path,
            missing: incomplete(NOTICES),
        }
    }

    fn install_from(bundle: tauri::Result<PathBuf>, ignored: &[String]) -> (Runtime, Vec<String>) {
        let logs = RefCell::new(Vec::new());
        let runtime = installed(bundle, PathBuf::from("C:\\work"), ignored, &|line: &str| {
            logs.borrow_mut().push(line.to_owned())
        });
        (runtime, logs.into_inner())
    }

    #[test]
    fn a_complete_bundle_resolves_every_tool_from_runtime_json() {
        let scratch = bundle(&manifest());
        let (runtime, logs) = install(&scratch);
        let root = &scratch.0;
        assert_eq!(
            runtime,
            Runtime {
                worker: Ok(WorkerFiles {
                    python: root.join("python").join("python.exe"),
                    base_python: None,
                    source: root.join("worker"),
                    sample: root
                        .join("samples")
                        .join("reference-60")
                        .join("project.json"),
                }),
                typst: Ok(root.join("typst").join("typst.exe")),
                tinytex_bin: Some(root.join("tinytex").join("bin").join("windows")),
                tex_error: None,
                work_dir: PathBuf::from("C:\\work"),
                notices: installed_notices(Some(root.join(NOTICES))),
            }
        );
        assert_eq!(logs, [format!("runtime.json: {}", manifest())]);
    }

    #[test]
    fn a_missing_required_field_makes_the_worker_unavailable() {
        for field in ["python", "worker", "sample"] {
            let scratch = bundle(&without(field));
            let (runtime, _) = install(&scratch);
            assert_eq!(runtime.worker, Err(incomplete(field)), "{field}");
            assert!(
                runtime.typst.is_ok() && runtime.tex_error.is_none(),
                "{field}"
            );
        }
    }

    #[test]
    fn a_required_file_that_is_gone_counts_as_missing() {
        let scratch = bundle(&manifest());
        fs::remove_file(scratch.0.join("python").join("python.exe")).unwrap();
        assert_eq!(install(&scratch).0.worker, Err(incomplete("python")));
        let scratch = bundle(&manifest());
        fs::remove_dir(scratch.0.join("worker").join("mathimator_worker")).unwrap();
        assert_eq!(install(&scratch).0.worker, Err(incomplete("worker")));
    }

    #[test]
    fn a_platform_mismatch_makes_the_whole_bundle_unusable() {
        let mut other = manifest();
        other["platform"] = json!("linux-x86_64-or-other");
        let scratch = bundle(&other);
        let (runtime, _) = install(&scratch);
        assert_eq!(
            runtime,
            Runtime::unusable(
                incomplete("platform"),
                installed_notices(Some(scratch.0.join(NOTICES))),
                PathBuf::from("C:\\work")
            )
        );
        assert_eq!(runtime.tex_error.as_deref(), Some(tex::MISSING));
    }

    #[test]
    fn a_missing_or_unreadable_runtime_json_makes_the_bundle_unusable() {
        let scratch = Scratch::new();
        let (runtime, logs) = install(&scratch);
        assert_eq!(runtime.worker, Err(incomplete("runtime.json")));
        assert!(logs.is_empty());
        fs::write(scratch.0.join("runtime.json"), b"{\"python\": ").unwrap();
        assert_eq!(install(&scratch).0.worker, Err(incomplete("runtime.json")));
    }

    #[test]
    fn without_a_resource_folder_the_bundle_is_unusable() {
        let (runtime, logs) = install_from(Err(tauri::Error::UnknownPath), &[]);
        assert_eq!(
            runtime,
            Runtime::unusable(
                incomplete("runtime.json"),
                installed_notices(None),
                PathBuf::from("C:\\work")
            )
        );
        assert_eq!(logs, ["Could not find the resource folder: unknown path."]);
    }

    #[test]
    fn a_missing_typst_disables_only_the_glyph_preview() {
        let scratch = bundle(&without("typst"));
        let (runtime, _) = install(&scratch);
        assert_eq!(
            runtime.typst,
            Err("The bundled Typst is missing. Reinstall Mathimator.".to_owned())
        );
        assert!(runtime.worker.is_ok());
        assert!(runtime.tinytex_bin.is_some() && runtime.tex_error.is_none());
        let scratch = bundle(&manifest());
        fs::remove_file(scratch.0.join("typst").join("typst.exe")).unwrap();
        assert!(install(&scratch).0.typst.is_err());
    }

    #[test]
    fn a_missing_tinytex_blocks_tex_with_the_reinstall_message() {
        for scratch in [bundle(&without("tinytexBin")), bundle(&manifest())] {
            fs::remove_dir_all(scratch.0.join("tinytex")).unwrap();
            let (runtime, _) = install(&scratch);
            assert_eq!(runtime.tinytex_bin, None);
            assert_eq!(runtime.tex_error.as_deref(), Some(tex::MISSING));
            assert!(runtime.worker.is_ok() && runtime.typst.is_ok());
        }
    }

    #[test]
    fn unknown_runtime_json_fields_are_ignored() {
        let mut extra = manifest();
        extra["tlnetRepository"] = json!("https://example.invalid/tlnet");
        extra["layout"] = json!({"version": 9, "parts": [1, 2]});
        let scratch = bundle(&extra);
        let (runtime, logs) = install(&scratch);
        assert!(runtime.worker.is_ok() && runtime.typst.is_ok() && runtime.tex_error.is_none());
        assert!(logs[0].contains("tlnetRepository"), "{logs:?}");
    }

    #[test]
    fn release_ignores_every_mathimator_variable_but_the_ready_file_and_logs_them() {
        let inherited = [
            ("MATHIMATOR_PYTHON", "C:\\elsewhere\\python.exe"),
            ("MATHIMATOR_SHELL_READY_FILE", "C:\\out\\shell-ready.txt"),
            ("MATHIMATOR_SKIP_PROCESS_TABLE", "1"),
            ("Mathimator_Win32_Jobs", "0"),
            ("Path", "C:\\Windows"),
        ]
        .map(|(key, value)| (OsString::from(key), OsString::from(value)));
        let ignored = ignored_in_release(inherited);
        let names = [
            "MATHIMATOR_PYTHON",
            "MATHIMATOR_SKIP_PROCESS_TABLE",
            "Mathimator_Win32_Jobs",
        ];
        assert_eq!(ignored, names);
        let scratch = bundle(&manifest());
        let (_, logs) = install_from(Ok(scratch.0.clone()), &ignored);
        assert_eq!(
            logs[..3],
            names.map(|name| format!(
                "Ignored {name}: release builds neither read it nor pass it to the worker."
            ))
        );
    }

    #[test]
    fn only_debug_builds_have_a_repository_root_and_it_holds_the_worker_source() {
        let repo = repo_root();
        assert_eq!(repo.is_some(), cfg!(debug_assertions));
        if let Some(repo) = repo {
            assert!(repo.join("worker").join("mathimator_worker").is_dir());
            assert!(!repo.to_string_lossy().starts_with(r"\\?\"));
        }
    }

    #[test]
    fn debug_uses_the_repository_and_the_tools_of_packaging_bundle() {
        let repo = Scratch::new();
        let python = repo.file(".venv/Scripts/python.exe");
        repo.file(".venv/bin/python");
        let typst = repo.file("packaging/bundle/typst/typst.exe");
        let tinytex = repo.folder("packaging/bundle/tinytex/bin/windows");
        fs::write(
            repo.0.join("packaging").join("bundle").join("runtime.json"),
            json!({"typst": "typst/typst.exe", "tinytexBin": "tinytex/bin/windows"}).to_string(),
        )
        .unwrap();
        let runtime = development(&repo.0, &Overrides::default());
        let python = if cfg!(windows) {
            python
        } else {
            repo.0.join(".venv").join("bin").join("python")
        };
        assert_eq!(
            runtime,
            Runtime {
                worker: Ok(WorkerFiles {
                    python,
                    base_python: None,
                    source: repo.0.join("worker"),
                    sample: repo
                        .0
                        .join("tests")
                        .join("fixtures")
                        .join("reference-60")
                        .join("project.json"),
                }),
                typst: Ok(typst),
                tinytex_bin: Some(tinytex),
                tex_error: None,
                work_dir: repo.0.clone(),
                notices: NoticesFile {
                    path: Some(repo.0.join("packaging").join("bundle").join(NOTICES)),
                    missing: NOTICES_NOT_BUILT.to_owned(),
                },
            }
        );
    }

    #[test]
    fn debug_says_the_notices_are_not_built_until_generate_notices_writes_them() {
        let repo = Scratch::new();
        let runtime = development(&repo.0, &Overrides::default());
        assert_eq!(runtime.notices.read(), Err(NOTICES_NOT_BUILT.to_owned()));
        let text = "Third-party notices for Mathimator 0.1.0\n";
        fs::write(repo.file("packaging/bundle/THIRD_PARTY_NOTICES.txt"), text).unwrap();
        assert_eq!(runtime.notices.read().as_deref(), Ok(text));
    }

    #[test]
    fn the_installed_notices_stay_readable_when_the_worker_cannot_start() {
        let scratch = bundle(&manifest());
        let text = "Third-party notices for Mathimator 0.1.0\n\nPyAV 19.0.2: BSD-3-Clause [T3]\n";
        fs::write(scratch.0.join(NOTICES), text).unwrap();
        fs::remove_file(scratch.0.join("runtime.json")).unwrap();
        let (runtime, _) = install(&scratch);
        assert_eq!(runtime.worker, Err(incomplete("runtime.json")));
        assert_eq!(runtime.notices.read().as_deref(), Ok(text));
    }

    #[test]
    fn missing_installed_notices_ask_for_a_reinstall_and_leave_the_tools_alone() {
        let scratch = bundle(&manifest());
        fs::remove_file(scratch.0.join(NOTICES)).unwrap();
        let (runtime, _) = install(&scratch);
        assert_eq!(
            runtime.notices.read(),
            Err("The installed runtime is incomplete (THIRD_PARTY_NOTICES.txt). Reinstall Mathimator.".to_owned())
        );
        assert!(runtime.worker.is_ok() && runtime.typst.is_ok() && runtime.tex_error.is_none());
    }

    #[test]
    fn notices_that_cannot_be_read_say_which_file_failed() {
        let scratch = bundle(&manifest());
        let (runtime, _) = install(&scratch);
        let file = scratch.0.join(NOTICES);
        fs::write(&file, b"\xFF is not UTF-8").unwrap();
        let message = runtime.notices.read().unwrap_err();
        assert!(
            message.starts_with(&format!("Could not read {}: ", file.display())),
            "{message}"
        );
    }

    #[test]
    fn debug_honours_the_overrides_and_a_bad_one_disables_only_its_feature() {
        let repo = Scratch::new();
        let tools = Scratch::new();
        let good = Overrides {
            python: Some(tools.file("python.exe")),
            typst: Some(tools.file("typst.exe")),
            tinytex_bin: Some(tools.folder("tinytex")),
        };
        let runtime = development(&repo.0, &good);
        assert_eq!(runtime.worker.unwrap().python, tools.0.join("python.exe"));
        assert_eq!(runtime.typst, Ok(tools.0.join("typst.exe")));
        assert_eq!(runtime.tinytex_bin, Some(tools.0.join("tinytex")));

        let missing = tools.0.join("missing");
        let bad = Overrides {
            python: Some(missing.join("python.exe")),
            typst: Some(missing.join("typst.exe")),
            tinytex_bin: Some(missing.clone()),
        };
        let runtime = development(&repo.0, &bad);
        assert_eq!(
            runtime.worker,
            Err(format!(
                "Python was not found at {}.",
                missing.join("python.exe").display()
            ))
        );
        assert_eq!(
            runtime.typst,
            Err(format!(
                "MATHIMATOR_TYPST is not a file: {}",
                missing.join("typst.exe").display()
            ))
        );
        assert_eq!(runtime.tinytex_bin, None);
        assert_eq!(
            runtime.tex_error,
            Some(format!(
                "Formulas and axis labels cannot render: MATHIMATOR_TINYTEX_BIN is not a folder ({}).",
                missing.display()
            ))
        );
    }

    fn runtime(tinytex_bin: Option<PathBuf>) -> Runtime {
        Runtime {
            worker: Ok(WorkerFiles {
                python: PathBuf::from("C:\\rt\\python\\python.exe"),
                base_python: None,
                source: PathBuf::from("C:\\rt\\worker"),
                sample: PathBuf::from("C:\\rt\\sample.json"),
            }),
            typst: Err("none".to_owned()),
            tinytex_bin,
            tex_error: None,
            work_dir: PathBuf::from("C:\\Users\\me\\AppData\\Local\\com.mathimator.desktop"),
            notices: installed_notices(None),
        }
    }

    fn envs(command: &Command) -> Vec<(String, Option<String>)> {
        command
            .get_envs()
            .map(|(key, value)| {
                (
                    key.to_string_lossy().into_owned(),
                    value.map(|value| value.to_string_lossy().into_owned()),
                )
            })
            .collect()
    }

    // The paths are Windows paths: on Unix `C:` holds the PATH separator, so join_paths refuses them.
    #[cfg(windows)]
    #[test]
    fn children_lose_inherited_python_and_tex_settings_and_get_tinytex_first_on_path() {
        let runtime = runtime(Some(PathBuf::from("C:\\rt\\tinytex\\bin\\windows")));
        let inherited = [
            ("PYTHONHOME", "C:\\Python27"),
            ("PythonStartup", "C:\\startup.py"),
            ("PYTHONPATH", "C:\\elsewhere"),
            ("TEXMFHOME", "C:\\texmf"),
            ("texmfcnf", "C:\\cnf"),
            ("TEXINPUTS", "C:\\inputs"),
            ("Path", "C:\\Windows;C:\\MiKTeX\\bin"),
            ("TEMP", "C:\\Temp"),
        ]
        .map(|(key, value)| (OsString::from(key), OsString::from(value)));
        let command = runtime.command_with("latex", inherited, false);
        let mut envs = envs(&command);
        envs.sort_by_key(|(key, _)| key.to_ascii_uppercase());
        let expected_path = env::join_paths([
            "C:\\rt\\tinytex\\bin\\windows",
            "C:\\Windows",
            "C:\\MiKTeX\\bin",
        ])
        .unwrap();
        assert_eq!(
            envs,
            [
                ("PATH", Some(expected_path.to_string_lossy().into_owned())),
                ("PYTHONHOME", None),
                ("PYTHONPATH", None),
                ("PythonStartup", None),
                ("TEXINPUTS", None),
                ("texmfcnf", None),
                ("TEXMFHOME", None),
            ]
            .map(|(key, value)| (key.to_owned(), value))
        );
        assert_eq!(command.get_current_dir(), Some(runtime.work_dir.as_path()));
    }

    #[test]
    fn release_children_also_lose_every_mathimator_variable() {
        for (key, debug, release) in [
            ("PYTHONHOME", true, true),
            ("texmfcnf", true, true),
            ("TEXINPUTS", true, true),
            ("MATHIMATOR_WIN32_JOBS", false, true),
            ("Mathimator_Skip_Process_Table", false, true),
            ("MATHIMATOR_TEX_ERROR", false, true),
            ("MATHIMATOR_SHELL_READY_FILE", false, true),
            ("MATHIMATORS", false, false),
            ("PATH", false, false),
        ] {
            let key = OsStr::new(key);
            assert_eq!(
                (is_cleaned(key, false), is_cleaned(key, true)),
                (debug, release),
                "{key:?}"
            );
        }
        let inherited = || {
            [
                ("MATHIMATOR_WIN32_JOBS", "0"),
                ("MATHIMATOR_TEX_ERROR", "stale"),
            ]
            .map(|(key, value)| (OsString::from(key), OsString::from(value)))
        };
        let runtime = runtime(None);
        assert!(envs(&runtime.command_with("python", inherited(), false)).is_empty());
        let mut command = runtime.command_with("python", inherited(), true);
        command.env("MATHIMATOR_TEX_ERROR", "fresh");
        let mut envs = envs(&command);
        envs.sort();
        assert_eq!(
            envs,
            [
                ("MATHIMATOR_TEX_ERROR", Some("fresh")),
                ("MATHIMATOR_WIN32_JOBS", None),
            ]
            .map(|(key, value)| (key.to_owned(), value.map(str::to_owned)))
        );
    }

    #[test]
    fn without_tinytex_children_keep_the_inherited_path() {
        let inherited = [(OsString::from("PATH"), OsString::from("C:\\Windows"))];
        assert!(envs(&runtime(None).command_with("latex", inherited, false)).is_empty());
    }

    #[test]
    fn python_children_run_with_dash_p_and_the_worker_on_pythonpath() {
        let runtime = runtime(None);
        let files = runtime.worker.clone().unwrap();
        let command = runtime.python_command(&files, "mathimator_worker");
        assert_eq!(command.get_program(), files.python);
        let args: Vec<&OsStr> = command.get_args().collect();
        assert_eq!(args, ["-P", "-m", "mathimator_worker"]);
        let envs = envs(&command);
        for (key, value) in [
            ("PYTHONPATH", "C:\\rt\\worker"),
            ("PYTHONUNBUFFERED", "1"),
            ("PYTHONIOENCODING", "utf-8"),
            ("PYTHONUTF8", "1"),
        ] {
            assert!(
                envs.contains(&(key.to_owned(), Some(value.to_owned()))),
                "{key}: {envs:?}"
            );
        }
        assert!(!envs
            .iter()
            .any(|(key, value)| key.eq_ignore_ascii_case("PYTHONHOME") && value.is_some()));
        assert!(
            !envs.iter().any(|(key, _)| key == VENV_LAUNCHER),
            "{envs:?}"
        );
        assert_eq!(command.get_current_dir(), Some(runtime.work_dir.as_path()));
    }

    /// A debug runtime on `python`, as `MATHIMATOR_PYTHON` or the repository's .venv gives it.
    fn debug_runtime(repo: &Scratch, python: &Path) -> Runtime {
        let overrides = Overrides {
            python: Some(python.to_owned()),
            ..Overrides::default()
        };
        development(&repo.0, &overrides)
    }

    /// A repository with a .venv launcher, an interpreter in each of its `base` and `other`
    /// folders, and the pyvenv.cfg `configs`, whose `{base}`, `{other}` and `{missing}` name those
    /// folders and one without an interpreter.
    fn venv_repo(configs: &[(&str, &str)]) -> Scratch {
        let repo = Scratch::new();
        repo.file(".venv/Scripts/python.exe");
        repo.file("base/python.exe");
        repo.file("other/python.exe");
        for (config, text) in configs {
            let mut text = text.to_string();
            for name in ["base", "other", "missing"] {
                let folder = repo.0.join(name);
                text = text.replace(&format!("{{{name}}}"), &folder.to_string_lossy());
            }
            fs::write(bundled(&repo.0, config), text).unwrap();
        }
        repo
    }

    #[test]
    fn a_debug_venv_launcher_runs_the_real_interpreter_that_pyvenv_cfg_names() {
        // The launcher reads the pyvenv.cfg beside it before the one a folder up, and the first
        // `home` in it, with or without spaces and CRs.
        let cases: [&[(&str, &str)]; 3] = [
            &[(
                ".venv/pyvenv.cfg",
                "home = {base}\nimplementation = CPython\nuv = 0.13.5\n",
            )],
            &[(
                ".venv/Scripts/pyvenv.cfg",
                "include-system-site-packages = false\r\nhome={base}\r\n",
            )],
            &[
                (".venv/pyvenv.cfg", "home = {other}\n"),
                (
                    ".venv/Scripts/pyvenv.cfg",
                    "home = {base}\nhome = {other}\n",
                ),
            ],
        ];
        for configs in cases {
            let repo = venv_repo(configs);
            let launcher = bundled(&repo.0, ".venv/Scripts/python.exe");
            let base = bundled(&repo.0, "base/python.exe");
            let runtime = debug_runtime(&repo, &launcher);
            let files = runtime.worker.clone().unwrap();
            let command = runtime.python_command(&files, "mathimator_worker");
            let args: Vec<&OsStr> = command.get_args().collect();
            assert_eq!(args, ["-P", "-m", "mathimator_worker"], "{configs:?}");
            assert_eq!(files.python, launcher, "{configs:?}");
            let told = (
                VENV_LAUNCHER.to_owned(),
                Some(launcher.to_string_lossy().into_owned()),
            );
            if cfg!(windows) {
                assert_eq!(files.base_python.as_ref(), Some(&base), "{configs:?}");
                assert_eq!(command.get_program(), base, "{configs:?}");
                assert!(envs(&command).contains(&told), "{configs:?}");
                let line = format!("python {} (runs {}),", launcher.display(), base.display());
                assert!(runtime.describe().contains(&line), "{configs:?}");
            } else {
                assert_eq!(files.base_python, None, "{configs:?}");
                assert_eq!(command.get_program(), launcher, "{configs:?}");
            }
        }
    }

    #[test]
    fn an_interpreter_that_is_no_venv_launcher_runs_as_it_is() {
        // No pyvenv.cfg, as for a real interpreter; a `home` the launcher does not read, because
        // the pyvenv.cfg it reads has none or its key is not lowercase; and a `home` with no
        // interpreter, which the launcher then reports itself.
        let cases: [&[(&str, &str)]; 5] = [
            &[],
            &[(".venv/pyvenv.cfg", "implementation = CPython\n")],
            &[
                (".venv/pyvenv.cfg", "home = {other}\n"),
                (".venv/Scripts/pyvenv.cfg", "implementation = CPython\n"),
            ],
            &[(".venv/pyvenv.cfg", "Home = {other}\n")],
            &[(".venv/pyvenv.cfg", "home = {missing}\n")],
        ];
        for configs in cases {
            let repo = venv_repo(configs);
            let python = bundled(&repo.0, ".venv/Scripts/python.exe");
            let runtime = debug_runtime(&repo, &python);
            let files = runtime.worker.clone().unwrap();
            assert_eq!(files.base_python, None, "{configs:?}");
            let command = runtime.python_command(&files, "mathimator_worker");
            assert_eq!(command.get_program(), python, "{configs:?}");
            let envs = envs(&command);
            assert!(
                !envs.iter().any(|(key, _)| key == VENV_LAUNCHER),
                "{envs:?}"
            );
        }
    }

    /// The repository's venv, started as the shell starts the worker: the process the shell
    /// spawns, and so puts in its Job Object, is the interpreter that runs the module, inside the
    /// venv, and it sees the venv's python as `sys.executable`, as the worker sees the `--python`
    /// it gets (D16). Runs only under `--ignored` with `MATHIMATOR_REAL_WORKER_TESTS=1`.
    #[test]
    #[ignore = "starts the repository's Python; needs MATHIMATOR_REAL_WORKER_TESTS=1"]
    fn the_repository_venv_runs_in_the_process_the_shell_spawns() {
        if env::var("MATHIMATOR_REAL_WORKER_TESTS").as_deref() != Ok("1") {
            return;
        }
        let repo = repo_root().expect("debug builds run the repository's venv");
        let runtime = development(&repo, &Overrides::default());
        let probe = Scratch::new();
        fs::write(
            probe.0.join("probe.py"),
            "import os, sys\nprint(os.getpid(), sys.prefix, sys.executable, sep='\\n')\n",
        )
        .unwrap();
        let files = WorkerFiles {
            source: probe.0.clone(),
            ..runtime.worker.clone().expect("the repository's venv")
        };
        let child = runtime
            .python_command(&files, "probe")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("python");
        let pid = child.id();
        let output = child.wait_with_output().expect("probe");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(output.status.success(), "{stderr}");
        let stdout = String::from_utf8(output.stdout).expect("UTF-8");
        let lines: Vec<&str> = stdout.lines().collect();
        assert_eq!(
            lines,
            [
                pid.to_string(),
                repo.join(".venv").display().to_string(),
                files.python.display().to_string(),
            ],
            "{stderr}"
        );
    }

    #[test]
    fn tex_programs_come_from_tinytex_when_there_is_one() {
        let bin = PathBuf::from("C:\\rt\\tinytex\\bin\\windows");
        assert_eq!(
            runtime(Some(bin.clone())).tex_program("latex"),
            bin.join(format!("latex{}", env::consts::EXE_SUFFIX))
        );
        assert_eq!(runtime(None).tex_program("latex"), PathBuf::from("latex"));
    }

    #[test]
    fn the_verbatim_prefix_is_dropped_from_drive_paths_only() {
        assert_eq!(
            plain(PathBuf::from(r"\\?\C:\Program Files\Mathimator")),
            PathBuf::from(r"C:\Program Files\Mathimator")
        );
        for kept in [
            r"\\?\UNC\server\share\Mathimator",
            r"C:\Program Files\Mathimator",
        ] {
            assert_eq!(plain(PathBuf::from(kept)), PathBuf::from(kept));
        }
    }

    #[test]
    fn bundle_paths_use_the_platform_separator() {
        assert_eq!(
            bundled(Path::new("C:\\rt"), "tinytex/bin/windows"),
            Path::new("C:\\rt")
                .join("tinytex")
                .join("bin")
                .join("windows")
        );
    }
}
