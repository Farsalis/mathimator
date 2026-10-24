"""Only the scene writer methods that ``mathimator_worker.manim_main`` replaces."""


class SceneFileWriter:
    @staticmethod
    def _concat_manifest_bytes(input_files):
        raise NotImplementedError

    def write_frame(self, pixels, *, repeat=1):
        raise NotImplementedError
