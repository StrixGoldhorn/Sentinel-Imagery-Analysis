"""Command-line interface for Copernicus imagery downloads."""

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.geocoding import NominatimLocationResolver
from sentinel_analysis.infrastructure.imagery.copernicus import CopernicusImageryProvider, CopernicusTokenProvider
from sentinel_analysis.infrastructure.imagery.stitching import PillowImageStitcher
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.interfaces.cli.common import CLICommand


class DownloadCommand(CLICommand):
    def __init__(
        self,
        settings_loader: Callable[[], Settings] = Settings.from_environment,
        use_case_factory: Callable[[Path], CreateScan] | None = None,
    ) -> None:
        self._settings_loader = settings_loader
        self._use_case_factory = use_case_factory

    def create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Download and stitch Sentinel-1 SAR imagery")
        parser.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
        parser.add_argument("--days-ago", type=int, default=30)
        parser.add_argument("--output-dir", default=".")
        return parser

    def _build_use_case(self, output_root: Path) -> CreateScan:
        if self._use_case_factory is not None:
            return self._use_case_factory(output_root)
        settings = self._settings_loader()
        provider = CopernicusImageryProvider(
            CopernicusTokenProvider(settings.copernicus_username, settings.copernicus_password)
        )
        return CreateScan(
            provider,
            PillowImageStitcher(),
            FilesystemScanRepository(output_root),
            NominatimLocationResolver(),
        )

    def execute(self, args: argparse.Namespace, stdout: TextIO) -> int:
        use_case = self._build_use_case(Path(args.output_dir).resolve())
        scan = use_case.execute(BoundingBox.from_sequence(args.bbox), args.days_ago)
        print(scan.image_path, file=stdout)
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    return DownloadCommand().run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
