//! The equation glyph preview (contract C9.9): the LaTeX-to-Typst translator, which runs in the
//! shell with no Python child (D28), and the Typst compile of one formula per slot, where only the
//! newest generation may publish (F037).

use std::collections::HashMap;
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::lock;
use crate::protocol::{ErrorCode, ShellError};
use crate::runtime::hidden_command;

/// How long one Typst run may take.
const LIMIT: Duration = Duration::from_secs(10);
/// How often a running Typst is checked for its exit, a newer compile and the limit.
const POLL: Duration = Duration::from_millis(5);
/// The Typst the preview is written for (spec §1).
pub const VERSION: &str = "typst 0.15.0";
/// Generations stay below 2^53, so they are exact JavaScript numbers (C9.9).
const GENERATION_LIMIT: u64 = 1 << 53;
/// `\frac` and `\sqrt` groups nested deeper than this are unsupported. A stored formula has at most
/// 500 characters, so it nests at most 71 deep; the bound only keeps the recursion off the end of
/// the stack.
const MAX_DEPTH: usize = 100;
/// Why a compile finds no project folder (C9.9, G5.3).
const MOVED: &str = "The project folder was moved or deleted.";
/// The page around the translated math (spec §6).
const PAGE: &str = "#set page(width: auto, height: auto, margin: 8pt, fill: none)";
/// The commands Typst knows by the same name (spec §6).
const NAMES: [&str; 10] = [
    "sin", "cos", "tan", "log", "exp", "pi", "theta", "alpha", "beta", "gamma",
];

/// What `compile_equation_preview` returns (C9.9).
#[derive(Debug, PartialEq, Serialize)]
#[serde(tag = "status", rename_all = "lowercase")]
pub enum GlyphResult {
    Ok {
        #[serde(rename = "svgPath")]
        svg_path: String,
    },
    /// The formula is outside the supported subset.
    Unsupported,
    Error {
        message: String,
    },
    /// A newer generation for the slot, or a cancel, superseded this compile.
    Cancelled,
}

impl GlyphResult {
    pub fn error(message: impl Into<String>) -> Self {
        Self::Error {
            message: message.into(),
        }
    }
}

/// The shell's own check of a glyph request (C9.9): `slot` is a formula id, matching
/// `^[a-z][a-z0-9_]{0,31}$`, and `generation` a positive integer below 2^53.
pub fn check_arguments(slot: &str, generation: u64) -> Result<(), ShellError> {
    let mut bytes = slot.bytes();
    let is_slot = slot.len() <= 32
        && bytes.next().is_some_and(|first| first.is_ascii_lowercase())
        && bytes.all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_');
    if !is_slot {
        return Err(ShellError::new(
            ErrorCode::BadArgument,
            format!("The glyph slot {slot:?} is not a formula id."),
        ));
    }
    if !(1..GENERATION_LIMIT).contains(&generation) {
        return Err(ShellError::new(
            ErrorCode::BadArgument,
            format!("The glyph generation {generation} is not a positive integer below 2^53."),
        ));
    }
    Ok(())
}

/// The glyph preview. The shell runs Typst itself, never behind the worker (spec §6).
pub struct GlyphPreview {
    gate: Mutex<Gate>,
    /// The Typst the runtime resolved, or why there is none (C9.6).
    typst: Result<PathBuf, String>,
    /// Set once `typst --version` has reported [`VERSION`]. A failed check runs again on the next
    /// compile, so a Typst fixed meanwhile is picked up (F052).
    checked: AtomicBool,
}

impl GlyphPreview {
    pub fn new(typst: Result<PathBuf, String>) -> Self {
        Self {
            gate: Mutex::default(),
            typst,
            checked: AtomicBool::new(false),
        }
    }

