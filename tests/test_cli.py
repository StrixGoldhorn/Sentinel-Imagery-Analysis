"""Offline tests for command-line adapters and compatibility entry points."""

import importlib
import unittest
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from sentinel_analysis.application.ports.detection import DetectionResult
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan, ShipDetection
from sentinel_analysis.interfaces.cli.detect import DetectCommand, get_ship_boxes
from sentinel_analysis.interfaces.cli.download import DownloadCommand
from sentinel_analysis.interfaces.cli.ingest import IngestCommand
from sentinel_analysis.interfaces.cli.predict import PredictCommand


RUNTIME = Path(__file__).resolve().parent / "runtime" / "cli"
BBOX_ARGUMENTS = ["--bbox", "103", "1", "104", "2"]


class FakeUseCase:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []
        self.keyword_calls = []

    def execute(self, *args, **kwargs):
        self.calls.append(args)
        self.keyword_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class CLIInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        RUNTIME.mkdir(parents=True, exist_ok=True)

    def test_detect_command_uses_application_result_and_writes_annotation(self) -> None:
        image_path = RUNTIME / "source.png"
        output_path = RUNTIME / "detected.jpg"
        Image.new("L", (20, 20), 0).save(image_path)
        use_case = FakeUseCase(DetectionResult([ShipDetection(2, 3, 4, 5)], 20, 20))
        stdout = StringIO()
        try:
            exit_code = DetectCommand(use_case).run(
                [str(image_path), "--output", str(output_path), "--threshold", "50"],
                stdout=stdout,
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.is_file())
            self.assertIn("Detected 1", stdout.getvalue())
            self.assertEqual(use_case.calls[0][2], 50)
        finally:
            image_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def test_legacy_box_function_delegates_to_use_case(self) -> None:
        use_case = FakeUseCase(DetectionResult([ShipDetection(1, 2, 3, 4)], 100, 50))

        boxes, width, height = get_ship_boxes("scan.png", threshold=30, use_case=use_case)

        self.assertEqual(boxes, [(1, 2, 3, 4)])
        self.assertEqual((width, height), (100, 50))

    def test_download_command_delegates_complete_workflow_to_create_scan(self) -> None:
        output_root = RUNTIME / "downloads"
        scan = Scan(
            "scan",
            BoundingBox(103, 1, 104, 2),
            Acquisition(datetime(2026, 8, 27, tzinfo=timezone.utc), "Sentinel-1", "sar"),
            str(output_root / "scan" / "images" / "scan.png"),
        )
        use_case = FakeUseCase(scan)
        roots = []
        command = DownloadCommand(use_case_factory=lambda root: roots.append(root) or use_case)
        stdout = StringIO()

        exit_code = command.run(
            [*BBOX_ARGUMENTS, "--days-ago", "7", "--output-dir", str(output_root)],
            stdout=stdout,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(roots, [output_root.resolve()])
        self.assertEqual(use_case.calls[0][1], 7)
        self.assertIn("scan.png", stdout.getvalue())

    def test_ingest_command_reports_plugin_outcomes_and_failure_exit_code(self) -> None:
        result = {
            "total_inserted": 2,
            "logs": [
                {"plugin": "one", "status": "SUCCESS", "records": 2, "error": None},
                {"plugin": "two", "status": "FAILED", "records": 0, "error": "offline"},
            ],
        }
        use_case = FakeUseCase(result)
        now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
        stdout = StringIO()

        exit_code = IngestCommand(use_case, clock=lambda: now).run(
            [*BBOX_ARGUMENTS, "--hours", "6"],
            stdout=stdout,
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("two: FAILED", stdout.getvalue())
        self.assertEqual(use_case.calls[0][1], (datetime(2026, 8, 27, 6, tzinfo=timezone.utc), now))

    def test_predict_command_uses_configured_key_and_formats_predictions(self) -> None:
        use_case = FakeUseCase([{"time": "2026-08-28T00:00:00+00:00", "max_elevation": 42}])
        command = PredictCommand(
            use_case,
            settings_loader=lambda: SimpleNamespace(n2yo_api_key="configured-key"),
        )
        stdout = StringIO()

        exit_code = command.run(BBOX_ARGUMENTS, stdout=stdout)

        self.assertEqual(exit_code, 0)
        self.assertEqual(use_case.calls[0][1], "configured-key")
        self.assertIn("max elev: 42°", stdout.getvalue())

    def test_commands_translate_expected_errors_to_stderr_and_exit_one(self) -> None:
        command = PredictCommand(
            FakeUseCase(error=ValueError("bad prediction")),
            settings_loader=lambda: SimpleNamespace(n2yo_api_key="key"),
        )
        stderr = StringIO()

        exit_code = command.run(BBOX_ARGUMENTS, stderr=stderr)

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue().strip(), "Error: bad prediction")

    def test_unified_dispatcher_forwards_remaining_arguments(self) -> None:
        dispatcher = importlib.import_module("sentinel_analysis.interfaces.cli.__main__")
        original = dispatcher.COMMANDS["predict"]
        received = []
        dispatcher.COMMANDS["predict"] = lambda arguments: received.append(list(arguments)) or 7
        try:
            exit_code = dispatcher.main(["predict", *BBOX_ARGUMENTS])
        finally:
            dispatcher.COMMANDS["predict"] = original

        self.assertEqual(exit_code, 7)
        self.assertEqual(received[0], BBOX_ARGUMENTS)

    def test_unified_dispatcher_forwards_subcommand_help(self) -> None:
        dispatcher = importlib.import_module("sentinel_analysis.interfaces.cli.__main__")
        original = dispatcher.COMMANDS["predict"]
        received = []
        dispatcher.COMMANDS["predict"] = lambda arguments: received.append(list(arguments)) or 0
        try:
            exit_code = dispatcher.main(["predict", "--help"])
        finally:
            dispatcher.COMMANDS["predict"] = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(received, [["--help"]])

if __name__ == "__main__":
    unittest.main()
