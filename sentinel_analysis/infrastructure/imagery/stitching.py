"""Pillow implementation of tile stitching."""

from pathlib import Path

from PIL import Image

from sentinel_analysis.domain.entities import ImageTile


class PillowImageStitcher:
    def stitch(self, tiles: list[tuple[ImageTile, Path]], output_path: Path) -> None:
        if not tiles:
            raise ValueError("At least one tile is required")

        widths = {tile.x: tile.width for tile, _ in tiles}
        heights = {tile.y: tile.height for tile, _ in tiles}
        canvas = Image.new("RGBA", (sum(widths.values()), sum(heights.values())), (0, 0, 0, 0))

        for tile, path in tiles:
            with Image.open(path) as source:
                image = source.convert("RGBA")
                if image.size != (tile.width, tile.height):
                    raise ValueError(f"Tile dimensions do not match metadata: {path.name}")
                x_offset = sum(widths[index] for index in range(tile.x))
                y_offset = sum(heights[index] for index in range(tile.y + 1, len(heights)))
                canvas.paste(image, (x_offset, y_offset))

        if not canvas.getbbox():
            raise ValueError("No valid imagery coverage returned for this bounding box")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path)