    /// Compiles `latex` for the formula in `slot` to an SVG in `<project>/.mathimator/equation`
    /// (C9.9, D52). A compile whose generation is not above every one the slot has seen, or that
    /// a newer compile or a cancel supersedes before it publishes, is cancelled. Blocks until
    /// Typst ends.
    pub fn compile(&self, project: &Path, latex: &str, slot: &str, generation: u64) -> GlyphResult {
        let Some(claim) = lock(&self.gate).claim(slot, generation) else {
            return GlyphResult::Cancelled;
        };
        let Some(math) = translate(latex) else {
            return GlyphResult::Unsupported;
        };
        let typst = match self.typst() {
            Ok(typst) => typst,
            Err(reason) => return GlyphResult::error(format!("Typst is unavailable: {reason}")),
        };
        let folder = match equation_folder(project) {
            Ok(folder) => folder,
            Err(message) => return GlyphResult::error(message),
        };
        let name = format!("formula-{slot}-{generation}");
        let svg = folder.join(format!("{name}.svg"));
        let Some(svg_path) = svg.to_str().map(str::to_owned) else {
            return GlyphResult::error(format!("{} is not a Unicode path.", svg.display()));
        };
        let source = folder.join(format!("{name}.typ"));
        match self.write_source(&source, &math, slot, claim) {
            Ok(true) => {}
            Ok(false) => return GlyphResult::Cancelled,
            Err(err) => {
                return GlyphResult::error(format!("Could not write {}: {err}", source.display()))
            }
        }

        let live = || lock(&self.gate).is_live(slot, claim);
        let output = match run(typst_command(typst, &folder, &name), LIMIT, live) {
            Ok(Some(output)) => output,
            Ok(None) if live() => return GlyphResult::error("typst compile timed out"),
            Ok(None) => return GlyphResult::Cancelled,
            Err(err) => return GlyphResult::error(format!("Could not start Typst: {err}")),
        };
        if !live() {
            return GlyphResult::Cancelled;
        }
        if !output.status.success() {
            return GlyphResult::error(failure(&output));
        }
        if !fs::metadata(&svg).is_ok_and(|metadata| metadata.is_file() && metadata.len() > 0) {
            return GlyphResult::error("typst did not write an SVG");
        }
        if !self.publish(&folder, slot, generation, claim) {
            return GlyphResult::Cancelled;
        }
        GlyphResult::Ok { svg_path }
    }

    /// `cancel_equation_preview` (C9.9): no compile in `slot` at or below `generation` publishes,
    /// and the one running ends its Typst.
    pub fn cancel(&self, slot: &str, generation: u64) {
        lock(&self.gate).cancel(slot, generation);
    }

    /// Forgets every slot's generations, on a page load (C9.9). The reloaded UI counts from 1
    /// again. The compiles still running are stale and end their Typst.
    pub fn reset(&self) {
        lock(&self.gate).reset();
    }

    /// The Typst binary, once its version check has passed (F052).
    fn typst(&self) -> Result<&Path, String> {
        let typst = self.typst.as_ref().map_err(String::clone)?;
        if !self.checked.load(Ordering::Relaxed) {
            check_version(typst)?;
            self.checked.store(true, Ordering::Relaxed);
        }
        Ok(typst)
    }

    /// Writes the Typst document for `math` to `source` if `claim` is still live, and says
    /// whether it was. The gate stays locked meanwhile, as in `publish`, so a compile superseded
    /// before its write (during the version check, say) leaves no file, and the file of one
    /// superseded later is there for the newer compile's `publish` to delete.
    fn write_source(&self, source: &Path, math: &str, slot: &str, claim: u64) -> io::Result<bool> {
        let gate = lock(&self.gate);
        if !gate.is_live(slot, claim) {
            return Ok(false);
        }
        fs::write(source, document(math))?;
        Ok(true)
    }

    /// Deletes the slot's other formula files if `claim` is still live, and says whether it was.
    /// The gate stays locked meanwhile, so a newer compile of the slot cannot write its file
    /// before the deletion ends. Every other file of the slot is then an older one, or one left
    /// from before a page reload (F127, F169).
    fn publish(&self, folder: &Path, slot: &str, generation: u64, claim: u64) -> bool {
        let gate = lock(&self.gate);
        let live = gate.is_live(slot, claim);
        if live {
            remove_other_files(folder, slot, generation);
        }
        live
    }
}

/// The slot and generation gate (C9.9, F037). A slot is one formula's preview.
#[derive(Default)]
struct Gate {
    slots: HashMap<String, Slot>,
    /// The number of the last claim. Claims are numbered across page loads, so a compile from
    /// before a reload never passes for a later one with the same slot and generation.
    claims: u64,
}

#[derive(Default)]
struct Slot {
    /// The highest generation a compile or a cancel named. A compile at or below it is stale.
    latest: u64,
    /// The claim of the compile of `latest`, until it is cancelled.
    live: Option<u64>,
}

impl Gate {
    /// Claims `generation` in `slot` for a compile, which makes every older compile there stale.
    /// Returns the claim, or `None` when the generation is stale already.
    fn claim(&mut self, slot: &str, generation: u64) -> Option<u64> {
        let entry = self.slots.entry(slot.to_owned()).or_default();
        if generation <= entry.latest {
            return None;
        }
        self.claims += 1;
        entry.latest = generation;
        entry.live = Some(self.claims);
        Some(self.claims)
    }

