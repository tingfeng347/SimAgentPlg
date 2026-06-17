from __future__ import annotations

from simagentplg.game.world import RESOURCE_TYPES, WEATHER_TYPES, WorldState


class GodSystem:
    """Player-facing god powers with explicit event logging."""

    def __init__(self, world: WorldState) -> None:
        self.world = world

    def give_resource(self, faction_id: str, resource: str, amount: int) -> None:
        if faction_id not in self.world.factions:
            raise ValueError(f"unknown faction {faction_id!r}")
        if resource not in RESOURCE_TYPES:
            raise ValueError(f"unknown resource {resource!r}")
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.world.factions[faction_id].resources.add(resource, amount)
        self.world.add_event(
            "god",
            f"God granted {amount} {resource} to {faction_id}",
            faction_id=faction_id,
        )

    def set_weather(self, x: int, y: int, weather: str) -> None:
        if weather not in WEATHER_TYPES:
            raise ValueError(f"unknown weather {weather!r}")
        tile = self.world.tile_at(x, y)
        tile.weather = weather
        self.world.add_event(
            "god",
            f"God changed weather at ({x}, {y}) to {weather}",
            faction_id=tile.owner,
        )

    def claim_tile(self, faction_id: str, x: int, y: int) -> None:
        if faction_id not in self.world.factions:
            raise ValueError(f"unknown faction {faction_id!r}")
        tile = self.world.tile_at(x, y)
        previous = tile.owner
        tile.owner = faction_id
        if tile.population_of(faction_id) == 0:
            tile.population[faction_id] = 1
        self.world.add_event(
            "god",
            f"God assigned tile ({x}, {y}) from {previous} to {faction_id}",
            faction_id=faction_id,
        )

    def protect_tile(self, x: int, y: int, protected: bool = True) -> None:
        tile = self.world.tile_at(x, y)
        tile.protected = protected
        state = "protected" if protected else "unprotected"
        self.world.add_event(
            "god",
            f"God marked tile ({x}, {y}) as {state}",
            faction_id=tile.owner,
        )

    def disaster(self, x: int, y: int, kind: str) -> None:
        tile = self.world.tile_at(x, y)
        if kind == "storm":
            tile.weather = "storm"
        elif kind == "drought":
            tile.weather = "drought"
        elif kind == "plague":
            for faction_id, population in list(tile.population.items()):
                tile.population[faction_id] = max(0, population - max(1, population // 4))
        else:
            raise ValueError(f"unknown disaster {kind!r}")
        self.world.add_event(
            "god",
            f"God sent {kind} to ({x}, {y})",
            faction_id=tile.owner,
        )

    def answer_petition(self, petition_id: int, approve: bool) -> None:
        petition = next(
            (
                item
                for item in self.world.petitions
                if item.petition_id == petition_id
            ),
            None,
        )
        if petition is None:
            raise ValueError(f"unknown petition {petition_id}")
        if petition.status != "pending":
            raise ValueError(f"petition {petition_id} is already {petition.status}")

        if not approve:
            petition.status = "rejected"
            self.world.add_event(
                "god",
                f"God rejected petition {petition_id} from {petition.faction_id}",
                faction_id=petition.faction_id,
            )
            return

        petition.status = "approved"
        self._apply_petition(petition.faction_id, petition.kind, petition.request)
        self.world.add_event(
            "god",
            f"God approved petition {petition_id} from {petition.faction_id}",
            faction_id=petition.faction_id,
        )

    def _apply_petition(
        self,
        faction_id: str,
        kind: str,
        request: dict[str, object],
    ) -> None:
        if kind == "resources":
            resource = str(request.get("resource", "food"))
            amount = int(request.get("amount", 25))
            self.give_resource(faction_id, resource, amount)
        elif kind == "weather":
            x = int(request["x"])
            y = int(request["y"])
            weather = str(request.get("weather", "rain"))
            self.set_weather(x, y, weather)
        elif kind == "territory":
            x = int(request["x"])
            y = int(request["y"])
            self.claim_tile(faction_id, x, y)
        elif kind == "protection":
            x = int(request["x"])
            y = int(request["y"])
            self.protect_tile(x, y, True)
        else:
            raise ValueError(f"unsupported petition kind {kind!r}")
