"""Top-level CLI entry point for running the Sentinel Analysis background scheduler daemon.

Example:
    python -m sentinel_analysis.scheduler --aoi-interval 30 --workers 4
"""

from sentinel_analysis.interfaces.cli.scheduler_runner import main

if __name__ == "__main__":
    main()