    fn cancel(&mut self, slot: &str, generation: u64) {
        let entry = self.slots.entry(slot.to_owned()).or_default();
        if generation >= entry.latest {
            entry.latest = generation;
            entry.live = None;
        }
    }

    /// Whether the compile with `claim` may still publish.
    fn is_live(&self, slot: &str, claim: u64) -> bool {
        self.slots.get(slot).and_then(|entry| entry.live) == Some(claim)
    }

    fn reset(&mut self) {
        self.slots.clear();
    }
}

/// Translates the supported LaTeX subset to Typst math (spec §6), or returns `None` when any part
/// of `latex` is outside it: there is no partial translation. worker/mathimator_worker/equation.py
/// is the reference, and both pass tests/fixtures/equation/translate-cases.json.
pub fn translate(latex: &str) -> Option<String> {
    Parser {
        source: latex,
        index: 0,
    }
    .sequence(None, 0)
}

/// The Typst document for translated math.
fn document(math: &str) -> String {
    format!("{PAGE}\n$ {math} $\n")
}

struct Parser<'a> {
    source: &'a str,
    /// The byte index of the next character. Everything before it is ASCII.
    index: usize,
}

impl Parser<'_> {
    fn peek(&self) -> Option<u8> {
        self.source.as_bytes().get(self.index).copied()
    }

    /// Translates pieces up to `closing`, or to the end when it is `None`, inside `depth` groups.
    fn sequence(&mut self, closing: Option<u8>, depth: usize) -> Option<String> {
        let mut math = String::new();
        while let Some(next) = self.peek().filter(|&next| Some(next) != closing) {
            let piece = self.piece(next, depth)?;
            // Typst reads a run such as mc or x2 as one name. A space keeps each letter, digit
            // and command its own atom, as in LaTeX: mc^2 becomes m c^2 (D10).
            let is_atom = |character: char| character.is_ascii_alphanumeric();
            if math.ends_with(is_atom) && piece.starts_with(is_atom) {
                math.push(' ');
            }
            math.push_str(&piece);
        }
        Some(math)
    }

    /// The piece that starts with `next`.
    fn piece(&mut self, next: u8, depth: usize) -> Option<String> {
        let start = self.index;
        if is_space(next) {
            while self.peek().is_some_and(is_space) {
                self.index += 1;
            }
            return Some(self.source[start..self.index].to_owned());
        }
        self.index += 1;
        match next {
            b'\\' => self.command(depth),
            _ if next.is_ascii_alphanumeric() || b"^_+-=()[]".contains(&next) => {
                Some(char::from(next).to_string())
            }
            _ => None,
        }
    }

    /// The command after a backslash.
    fn command(&mut self, depth: usize) -> Option<String> {
        let source = self.source;
        let start = self.index;
        while self.peek().is_some_and(|byte| byte.is_ascii_alphabetic()) {
            self.index += 1;
        }
        match &source[start..self.index] {
            name if NAMES.contains(&name) => Some(name.to_owned()),
            "frac" => {
                let numerator = self.group(depth)?;
                let denominator = self.group(depth)?;
                Some(format!("frac({numerator}, {denominator})"))
            }
            "sqrt" => Some(format!("sqrt({})", self.group(depth)?)),
            _ => None,
        }
    }

    /// A `{...}` argument.
    fn group(&mut self, depth: usize) -> Option<String> {
        if self.peek() != Some(b'{') || depth == MAX_DEPTH {
            return None;
        }
        self.index += 1;
        let body = self.sequence(Some(b'}'), depth + 1)?;
        if self.peek() != Some(b'}') {
            return None;
        }
        self.index += 1;
        Some(body)
    }
}

fn is_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\r' | b'\n')
}

/// `<project>/.mathimator/equation` (D52). It is made inside the project folder, never the folder
/// itself, so a project moved or deleted meanwhile is not brought back (G5.3).
fn equation_folder(project: &Path) -> Result<PathBuf, String> {
    let mut folder = project.to_owned();
    for name in [".mathimator", "equation"] {
        folder.push(name);
        match fs::create_dir(&folder) {
            Ok(()) => {}
            Err(err) if err.kind() == io::ErrorKind::AlreadyExists && folder.is_dir() => {}
            Err(err) if err.kind() == io::ErrorKind::NotFound => return Err(MOVED.to_owned()),
            Err(err) => return Err(format!("Could not create {}: {err}", folder.display())),
        }
    }
    Ok(folder)
}

