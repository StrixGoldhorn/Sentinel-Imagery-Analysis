"""Use cases for querying and editing individual vessel details."""

from typing import Any

from sentinel_analysis.application.exceptions import VesselNotFoundError
from sentinel_analysis.application.ports.ais_repository import AISRepository


class GetVesselDetails:
    """Retrieves full details and latest location for an individual vessel."""

    def __init__(self, repository: AISRepository) -> None:
        self._repository = repository

    def execute(self, vessel_id: int) -> dict[str, Any]:
        if isinstance(vessel_id, bool) or not isinstance(vessel_id, int) or vessel_id <= 0:
            raise ValueError("Vessel ID must be a positive integer")

        vessel = self._repository.get_vessel_by_id(vessel_id)
        if vessel is None:
            raise VesselNotFoundError(f"Vessel with ID {vessel_id} not found")

        return vessel


class UpdateVesselDetails:
    """Updates custom metadata, ship classification, name, IMO, or callsign for an individual vessel."""

    def __init__(self, repository: AISRepository) -> None:
        self._repository = repository

    def execute(
        self,
        vessel_id: int,
        name: str | None = None,
        vessel_type: str | None = None,
        callsign: str | None = None,
        imo: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(vessel_id, bool) or not isinstance(vessel_id, int) or vessel_id <= 0:
            raise ValueError("Vessel ID must be a positive integer")

        # Verify vessel exists before updating
        existing = self._repository.get_vessel_by_id(vessel_id)
        if existing is None:
            raise VesselNotFoundError(f"Vessel with ID {vessel_id} not found")

        if imo is not None:
            if not isinstance(imo, str) or not imo.strip():
                raise ValueError("IMO cannot be empty string")

        updated = self._repository.update_vessel(
            vessel_id=vessel_id,
            name=name,
            vessel_type=vessel_type,
            callsign=callsign,
            imo=imo,
        )
        if updated is None:
            raise VesselNotFoundError(f"Vessel with ID {vessel_id} not found")

        return updated
