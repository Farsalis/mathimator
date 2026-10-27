//! Temp projects (spec §6): the folders the shell makes in %TEMP% for the launch project,
//! New and each Sample copy, their marker, the "touched" check, and pruning.

use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::{sha256, write_atomically};

/// The `kind` of every marker.
const KIND: &str = "mathimator-temp";
/// Pruning keeps a temp project for at least this long.
const PRUNE_AGE_MS: u64 = 24 * 60 * 60 * 1000;

/// `<folder>\.mathimator\temp-project.json`, which makes a folder a temp project. The worker
/// leaves it alone, and Save As does not copy it.
#[derive(Debug, PartialEq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Marker {
    kind: String,
    /// The SHA-256 of the folder's project.json as first created.
    seed_sha256: String,
    created_ms: u64,
}

impl Marker {
    pub fn new(seed_sha256: String, created_ms: u64) -> Self {
        Self {
            kind: KIND.to_owned(),
            seed_sha256,
            created_ms,
        }
    }

    /// The marker of `folder`, if it has one.
    pub fn read(folder: &Path) -> Option<Self> {
        let bytes = fs::read(marker_file(folder)).ok()?;
        serde_json::from_slice::<Self>(&bytes)
            .ok()
            .filter(|marker| marker.kind == KIND)
    }

    /// Writes the marker into `folder`, whose `.mathimator` folder the worker made when it bound
    /// the folder.
    pub fn write(&self, folder: &Path) -> io::Result<()> {
        write_atomically(&marker_file(folder), &serde_json::to_vec(self)?)
    }

    /// Whether the project in `folder` holds work: its project.json no longer hashes to the seed,
    /// or an export was published. A project.json that cannot be read counts as changed.
    pub fn is_touched(&self, folder: &Path) -> bool {
        seed_of(folder).ok().as_ref() != Some(&self.seed_sha256) || has_exports(folder)
    }
}

fn marker_file(folder: &Path) -> PathBuf {
    folder.join(".mathimator").join("temp-project.json")
}

/// The SHA-256 of the project.json in `folder`, the seed of a temp project made a moment ago.
pub fn seed_of(folder: &Path) -> io::Result<String> {
    fs::read(folder.join("project.json")).map(|bytes| sha256::hex_digest(&bytes))
}

/// The creation time in the name the shell gives temp folders, `mathimator-<pid>-<ms>`, or `None`
/// for any other name.
pub fn created_ms_in_name(folder: &Path) -> Option<u64> {
    let digits = |text: &str| !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit());
    let (pid, millis) = folder
        .file_name()?
        .to_str()?
        .strip_prefix("mathimator-")?
        .split_once('-')?;
    if digits(pid) && digits(millis) {
        millis.parse().ok()
    } else {
        None
    }
}

/// Deletes the temp projects in `temp_root` that hold nothing to keep, and logs each deletion.
/// Pruning only considers `mathimator-<pid>-<ms>` folders that are at least 24 h old, that no
/// worker has locked, and that are not in `keep`.
///
/// - A marked folder must be untouched: its project.json still hashes to the seed, and it
///   published no export. Its age is the marker's `createdMs`.
/// - An unmarked folder must have published no export, whatever its project.json holds. Its age
///   comes from its name. Older builds wrote no marker, and this build leaves a folder unmarked
///   when a late reply binds it or the marker cannot be written.
pub fn prune(temp_root: &Path, keep: &[PathBuf], now_ms: u64, log: &dyn Fn(&str)) {
    let Ok(entries) = fs::read_dir(temp_root) else {
        return;
    };
    let keep: Vec<PathBuf> = keep
        .iter()
        .filter_map(|folder| fs::canonicalize(folder).ok())
        .collect();
    for entry in entries.flatten() {
        let folder = entry.path();
        // Links are never followed, so pruning cannot reach outside the temp folder.
        if !entry.file_type().is_ok_and(|kind| kind.is_dir()) {
            continue;
        }
        let kept = match fs::canonicalize(&folder) {
            Ok(canonical) => keep.contains(&canonical),
            Err(_) => true,
        };
        if kept || !is_prunable(&folder, now_ms) {
            continue;
        }
        match fs::remove_dir_all(&folder) {
            Ok(()) => log(&format!("Pruned the temp project {}.", folder.display())),
            Err(err) => log(&format!(
                "Could not prune the temp project {}: {err}.",
                folder.display()
            )),
        }
    }
}