/// `typst compile` of `<name>.typ` to `<name>.svg` in `folder`, with no console window. Typst
/// reads `{p}`, `{0p}` and `{n}` in its output path as a page template, so both paths are names
/// in the working folder, which no project path can change (G4.8). The embedded fonts cover the
/// subset, and skipping the system fonts saves their scan and keeps the glyph the same on every
/// PC (F053).
fn typst_command(typst: &Path, folder: &Path, name: &str) -> Command {
    let mut command = hidden_command(typst);
    command
        .args(["compile", "--ignore-system-fonts"])
        .arg(format!("{name}.typ"))
        .arg(format!("{name}.svg"))
        .current_dir(folder);
    command
}

/// Checks that `typst` runs, without a console window, and is [`VERSION`] (F052).
fn check_version(typst: &Path) -> Result<(), String> {
    let mut command = hidden_command(typst);
    command.arg("--version");
    let output = run(command, LIMIT, || true)
        .map_err(|err| format!("could not run {}: {err}", typst.display()))?
        .ok_or_else(|| {
            format!(
                "typst --version did not finish within {} s",
                LIMIT.as_secs()
            )
        })?;
    if !output.status.success() {
        return Err(format!("typst --version failed: {}", failure(&output)));
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let line = text.lines().next().unwrap_or("").trim();
    if line.starts_with(VERSION) {
        Ok(())
    } else {
        Err(format!("{VERSION} is required, found {line:?}"))
    }
}

/// Runs `command` with its output read until it exits, or ends it once `go_on` turns false or
/// `limit` passes, and then returns `None`. An ended process's readers are left to finish on
/// their own: a process it started, such as the real Typst behind a shim, may hold its pipes open
/// for as long as it runs (F038).
fn run(
    mut command: Command,
    limit: Duration,
    go_on: impl Fn() -> bool,
) -> io::Result<Option<Output>> {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let stdout = child.stdout.take().map(read_to_end);
    let stderr = child.stderr.take().map(read_to_end);
    let deadline = Instant::now() + limit;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if go_on() && Instant::now() < deadline => thread::sleep(POLL),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Ok(None);
            }
        }
    };
    Ok(Some(Output {
        status,
        stdout: joined(stdout),
        stderr: joined(stderr),
    }))
}

/// Reads `pipe` to its end on a thread of its own, so the child never blocks on a full pipe.
fn read_to_end(mut pipe: impl Read + Send + 'static) -> JoinHandle<Vec<u8>> {
    thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = pipe.read_to_end(&mut bytes);
        bytes
    })
}

fn joined(reader: Option<JoinHandle<Vec<u8>>>) -> Vec<u8> {
    reader
        .and_then(|reader| reader.join().ok())
        .unwrap_or_default()
}

/// What a failed Typst run reported, clipped: its stderr, else its stdout, else its exit status.
fn failure(output: &Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);
    let text = if stderr.trim().is_empty() {
        stdout.trim()
    } else {
        stderr.trim()
    };
    if text.is_empty() {
        format!("typst exited with {}", output.status)
    } else {
        clip(text, 800)
    }
}

/// Deletes the `formula-<slot>-<generation>.typ` and `.svg` files of `slot` other than those of
/// `keep`. A file that cannot be deleted is left for the next compile or Clear cache.
fn remove_other_files(folder: &Path, slot: &str, keep: u64) {
    let Ok(entries) = fs::read_dir(folder) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let generation = name.to_str().and_then(|name| generation_in(name, slot));
        if generation.is_some_and(|generation| generation != keep) {
            let _ = fs::remove_file(entry.path());
        }
    }
}

