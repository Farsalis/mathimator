"""Write the groups of files plan.json lists, print its log and exit with its code.

An auto plan, {"auto": {"beats": N}}, is one group that renders N beats where
Manim would for its command line, for a test that cannot know the worker's
paths in advance: N complete MP4 partials and the segment list that names
them in partial_movie_files/<scene class> of the quality folder
videos/<scene>/<height>p<fps> under --media_dir, then the movie
<scene class>.mp4 in that quality folder. It logs nothing, never holds, starts
no child and exits 0.

seen.json records the run marker and what the media folder held when this
process started, the folder, arguments and environment it started with, and
the function behind each scene writer method that manim_main replaces. Like
Manim, it writes one file after another, so each file gets a later mtime than
the one before. After each group but the last, and after the last too in a
held plan, it writes held-<n>.txt and waits for go-<n>.txt. A plan with a
child first starts a process that sleeps with this one's stdout and stderr,
like a TeX child that outlives Manim, and writes its pid to child.txt. Like
Manim at the end of a render, it then marks each segment that a segment list
it wrote names as used, in list order, and in that list's folder keeps at most
max_files_cached segments, as the --config_file sets it or Manim's default of
100, deleting the least recently used; -1 keeps them all.
"""

import configparser
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

from manim.scene.scene_file_writer import SceneFileWriter

ENVIRONMENT = ("HOME", "USERPROFILE", "shell_escape", "openin_any", "openout_any")
SEGMENTS = (".mov", ".mp4", ".webm")
# The smallest MP4 that the worker's clip check accepts, the boxes of support.complete_clip(".mp4").
CLIP = b"".join(
    struct.pack(">I4s", 8 + len(payload), kind) + payload
    for kind, payload in ((b"ftyp", b"isom" + bytes(4)), (b"free", b""), (b"mdat", bytes(64)), (b"moov", b""))
)


def argument(flag: str, offset: int = 1) -> str:
    """The command-line argument ``offset`` places after ``flag``."""
    return sys.argv[sys.argv.index(flag) + offset]


def render(beats: int, media: Path) -> dict[Path, bytes]:
    """The files Manim writes for a render of ``beats`` beats on this command line, in the order it writes them.

    Like Manim, it names each partial in the segment list by its resolved path,
    through the scene writer method that manim_main replaces.
    """
    fmt = argument("--format")
    if fmt != "mp4":
        sys.exit(f"an auto plan renders mp4, not {fmt}")
    scene, scene_class = Path(argument("render")), argument("render", 2)
    height = argument("-r").partition(",")[2]
    quality = (media / "videos" / scene.stem / f"{height}p{argument('--fps')}").resolve()
    partials = quality / "partial_movie_files" / scene_class
    files = {partials / f"{index:064x}.mp4": CLIP for index in range(beats)}
    manifest = SceneFileWriter._concat_manifest_bytes([str(path) for path in files])
    files[partials / "partial_movie_file_list.txt"] = manifest
    files[quality / f"{scene_class}.mp4"] = CLIP
    return files


here = Path(__file__).parent
plan = {"log": [], "exit": 0, "hold": False, "child": False} | json.loads(
    (here / "plan.json").read_text(encoding="utf-8")
)
media = Path(argument("--media_dir"))
marker = media.parent / "manim-run.json"
seen = {
    "marker": json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None,
    "files": sorted(path.relative_to(media).as_posix() for path in media.rglob("*") if path.is_file()),
    "cwd": os.getcwd(),
    "argv": sys.argv[1:],
    "env": {name: os.environ.get(name) for name in ENVIRONMENT},
    "writer": {name: getattr(SceneFileWriter, name).__name__ for name in ("_concat_manifest_bytes", "write_frame")},
}
(here / "seen.json").write_text(json.dumps(seen), encoding="utf-8")
if plan["child"]:
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    child = subprocess.Popen(sleeper, stdout=sys.stdout, stderr=sys.stderr)
    (here / "child.txt").write_text(str(child.pid), encoding="utf-8")
if "auto" in plan:
    groups = [render(plan["auto"]["beats"], media)]
else:
    groups = [{Path(name): bytes.fromhex(data) for name, data in group.items()} for group in plan["groups"]]
stamp = 0
listed = set()
for number, group in enumerate(groups):
    for target, data in group.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        stamp = max(stamp + 1_000_000, time.time_ns())
        os.utime(target, ns=(stamp, stamp))
        if target.name == "partial_movie_file_list.txt":
            listed.add(target.parent)
    if number < len(groups) - 1 or plan["hold"]:
        (here / f"held-{number}.txt").write_text("", encoding="utf-8")
        while not (here / f"go-{number}.txt").exists():
            time.sleep(0.02)
settings = configparser.ConfigParser()
if "--config_file" in sys.argv:
    settings.read(argument("--config_file"), encoding="utf-8")
cached = settings.getint("CLI", "max_files_cached", fallback=100)
for folder in listed:
    for line in (folder / "partial_movie_file_list.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("file "):
            used = folder / line.rpartition("/")[2].removesuffix("'")
            stamp = max(stamp + 1_000_000, time.time_ns())
            os.utime(used, ns=(stamp, used.stat().st_mtime_ns))
    segments = sorted(
        (path for path in folder.iterdir() if path.suffix in SEGMENTS), key=lambda path: path.stat().st_atime
    )
    excess = len(segments) - cached if cached != -1 else 0
    for path in segments[: max(excess, 0)]:
        path.unlink()
for line in plan["log"]:
    print(line, flush=True)
sys.exit(plan["exit"])
