"""Where a project keeps its files, and where Manim writes inside them (spec section 4).

Everything Mathimator derives lives in the project's ``.mathimator`` folder:
the generated scene, Manim's media and the render records under ``cache``,
the published exports, the logs and the project lock. Manim writes a render
into the quality folder of its media, ``videos/scene/<height>p<fps>``, and
a PNG sequence into one folder for every size and frame rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mathimator_worker.argv import SCENE_CLASS, SCENE_FILE
from mathimator_worker.document import Project
from mathimator_worker.identity import export_folder_name


@dataclass(frozen=True)
class ProjectLayout:
    """The paths of the project in the folder ``root``."""

    root: Path

    @property
    def project_file(self) -> Path:
        return self.root / "project.json"

    @property
    def state_dir(self) -> Path:
        return self.root / ".mathimator"

    @property
    def scene_path(self) -> Path:
        return self.state_dir / "codegen" / SCENE_FILE

    @property
    def cache_dir(self) -> Path:
        return self.state_dir / "cache"

    @property
    def media_dir(self) -> Path:
        return self.cache_dir / "media"

    @property
    def last_good_dir(self) -> Path:
        return self.cache_dir / "last-good"

    @property
    def rendered_path(self) -> Path:
        return self.cache_dir / "rendered.json"

    @property
    def index_path(self) -> Path:
        return self.cache_dir / "preview-index.json"

    @property
    def run_marker(self) -> Path:
        """The record of a Manim run in progress, which a later bind sweeps after a crash (G1.1)."""
        return self.cache_dir / "manim-run.json"

    @property
    def exports_dir(self) -> Path:
        return self.state_dir / "exports"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "logs" / "worker.log"

    @property
    def derived_dirs(self) -> tuple[Path, ...]:
        """What Clear cache deletes: the render cache, the generated code, and the glyphs of this and older builds."""
        return tuple(self.state_dir / name for name in ("cache", "codegen", "equation", "typst"))

    def export_dir(self, project: Project) -> Path:
        """The folder the export of ``project`` is published to: ``exports/<key16>``."""
        return self.exports_dir / export_folder_name(project)

    @property
    def videos_dir(self) -> Path:
        """The folder of Manim's quality folders."""
        return self.media_dir / "videos" / self.scene_path.stem

    def video_dir(self, quality: str) -> Path:
        """The quality folder Manim writes a render's partial movies and its movie into."""
        return self.videos_dir / quality

    def partial_movie_dir(self, quality: str) -> Path:
        return self.video_dir(quality) / "partial_movie_files" / SCENE_CLASS

    @property
    def png_frames_dir(self) -> Path:
        """Manim writes every PNG sequence here, whatever its size or frame rate."""
        return self.media_dir / "images" / self.scene_path.stem / SCENE_CLASS