/// Whether the rules of [`prune`] allow deleting `folder`, apart from `keep`. The lock is tested
/// last, just before the deletion.
fn is_prunable(folder: &Path, now_ms: u64) -> bool {
    let Some(named_ms) = created_ms_in_name(folder) else {
        return false;
    };
    let is_old = |created_ms: u64| now_ms.saturating_sub(created_ms) >= PRUNE_AGE_MS;
    let unused = match Marker::read(folder) {
        Some(marker) => is_old(marker.created_ms) && !marker.is_touched(folder),
        None => is_old(named_ms) && !has_exports(folder),
    };
    unused && !is_locked(folder)
}

fn lock_file(folder: &Path) -> PathBuf {
    folder.join(".mathimator").join("lock")
}

fn has_exports(folder: &Path) -> bool {
    holds_files(&folder.join(".mathimator").join("exports"))
}

/// Whether `folder`, or a folder inside it, holds a file. Anything that cannot be read counts as
/// a file, so that nothing is deleted on a guess.
fn holds_files(folder: &Path) -> bool {
    let entries = match fs::read_dir(folder) {
        Ok(entries) => entries,
        Err(err) => return err.kind() != io::ErrorKind::NotFound,
    };
    entries.into_iter().any(|entry| {
        let Ok(entry) = entry else {
            return true;
        };
        match entry.file_type() {
            Ok(kind) if kind.is_dir() => holds_files(&entry.path()),
            _ => true,
        }
    })
}

/// Whether a worker holds the project lock of `folder`. A lock file that cannot be opened
/// counts as held.
pub(crate) fn is_locked(folder: &Path) -> bool {
    match File::open(lock_file(folder)) {
        Ok(file) => is_held(file),
        Err(err) => err.kind() != io::ErrorKind::NotFound,
    }
}

/// Whether another process holds the lock on `file`, whose first byte the worker locks. Windows
/// refuses to read a locked byte, so the shell reads that byte and takes no lock: a probe that took
/// the lock, however briefly, would refuse a worker that binds the folder at that moment with
/// `project_locked`. A read that fails for any other reason counts as held too.
#[cfg(windows)]
fn is_held(mut file: File) -> bool {
    use std::io::Read;
    file.read(&mut [0]).is_err()
}

