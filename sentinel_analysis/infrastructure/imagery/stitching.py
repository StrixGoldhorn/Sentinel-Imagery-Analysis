"""Pillow implementation of tile stitching."""

from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from sentinel_analysis.application.ports.imagery import TileImage


class PillowImageStitcher:
    def stitch(self, tiles: Sequence[TileImage], output_path: Path) -> None:
        if not tiles:
            raise ValueError("At least one tile is required")

        coordinates = [(tile.x, tile.y) for tile, _ in tiles]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("Tile grid contains duplicate coordinates")

        maximum_x = max(x for x, _ in coordinates)
        maximum_y = max(y for _, y in coordinates)
        expected = {(x, y) for y in range(maximum_y + 1) for x in range(maximum_x + 1)}
        if set(coordinates) != expected:
            raise ValueError("Tile grid must be rectangular and contiguous")

        widths: dict[int, int] = {}
        heights: dict[int, int] = {}
        for tile, _ in tiles:
            if tile.x in widths and widths[tile.x] != tile.width:
                raise ValueError(f"Tiles in column {tile.x} have inconsistent widths")
            if tile.y in heights and heights[tile.y] != tile.height:
                raise ValueError(f"Tiles in row {tile.y} have inconsistent heights")
            widths[tile.x] = tile.width
            heights[tile.y] = tile.height

        canvas = Image.new("RGBA", (sum(widths.values()), sum(heights.values())), (0, 0, 0, 0))

        temporary: Path | None = None
        try:
            for tile, path in tiles:
                with Image.open(path) as source:
                    with source.convert("RGBA") as image:
                        if image.size != (tile.width, tile.height):
                            raise ValueError(f"Tile dimensions do not match metadata: {path.name}")
                        x_offset = sum(widths[index] for index in range(tile.x))
                        y_offset = sum(heights[index] for index in range(tile.y + 1, maximum_y + 1))
                        canvas.paste(image, (x_offset, y_offset))

            if not canvas.getbbox():
                raise ValueError("No valid imagery coverage returned for this bounding box")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_name(f"{output_path.name}.tmp")
            canvas.save(temporary, format="PNG")
            temporary.replace(output_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            canvas.close()
