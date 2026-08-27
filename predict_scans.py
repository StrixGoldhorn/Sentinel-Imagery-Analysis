"""Compatibility entry point for satellite-pass prediction."""

from sentinel_analysis.interfaces.cli.predict import main, predict_next_scans_n2yo

__all__ = ["predict_next_scans_n2yo"]


if __name__ == "__main__":
    main()

