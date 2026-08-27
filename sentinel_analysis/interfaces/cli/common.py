"""Shared command-line execution behavior."""

import argparse
import sys
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TextIO

from sentinel_analysis.application.exceptions import ApplicationError


class CLICommand(ABC):
    """Parse arguments and translate expected failures into stable exit codes."""

    @abstractmethod
    def create_parser(self) -> argparse.ArgumentParser:
        ...

    @abstractmethod
    def execute(self, args: argparse.Namespace, stdout: TextIO) -> int:
        ...

    def run(
        self,
        argv: Sequence[str] | None = None,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> int:
        output = stdout or sys.stdout
        errors = stderr or sys.stderr
        args = self.create_parser().parse_args(argv)
        try:
            return self.execute(args, output)
        except (ApplicationError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=errors)
            return 1
