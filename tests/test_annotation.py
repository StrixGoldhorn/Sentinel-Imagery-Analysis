"""Tests for the packaged batch-annotation workflow without opening a GUI."""

import unittest
from io import StringIO
from pathlib import Path

import numpy as np
from PIL import Image

from sentinel_analysis.application.ports.annotation import AnnotationProgress, AnnotationTile
from sentinel_analysis.application.use_cases.annotate_tiles import AnnotationSummary, BatchAnnotateTiles
from sentinel_analysis.infrastructure.annotation.filesystem import (
    FilesystemAnnotationTileSource,
    JSONAnnotationProgressRepository,
)
from sentinel_analysis.interfaces.cli.annotate import AnnotateCommand
from sentinel_analysis.interfaces.desktop.annotation import (
    OpenCVBoxEditor,
    lee_filter,
    load_boxes_from_file,
    mask_image_with_dem,
    rough_ship_boxes,
)


RUNTIME = Path(__file__).resolve().parent / "runtime" / "annotation"


class StaticTileSource:
    def __init__(self, tiles):
        self.tiles = tiles

    def list_tiles(self, tile_folder):
        return list(self.tiles)


class MemoryProgressRepository:
    def __init__(self, progress=AnnotationProgress()):
        self.progress = progress
        self.saved = []

    def load(self):
        return self.progress

    def save(self, progress):
        self.progress = progress
        self.saved.append(progress)


class FakeEditor:
    def __init__(self, continue_results):
        self.continue_results = iter(continue_results)
        self.calls = []

    def edit(self, tile, label_path, use_lee_filter):
        self.calls.append((tile, label_path, use_lee_filter))
        return next(self.continue_results)


def test_batch_workflow_resumes_and_persists_after_every_tile() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    tiles = [
        AnnotationTile(str(index), Path(f"{index}_sar.png"))
        for index in range(3)
    ]
    progress = MemoryProgressRepository(AnnotationProgress(1, 3))
    editor = FakeEditor([True, True])

    summary = BatchAnnotateTiles(StaticTileSource(tiles), progress, editor).execute(RUNTIME)

    assert summary == AnnotationSummary(3, 2, 3, True)
    assert [item.current_index for item in progress.saved] == [2, 3]
    assert all(call[2] for call in editor.calls)


def test_batch_workflow_stops_cleanly_when_editor_requests_exit() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    tiles = [AnnotationTile("0", Path("0_sar.png")), AnnotationTile("1", Path("1_sar.png"))]
    progress = MemoryProgressRepository()

    summary = BatchAnnotateTiles(
        StaticTileSource(tiles),
        progress,
        FakeEditor([False]),
    ).execute(RUNTIME, start_index=0, use_lee_filter=False)

    assert summary == AnnotationSummary(2, 1, 1, False)
    assert progress.saved[-1] == AnnotationProgress(1, 2)


def test_filesystem_source_pairs_sar_and_optional_dem_tiles() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    sar_path = RUNTIME / "0001_sar.png"
    dem_path = RUNTIME / "0001_dem.png"
    Image.new("L", (2, 2), 0).save(sar_path)
    Image.new("L", (2, 2), 0).save(dem_path)
    try:
        tiles = FilesystemAnnotationTileSource().list_tiles(RUNTIME)
        selected = next(tile for tile in tiles if tile.tile_id == "0001")
        assert selected.dem_path == dem_path.resolve()
    finally:
        sar_path.unlink(missing_ok=True)
        dem_path.unlink(missing_ok=True)


def test_json_progress_round_trip_is_atomic() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    path = RUNTIME / "progress-test.json"
    path.unlink(missing_ok=True)
    repository = JSONAnnotationProgressRepository(path)
    try:
        repository.save(AnnotationProgress(4, 10))
        assert repository.load() == AnnotationProgress(4, 10)
        assert not (RUNTIME / "progress-test.json.tmp").exists()
    finally:
        path.unlink(missing_ok=True)


def test_processing_helpers_validate_filter_and_find_bright_region() -> None:
    image = np.zeros((50, 50), dtype=np.uint8)
    image[20:30, 20:30] = 255

    assert lee_filter(image, 5).dtype == image.dtype
    assert len(rough_ship_boxes(image)) == 1
    try:
        lee_filter(image, 4)
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_load_boxes_from_file_parses_existing_labels() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    label_file = RUNTIME / "test_labels.txt"
    label_file.write_text("0 10 20 50 60\n0 100 120 150 170\n", encoding="utf-8")
    try:
        boxes = load_boxes_from_file(label_file)
        assert boxes == [[10, 20, 50, 60], [100, 120, 150, 170]]
    finally:
        label_file.unlink(missing_ok=True)


def test_mask_image_with_dem_handles_different_shapes_gracefully() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    sar = np.zeros((100, 100), dtype=np.uint8)
    dem_path = RUNTIME / "dem_mismatch.png"
    Image.new("L", (80, 80), 0).save(dem_path)
    try:
        masked, mask = mask_image_with_dem(sar, dem_path)
        assert masked.shape == (100, 100)
    finally:
        dem_path.unlink(missing_ok=True)


def test_box_editor_save_persists_labels_correctly() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    output_path = RUNTIME / "saved_boxes.txt"
    output_path.unlink(missing_ok=True)
    dummy_img = np.zeros((200, 200), dtype=np.uint8)
    editor = OpenCVBoxEditor(dummy_img, [(10, 10, 20, 20)], output_path)
    editor._save()
    try:
        assert output_path.is_file()
        content = output_path.read_text(encoding="utf-8")
        assert "0 10 10 30 30" in content
    finally:
        output_path.unlink(missing_ok=True)


def test_annotation_cli_delegates_to_workflow() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    class UseCase:
        def __init__(self):
            self.calls = []

        def execute(self, *args):
            self.calls.append(args)
            return AnnotationSummary(2, 2, 2, True)

    use_case = UseCase()
    stdout = StringIO()
    exit_code = AnnotateCommand(use_case_factory=lambda progress: use_case).run(
        [str(RUNTIME), "--no-lee-filter"],
        stdout=stdout,
    )

    assert exit_code == 0
    assert not use_case.calls[0][2]
    assert "Processed 2 of 2" in stdout.getvalue()


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

