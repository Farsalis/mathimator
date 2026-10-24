"""Where a project keeps its files, and where Manim writes inside them (spec section 4).

Everything Mathimator derives lives in the project's ``.mathimator`` folder:
the generated scene, Manim's media and the render records under ``cache``,
the published exports, the logs and the project lock. Manim writes a render
into the quality folder of its media, ``videos/scene/<height>p<fps>``, and
a PNG sequence into one folder for every size and frame rate. None of the
folders Mathimator keeps its files in, nor its log, may be a symbolic link or
a junction, which would point its writes and deletes at another place (O10).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mathimator_worker.argv import SCENE_CLASS, SCENE_FILE
from mathimator_worker.document import FORMATS, Project
from mathimator_worker.identity import export_folder_name
from mathimator_worker.project_folder import first_link


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

    @property
    def managed_paths(self) -> tuple[Path, ...]:
        """The folders Mathimator writes its files into and deletes them from, and its log: none may be a link (O10).

        Each comes with the folders between it and the project folder:
        ``.mathimator``, its cache and the media folder.
        """
        return (
            self.scene_path.parent,
            self.tex_dir,
            self.videos_dir,
            self.png_frames_dir,
            self.last_good_dir,
            self.exports_dir,
            self.state_dir / "equation",
            self.log_path,
        )

    def linked_path(self, *also: Path) -> Path | None:
        """The first of ``managed_paths`` and ``also``, or a folder above one, that is a link, or None (O10).

        ``also`` are the paths of one job, which the layout cannot know: its
        quality folder, and the file or folder an export is published as.
        """
        for path in (*self.managed_paths, *also):
            link = first_link(self.root, path)
            if link is not None:
                return link
        return None

    def export_dir(self, project: Project) -> Path:
        """The folder the export of ``project`` is published to: ``exports/<key16>``."""
        return self.exports_dir / export_folder_name(project)

    def export_path(self, project: Project) -> Path:
        """The file or folder the export of ``project`` is published as, in ``export_dir``."""
        return self.export_dir(project) / FORMATS[project.export_profile.format].artifact_name

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

    @property
    def tex_dir(self) -> Path:
        """Manim's TeX folder, whose inputs TeX reads before its own."""
        return self.media_dir / "Tex"
