"""Compatibility entry point for the packaged detection CLI."""

from sentinel_analysis.interfaces.cli.detect import detect_ships_basic, get_ship_boxes, main

__all__ = ["detect_ships_basic", "get_ship_boxes"]


if __name__ == "__main__":
    main()

