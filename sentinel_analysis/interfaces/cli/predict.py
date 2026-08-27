"""Command-line interface for satellite-pass prediction."""

import argparse
from collections.abc import Callable, Sequence
from typing import TextIO

from sentinel_analysis.application.ports.satellite import PassPredictor
from sentinel_analysis.application.use_cases.predict_passes import PredictPasses
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.satellite.n2yo import N2YOPassPredictor
from sentinel_analysis.interfaces.cli.common import CLICommand


def predict_next_scans_n2yo(
    bbox,
    api_key: str,
    predictor: PassPredictor | None = None,
):
    return PredictPasses(predictor or N2YOPassPredictor()).execute(
        BoundingBox.from_sequence(bbox),
        api_key,
    )


class PredictCommand(CLICommand):
    def __init__(
        self,
        use_case: PredictPasses | None = None,
        settings_loader: Callable[[], Settings] = Settings.from_environment,
    ) -> None:
        self._use_case = use_case or PredictPasses(N2YOPassPredictor())
        self._settings_loader = settings_loader

    def create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Predict Sentinel-1 passes for a bounding box")
        parser.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
        parser.add_argument("--n2yo-key", default=None, help="N2YO API key")
        return parser

    def execute(self, args: argparse.Namespace, stdout: TextIO) -> int:
        key = args.n2yo_key or self._settings_loader().n2yo_api_key
        if not key:
            raise ValueError("Provide --n2yo-key or set N2YO_API_KEY")
        predictions = self._use_case.execute(BoundingBox.from_sequence(args.bbox), key)
        if not predictions:
            print("No upcoming passes found.", file=stdout)
            return 0
        for index, prediction in enumerate(predictions, 1):
            print(
                f"{index}. {prediction['time']} | max elevation: {prediction['max_elevation']}",
                file=stdout,
            )
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    return PredictCommand().run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