/// The generation in `name` when it is a `formula-<slot>-<generation>.typ` or `.svg` file.
fn generation_in(name: &str, slot: &str) -> Option<u64> {
    let stem = name
        .strip_suffix(".typ")
        .or_else(|| name.strip_suffix(".svg"))?;
    let digits = stem
        .strip_prefix("formula-")?
        .strip_prefix(slot)?
        .strip_prefix('-')?;
    if digits.bytes().all(|byte| byte.is_ascii_digit()) {
        digits.parse().ok()
    } else {
        None
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

#[cfg(test)]
mod tests {
    use std::ffi::OsStr;

    use serde::Deserialize;
    use serde_json::json;

    use super::*;

    /// One case of tests/fixtures/equation/translate-cases.json; `None` is unsupported.
    #[derive(Deserialize)]
    struct Case {
        latex: String,
        typst: Option<String>,
    }

    #[test]
    fn the_translator_passes_the_shared_cases() {
        let cases: Vec<Case> = serde_json::from_str(include_str!(
            "../../../tests/fixtures/equation/translate-cases.json"
        ))
        .unwrap();
        assert!(!cases.is_empty());
        let wrong: Vec<String> = cases
            .iter()
            .filter_map(|case| {
                let translated = translate(&case.latex);
                (translated != case.typst).then(|| {
                    format!(
                        "{:?}: expected {:?}, got {translated:?}",
                        case.latex, case.typst
                    )
                })
            })
            .collect();
        assert!(wrong.is_empty(), "{wrong:#?}");
    }

    #[test]
    fn the_document_puts_the_math_on_the_phase_3_page() {
        assert_eq!(
            document("y = sin(x)"),
            "#set page(width: auto, height: auto, margin: 8pt, fill: none)\n$ y = sin(x) $\n"
        );
    }

    #[test]
    fn groups_nested_deeper_than_the_bound_are_unsupported() {
        let nested = |depth: usize| format!("{}x{}", r"\sqrt{".repeat(depth), "}".repeat(depth));
        assert_eq!(
            translate(&nested(MAX_DEPTH)),
            Some(format!(
                "{}x{}",
                "sqrt(".repeat(MAX_DEPTH),
                ")".repeat(MAX_DEPTH)
            ))
        );
        assert_eq!(translate(&nested(MAX_DEPTH + 1)), None);
        assert_eq!(translate(&nested(100_000)), None);
    }

    #[test]
    fn a_newer_generation_wins_its_slot() {
        let mut gate = Gate::default();
        let older = gate.claim("eq_1", 3).unwrap();
        let newer = gate.claim("eq_1", 5).unwrap();
        assert!(!gate.is_live("eq_1", older));
        assert!(gate.is_live("eq_1", newer));
        assert_eq!(gate.claim("eq_1", 4), None);
        assert_eq!(gate.claim("eq_1", 5), None);
    }

    #[test]
    fn slots_are_gated_independently() {
        let mut gate = Gate::default();
        let source = gate.claim("eq_1", 7).unwrap();
        let target = gate.claim("eq_2", 2).unwrap();
        gate.cancel("eq_2", 2);
        assert!(gate.is_live("eq_1", source));
        assert!(!gate.is_live("eq_2", target));
        assert!(gate.claim("eq_3", 1).is_some());
    }

    #[test]
    fn a_cancelled_generation_is_stale() {
        let mut gate = Gate::default();
        let running = gate.claim("eq_1", 4).unwrap();
        gate.cancel("eq_1", 4);
        assert!(!gate.is_live("eq_1", running));
        assert_eq!(gate.claim("eq_1", 4), None);
        // A cancel can arrive before its compile.
        gate.cancel("eq_1", 6);
        assert_eq!(gate.claim("eq_1", 6), None);
        // An older generation's cancel leaves the newer compile running.
        let newer = gate.claim("eq_1", 9).unwrap();
        gate.cancel("eq_1", 8);
        assert!(gate.is_live("eq_1", newer));
    }

    #[test]
    fn after_a_page_load_generation_1_compiles_again() {
        let mut gate = Gate::default();
        let before = gate.claim("eq_1", 50).unwrap();
        let same_numbers = gate.claim("eq_2", 1).unwrap();
        gate.reset();
        let after = gate.claim("eq_1", 1).unwrap();
        let again = gate.claim("eq_2", 1).unwrap();
        assert!(gate.is_live("eq_1", after));
        assert!(gate.is_live("eq_2", again));
        assert!(!gate.is_live("eq_1", before));
        assert!(!gate.is_live("eq_2", same_numbers));
    }

    #[test]
    fn slots_and_generations_are_checked() {
        let longest = "a".repeat(32);
        for slot in ["eq_1", "a", "formula_2", longest.as_str()] {
            assert_eq!(check_arguments(slot, 1), Ok(()), "{slot}");
        }
        let too_long = "a".repeat(33);
        for slot in [
            "",
            "1eq",
            "Eq",
            "eq-1",
            "eq 1",
            "é",
            "../eq",
            too_long.as_str(),
        ] {
            assert_eq!(
                check_arguments(slot, 1).unwrap_err().code,
                ErrorCode::BadArgument,
                "{slot}"
            );
        }
        assert_eq!(check_arguments("eq_1", GENERATION_LIMIT - 1), Ok(()));
        for generation in [0, GENERATION_LIMIT, u64::MAX] {
            assert_eq!(
                check_arguments("eq_1", generation).unwrap_err().code,
                ErrorCode::BadArgument,
                "{generation}"
            );
        }
    }

    #[test]
    fn glyph_results_are_tagged_by_status() {
        let results = [
            GlyphResult::Ok {
                svg_path: "C:\\p\\.mathimator\\equation\\formula-eq_1-3.svg".to_owned(),
            },
            GlyphResult::Unsupported,
            GlyphResult::error("typst compile timed out"),
            GlyphResult::Cancelled,
        ]
        .map(|result| serde_json::to_value(result).unwrap());
        assert_eq!(
            results,
            [
                json!({"status": "ok", "svgPath": "C:\\p\\.mathimator\\equation\\formula-eq_1-3.svg"}),
                json!({"status": "unsupported"}),
                json!({"status": "error", "message": "typst compile timed out"}),
                json!({"status": "cancelled"}),
            ]
        );
    }

    fn scratch(name: &str) -> PathBuf {
        let folder =
            std::env::temp_dir().join(format!("mathimator-{name}-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&folder);
        fs::create_dir_all(&folder).unwrap();
        folder
    }

    /// The names of the files in `folder`, sorted.
    fn names_in(folder: &Path) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(folder)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().into_string().unwrap())
            .collect();
        names.sort();
        names
    }

    /// A glyph preview whose gate is driven by hand, with no Typst.
    fn gated() -> GlyphPreview {
        GlyphPreview::new(Err("no Typst in this test".to_owned()))
    }

    #[test]
    fn a_superseded_or_cancelled_compile_writes_no_source() {
        let folder = scratch("glyph-source");
        let glyph = gated();
        let superseded = lock(&glyph.gate).claim("eq_1", 5).unwrap();
        assert!(lock(&glyph.gate).claim("eq_1", 6).is_some());
        let cancelled = lock(&glyph.gate).claim("eq_2", 1).unwrap();
        glyph.cancel("eq_2", 1);
        let live = lock(&glyph.gate).claim("eq_3", 1).unwrap();
        let written =
            [("eq_1", superseded), ("eq_2", cancelled), ("eq_3", live)].map(|(slot, claim)| {
                let source = folder.join(format!("formula-{slot}.typ"));
                let wrote = glyph.write_source(&source, "x", slot, claim).unwrap();
                (wrote, source.is_file())
            });
        let source = fs::read_to_string(folder.join("formula-eq_3.typ"));
        let _ = fs::remove_dir_all(&folder);
        assert_eq!(written, [(false, false), (false, false), (true, true)]);
        assert_eq!(source.unwrap(), document("x"));
    }

    #[test]
    fn only_the_live_compile_publishes_and_removes_the_slots_other_files() {
        let folder = scratch("glyph-publish");
        let pair = |generation: u64| {
            ["svg", "typ"].map(|extension| format!("formula-eq_1-{generation}.{extension}"))
        };
        for name in [5, 6, 7].map(pair).concat() {
            fs::write(folder.join(name), "x").unwrap();
        }
        let glyph = gated();
        let older = lock(&glyph.gate).claim("eq_1", 5).unwrap();
        let newer = lock(&glyph.gate).claim("eq_1", 6).unwrap();
        let superseded = glyph.publish(&folder, "eq_1", 5, older);
        let after_superseded = names_in(&folder);
        let published = glyph.publish(&folder, "eq_1", 6, newer);
        let after_published = names_in(&folder);
        fs::write(folder.join("formula-eq_1-7.svg"), "x").unwrap();
        let running = lock(&glyph.gate).claim("eq_1", 7).unwrap();
        glyph.cancel("eq_1", 7);
        let cancelled = glyph.publish(&folder, "eq_1", 7, running);
        let after_cancelled = names_in(&folder);
        let _ = fs::remove_dir_all(&folder);

        assert!(!superseded);
        assert_eq!(
            after_superseded,
            [5, 6, 7].map(pair).concat(),
            "a superseded compile removed a file"
        );
        assert!(published);
        assert_eq!(after_published, pair(6));
        assert!(!cancelled);
        assert_eq!(
            after_cancelled,
            [
                "formula-eq_1-6.svg",
                "formula-eq_1-6.typ",
                "formula-eq_1-7.svg"
            ]
        );
    }

    #[test]
    fn the_equation_folder_is_made_only_inside_an_existing_project() {
        let scratch = scratch("equation-folder");
        let project = scratch.join("{n} drafts").join("demo");
        let missing = equation_folder(&project);
        let resurrected = project.exists();
        fs::create_dir_all(&project).unwrap();
        let made = equation_folder(&project);
        let again = equation_folder(&project);
        let _ = fs::remove_dir_all(&scratch);
        assert_eq!(missing, Err(MOVED.to_owned()));
        assert!(!resurrected, "the project folder was made");
        let folder = project.join(".mathimator").join("equation");
        assert_eq!(made, Ok(folder.clone()));
        assert_eq!(again, Ok(folder));
    }

    #[test]
    fn typst_gets_file_names_in_the_equation_folder() {
        let folder = Path::new("D:\\Slides\\{n} drafts\\{p}\\.mathimator\\equation");
        let command = typst_command(Path::new("C:\\rt\\typst.exe"), folder, "formula-eq_1-3");
        let args: Vec<&OsStr> = command.get_args().collect();
        assert_eq!(
            args,
            [
                "compile",
                "--ignore-system-fonts",
                "formula-eq_1-3.typ",
                "formula-eq_1-3.svg",
            ]
        );
        assert_eq!(command.get_current_dir(), Some(folder));
    }

    #[test]
    fn only_the_slots_other_formula_files_are_removed() {
        let folder = scratch("equation-cleanup");
        let names = [
            "formula-eq_1-2.typ",
            "formula-eq_1-2.svg",
            "formula-eq_1-50.svg",
            "formula-eq_1-3.typ",
            "formula-eq_1-3.svg",
            "formula-eq_1-x.svg",
            "formula-eq_1-4.png",
            "formula-eq_10-1.svg",
            "formula-eq_2-1.svg",
            "notes.txt",
        ];
        for name in names {
            fs::write(folder.join(name), "x").unwrap();
        }
        remove_other_files(&folder, "eq_1", 3);
        let left = names_in(&folder);
        let _ = fs::remove_dir_all(&folder);
        assert_eq!(
            left,
            [
                "formula-eq_1-3.svg",
                "formula-eq_1-3.typ",
                "formula-eq_1-4.png",
                "formula-eq_1-x.svg",
                "formula-eq_10-1.svg",
                "formula-eq_2-1.svg",
                "notes.txt",
            ]
        );
    }

    #[test]
    fn clip_ends_at_a_character_with_an_ellipsis() {
        assert_eq!(clip("abc", 3), "abc");
        assert_eq!(clip("abcd", 3), "abc…");
        assert_eq!(clip("aé", 2), "a…");
    }

    #[cfg(windows)]
    mod windows {
        use std::os::windows::process::ExitStatusExt;
        use std::process::ExitStatus;

        use super::*;

        fn output(code: u32, stdout: &str, stderr: &str) -> Output {
            Output {
                status: ExitStatus::from_raw(code),
                stdout: stdout.as_bytes().to_vec(),
                stderr: stderr.as_bytes().to_vec(),
            }
        }

        #[test]
        fn a_failure_reports_stderr_then_stdout_then_the_exit_status() {
            assert_eq!(
                failure(&output(1, "compiled", "error: unknown variable: q\n")),
                "error: unknown variable: q"
            );
            assert_eq!(
                failure(&output(1, "error: on stdout\n", " \n")),
                "error: on stdout"
            );
            assert_eq!(
                failure(&output(3, "", "")),
                "typst exited with exit code: 3"
            );
        }

        #[test]
        fn run_returns_the_output_of_a_process_that_exits() {
            let mut command = hidden_command("cmd");
            command.args(["/C", "echo typst 0.15.0"]);
            let output = run(command, LIMIT, || true).unwrap().unwrap();
            assert!(output.status.success());
            assert_eq!(
                String::from_utf8_lossy(&output.stdout).trim(),
                "typst 0.15.0"
            );
        }

        /// A process that runs for half a minute unless it is ended.
        fn slow() -> Command {
            let mut command = hidden_command("ping");
            command.args(["-n", "30", "127.0.0.1"]);
            command
        }

        #[test]
        fn run_ends_a_process_once_its_compile_goes_stale() {
            let started = Instant::now();
            let stale_at = started + Duration::from_millis(100);
            let ran = run(slow(), LIMIT, || Instant::now() < stale_at).unwrap();
            assert!(ran.is_none());
            assert!(started.elapsed() < Duration::from_secs(5));
        }

        #[test]
        fn run_ends_a_process_at_the_limit() {
            let started = Instant::now();
            let ran = run(slow(), Duration::from_millis(100), || true).unwrap();
            assert!(ran.is_none());
            assert!(started.elapsed() < Duration::from_secs(5));
        }

        #[test]
        fn run_returns_once_it_ends_a_process_whose_child_holds_its_pipes() {
            // The ping outlives the ended cmd by three seconds, with the pipes open.
            let mut command = hidden_command("cmd");
            command.args(["/C", "ping", "-n", "4", "127.0.0.1"]);
            let started = Instant::now();
            let stale_at = started + Duration::from_millis(100);
            let ran = run(command, LIMIT, || Instant::now() < stale_at).unwrap();
            assert!(ran.is_none());
            assert!(started.elapsed() < Duration::from_secs(2));
        }

        /// A stand-in for Typst 0.15.0: it checks that the source is in its working folder,
        /// fails on a formula with z in it, and otherwise writes the SVG there.
        const FAKE_TYPST: &str = "@echo off\r\n\
            if \"%~1\"==\"--version\" (echo typst 0.15.0 fake& exit /b 0)\r\n\
            if not exist \"%~3\" (echo error: %~3 is not in the working folder 1>&2& exit /b 1)\r\n\
            findstr /c:\"z\" \"%~3\" >nul && (echo error: unknown variable: z 1>&2& exit /b 1)\r\n\
            echo ^<svg/^>> \"%~4\"\r\n";

        #[test]
        fn a_compile_publishes_its_svg_and_removes_the_slots_older_files() {
            let scratch = scratch("glyph-compile");
            let typst = scratch.join("typst.cmd");
            fs::write(&typst, FAKE_TYPST).unwrap();
            let project = scratch.join("{n} drafts").join("demo");
            fs::create_dir_all(&project).unwrap();
            let folder = project.join(".mathimator").join("equation");
            let glyph = GlyphPreview::new(Ok(typst));

            let first = glyph.compile(&project, "E = mc^2", "eq_1", 1);
            let other_slot = glyph.compile(&project, r"\sqrt{x}", "eq_2", 2);
            let unsupported = glyph.compile(&project, r"\int_0^1 x dx", "eq_1", 3);
            let failed = glyph.compile(&project, "z", "eq_1", 4);
            let after_failure = ["formula-eq_1-1.svg", "formula-eq_1-4.typ"]
                .map(|name| folder.join(name).is_file());
            let stale = glyph.compile(&project, "x", "eq_1", 4);
            let second = glyph.compile(&project, "y = mx + b", "eq_1", 5);
            let source = fs::read_to_string(folder.join("formula-eq_1-5.typ"));
            let left = names_in(&folder);
            let _ = fs::remove_dir_all(&scratch);

            let svg = |name: &str| GlyphResult::Ok {
                svg_path: folder.join(name).to_str().unwrap().to_owned(),
            };
            assert_eq!(first, svg("formula-eq_1-1.svg"));
            assert_eq!(other_slot, svg("formula-eq_2-2.svg"));
            assert_eq!(unsupported, GlyphResult::Unsupported);
            assert_eq!(failed, GlyphResult::error("error: unknown variable: z"));
            assert_eq!(after_failure, [true, true], "a failure removed a file");
            assert_eq!(stale, GlyphResult::Cancelled);
            assert_eq!(second, svg("formula-eq_1-5.svg"));
            assert_eq!(source.unwrap(), document("y = m x + b"));
            assert_eq!(
                left,
                [
                    "formula-eq_1-5.svg",
                    "formula-eq_1-5.typ",
                    "formula-eq_2-2.svg",
                    "formula-eq_2-2.typ",
                ]
            );
        }

        #[test]
        fn a_compile_in_a_moved_project_does_not_bring_it_back() {
            let scratch = scratch("glyph-moved");
            let typst = scratch.join("typst.cmd");
            fs::write(&typst, FAKE_TYPST).unwrap();
            let project = scratch.join("moved");
            let result = GlyphPreview::new(Ok(typst)).compile(&project, "x", "eq_1", 1);
            let resurrected = project.exists();
            let _ = fs::remove_dir_all(&scratch);
            assert_eq!(result, GlyphResult::error(MOVED));
            assert!(!resurrected);
        }

        #[test]
        fn a_missing_or_wrong_typst_is_unavailable() {
            let scratch = scratch("glyph-typst");
            let wrong = scratch.join("typst.cmd");
            fs::write(&wrong, "@echo off\r\necho typst 0.14.2\r\n").unwrap();
            let missing = GlyphPreview::new(Err("typst 0.15.0 was not found on PATH".to_owned()))
                .compile(&scratch, "x", "eq_1", 1);
            let old = GlyphPreview::new(Ok(wrong)).compile(&scratch, "x", "eq_1", 1);
            let _ = fs::remove_dir_all(&scratch);
            assert_eq!(
                missing,
                GlyphResult::error("Typst is unavailable: typst 0.15.0 was not found on PATH")
            );
            assert_eq!(
                old,
                GlyphResult::error(
                    "Typst is unavailable: typst 0.15.0 is required, found \"typst 0.14.2\""
                )
            );
        }
    }
}