/// Whether another process holds the lock on `file`. Elsewhere the worker locks with `flock`,
/// which cannot be tested without being taken, so the shell takes the lock and releases it at once.
/// A worker that binds the folder at that moment is refused with `project_locked`; only the
/// Windows app, the one that ships, has to avoid that.
#[cfg(not(windows))]
fn is_held(file: File) -> bool {
    file.try_lock().is_err()
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::sync::atomic::{AtomicUsize, Ordering};

    use serde_json::{json, Value};

    use super::*;

    const NOW: u64 = 1_800_000_000_000;
    const SEED: &[u8] = b"{\"name\": \"Untitled\"}";

    /// A scratch folder that stands in for %TEMP%, deleted when dropped.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let root = std::env::temp_dir().join(format!(
                "mathimator-prune-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = fs::remove_dir_all(&root);
            fs::create_dir_all(&root).unwrap();
            Self(root)
        }

        /// A project folder holding the seed project.json and an empty `.mathimator`, as a worker
        /// leaves a temp project it bound.
        fn project(&self, name: &str) -> PathBuf {
            let folder = self.0.join(name);
            fs::create_dir_all(folder.join(".mathimator")).unwrap();
            fs::write(folder.join("project.json"), SEED).unwrap();
            folder
        }

        /// A marked temp project created at `created_ms`.
        fn marked(&self, name: &str, created_ms: u64) -> PathBuf {
            let folder = self.project(name);
            Marker::new(sha256::hex_digest(SEED), created_ms)
                .write(&folder)
                .unwrap();
            folder
        }

        /// Prunes, and returns the shell.log lines.
        fn prune(&self, keep: &[PathBuf]) -> Vec<String> {
            let logs = RefCell::new(Vec::new());
            prune(&self.0, keep, NOW, &|line| {
                logs.borrow_mut().push(line.to_owned())
            });
            logs.into_inner()
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    /// The name of a folder the shell made at `created_ms`.
    fn named(created_ms: u64) -> String {
        format!("mathimator-4321-{created_ms}")
    }

    fn export_a_movie(folder: &Path) {
        let exports = folder
            .join(".mathimator")
            .join("exports")
            .join("0123456789abcdef");
        fs::create_dir_all(&exports).unwrap();
        fs::write(exports.join("movie.mp4"), b"mp4").unwrap();
    }

    fn pruned(folder: &Path) -> String {
        format!("Pruned the temp project {}.", folder.display())
    }

    #[test]
    fn a_marker_round_trips_and_only_its_own_kind_counts() {
        let scratch = Scratch::new();
        let folder = scratch.project("lesson");
        assert_eq!(Marker::read(&folder), None);
        let marker = Marker::new(seed_of(&folder).unwrap(), 1_761_234_567_890);
        marker.write(&folder).unwrap();
        let saved: Value =
            serde_json::from_slice(&fs::read(marker_file(&folder)).unwrap()).unwrap();
        assert_eq!(
            saved,
            json!({
                "kind": "mathimator-temp",
                "seedSha256": sha256::hex_digest(SEED),
                "createdMs": 1_761_234_567_890_u64,
            })
        );
        assert_eq!(Marker::read(&folder), Some(marker));
        let other = json!({"kind": "other", "seedSha256": "0", "createdMs": 1});
        fs::write(marker_file(&folder), other.to_string()).unwrap();
        assert_eq!(Marker::read(&folder), None);
        fs::write(marker_file(&folder), b"{").unwrap();
        assert_eq!(Marker::read(&folder), None);
    }

    #[test]
    fn an_edit_or_an_export_touches_a_temp_project() {
        let scratch = Scratch::new();
        let folder = scratch.marked("mathimator-1-2", NOW);
        let marker = Marker::read(&folder).unwrap();
        assert!(!marker.is_touched(&folder));
        // An export folder with no file in it, as a failed publish may leave, holds no work.
        let exports = folder.join(".mathimator").join("exports");
        fs::create_dir_all(exports.join("0123456789abcdef")).unwrap();
        assert!(!marker.is_touched(&folder));

        export_a_movie(&folder);
        assert!(marker.is_touched(&folder));
        fs::remove_dir_all(&exports).unwrap();
        assert!(!marker.is_touched(&folder));

        fs::write(folder.join("project.json"), b"{\"name\": \"Waves\"}").unwrap();
        assert!(marker.is_touched(&folder));
        fs::remove_file(folder.join("project.json")).unwrap();
        assert!(marker.is_touched(&folder));
    }

    #[test]
    fn temp_folder_names_are_mathimator_digits_digits() {
        assert_eq!(created_ms_in_name(Path::new("mathimator-1-2")), Some(2));
        assert_eq!(
            created_ms_in_name(Path::new("C:\\Temp\\mathimator-4321-1761234567890")),
            Some(1_761_234_567_890)
        );
        for name in [
            "mathimator-1-2 - Copy",
            "mathimator-1-2-3",
            "mathimator-shell-test-1-2",
            "mathimator-texcheck-1",
            "mathimator--2",
            "mathimator-1-",
            "mathimator-12",
            "mathimator-1-+2",
            "mathimator-1-99999999999999999999999",
            "Mathimator-1-2",
        ] {
            assert_eq!(created_ms_in_name(Path::new(name)), None, "{name}");
        }
    }

    #[test]
    fn pruning_deletes_untouched_marked_projects_from_24_hours_ago() {
        let scratch = Scratch::new();
        let old = scratch.marked(&named(NOW), NOW - PRUNE_AGE_MS);
        // The age of a marked folder comes from its marker, not from its name.
        let young = scratch.marked(&named(1), NOW - PRUNE_AGE_MS + 1);
        let edited = scratch.marked(&named(2), 1);
        fs::write(edited.join("project.json"), b"{\"name\": \"Waves\"}").unwrap();
        let exported = scratch.marked(&named(3), 1);
        export_a_movie(&exported);
        let from_the_future = scratch.marked(&named(4), NOW + 1);

        assert_eq!(scratch.prune(&[]), [pruned(&old)]);
        assert!(!old.exists());
        for folder in [young, edited, exported, from_the_future] {
            assert!(folder.is_dir(), "{} was pruned", folder.display());
        }
    }

    #[test]
    fn pruning_deletes_unmarked_folders_from_24_hours_ago_that_published_nothing() {
        let scratch = Scratch::new();
        // From an older build, which wrote no marker and no lock file.
        let edited = scratch.project(&named(NOW - PRUNE_AGE_MS));
        fs::write(edited.join("project.json"), b"{\"name\": \"Waves\"}").unwrap();
        let bare = scratch.0.join(named(1));
        fs::create_dir(&bare).unwrap();
        // Bound by this build through a late reply, or left unmarked when the marker could not be
        // written. The workers that bound them have exited, so neither lock is held.
        let bound_edited = scratch.project(&named(2));
        fs::write(bound_edited.join("project.json"), b"{\"name\": \"Waves\"}").unwrap();
        File::create(lock_file(&bound_edited)).unwrap();
        let bound_untouched = scratch.project(&named(3));
        File::create(lock_file(&bound_untouched)).unwrap();
        let young = scratch.project(&named(NOW - PRUNE_AGE_MS + 1));
        let exported = scratch.project(&named(4));
        File::create(lock_file(&exported)).unwrap();
        export_a_movie(&exported);

        let mut logs = scratch.prune(&[]);
        logs.sort();
        let mut expected =
            [&edited, &bare, &bound_edited, &bound_untouched].map(|folder| pruned(folder));
        expected.sort();
        assert_eq!(logs, expected);
        assert!(young.is_dir() && exported.is_dir());
    }

    #[test]
    fn pruning_keeps_unmarked_folders_that_are_locked_or_kept() {
        let scratch = Scratch::new();
        let locked = scratch.project(&named(1));
        let lock = File::create(lock_file(&locked)).unwrap();
        lock.lock().unwrap();
        // The last project, which no worker has reopened yet.
        let keep = [scratch.project(&named(2))];

        assert!(scratch.prune(&keep).is_empty());
        assert!(locked.is_dir() && keep[0].is_dir());
        drop(lock);
        assert_eq!(scratch.prune(&keep), [pruned(&locked)]);
    }

    #[test]
    fn pruning_keeps_locked_and_kept_folders() {
        let scratch = Scratch::new();
        let locked = scratch.marked(&named(1), 1);
        let lock = File::create(lock_file(&locked)).unwrap();
        lock.lock().unwrap();
        let bound = scratch.marked(&named(2), 1);
        // A lock file nobody holds, as a worker that exited leaves it.
        let released = scratch.marked(&named(3), 1);
        File::create(lock_file(&released)).unwrap();

        let logs = scratch.prune(&[bound.clone(), scratch.0.join("gone")]);
        assert_eq!(logs, [pruned(&released)]);
        assert!(locked.is_dir() && bound.is_dir());
        drop(lock);
        assert_eq!(scratch.prune(&[bound]), [pruned(&locked)]);
    }

    /// Probing a lock takes no lock on Windows, so a worker that binds a folder while pruning
    /// probes it is never refused. Rust's lock stands in for the worker's.
    #[cfg(windows)]
    #[test]
    fn a_probe_never_refuses_a_lock_taken_while_it_runs() {
        use std::sync::atomic::AtomicBool;
        use std::thread;

        let scratch = Scratch::new();
        let folder = scratch.project(&named(1));
        File::create(lock_file(&folder)).unwrap();
        let done = AtomicBool::new(false);
        let refused = thread::scope(|scope| {
            scope.spawn(|| {
                while !done.load(Ordering::Relaxed) {
                    is_locked(&folder);
                }
            });
            // Nothing here panics, so the probe always hears that the locks are done.
            let refused = (0..2000)
                .filter(|_| {
                    !File::open(lock_file(&folder)).is_ok_and(|lock| lock.try_lock().is_ok())
                })
                .count();
            done.store(true, Ordering::Relaxed);
            refused
        });
        assert_eq!(refused, 0);
    }

    #[test]
    fn pruning_ignores_other_names_and_files() {
        let scratch = Scratch::new();
        let kept = [
            scratch.marked("mathimator-1-2 - Copy", 1),
            scratch.marked("mathimator-texcheck-1", 1),
            scratch.marked("lesson", 1),
        ];
        let file = scratch.0.join(named(3));
        fs::write(&file, b"not a folder").unwrap();
        assert!(scratch.prune(&[]).is_empty());
        assert!(kept.iter().all(|folder| folder.is_dir()) && file.is_file());
    }
}
