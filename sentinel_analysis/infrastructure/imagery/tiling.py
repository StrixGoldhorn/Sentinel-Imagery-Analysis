"""Geographic tile-grid calculation."""

import math

from sentinel_analysis.domain.entities import BoundingBox, ImageTile


class TileGridCalculator:
    def __init__(self, max_image_size: int = 2500, resolution_meters: float = 10) -> None:
        self._max_image_size = max_image_size
        self._resolution = resolution_meters
        self._max_tile_size_meters = max_image_size * resolution_meters

    def calculate(self, bbox: BoundingBox) -> list[ImageTile]:
        meters_per_degree_latitude = 111_320
        center_latitude, _ = bbox.center
        meters_per_degree_longitude = meters_per_degree_latitude * math.cos(math.radians(center_latitude))
        if meters_per_degree_longitude <= 0:
            raise ValueError("Bounding box is too close to a pole for this tiling model")

        tile_degrees_lon = self._max_tile_size_meters / meters_per_degree_longitude
        tile_degrees_lat = self._max_tile_size_meters / meters_per_degree_latitude
        count_x = math.ceil((bbox.max_longitude - bbox.min_longitude) / tile_degrees_lon)
        count_y = math.ceil((bbox.max_latitude - bbox.min_latitude) / tile_degrees_lat)

        tiles: list[ImageTile] = []
        for y in range(count_y):
            for x in range(count_x):
                minimum_lon = bbox.min_longitude + x * tile_degrees_lon
                maximum_lon = min(minimum_lon + tile_degrees_lon, bbox.max_longitude)
                minimum_lat = bbox.min_latitude + y * tile_degrees_lat
                maximum_lat = min(minimum_lat + tile_degrees_lat, bbox.max_latitude)
                tile_bbox = BoundingBox(minimum_lon, minimum_lat, maximum_lon, maximum_lat)
                width = max(1, min(self._max_image_size, round((maximum_lon - minimum_lon) * meters_per_degree_longitude / self._resolution)))
                height = max(1, min(self._max_image_size, round((maximum_lat - minimum_lat) * meters_per_degree_latitude / self._resolution)))
                tiles.append(ImageTile(tile_bbox, width, height, x, y))
        return tiles

