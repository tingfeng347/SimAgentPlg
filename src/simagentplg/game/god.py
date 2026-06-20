from __future__ import annotations

from simagentplg.game.world import (
    RESOURCE_TYPES,
    SETTLEMENT_IDLE_COST,
    WEATHER_TYPES,
    WorldState,
)

DEFAULT_WEATHER_DURATION = {
    "clear": 0,
    "rain": 5,
    "drought": 5,
    "storm": 3,
}


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

    def set_weather(self, x: int, y: int, weather: str, duration: int | None = None) -> None:
        if weather not in WEATHER_TYPES:
            raise ValueError(f"unknown weather {weather!r}")
        tile = self.world.tile_at(x, y)
        tile.weather = weather
        tile.weather_duration = (
            max(0, duration)
            if duration is not None
            else DEFAULT_WEATHER_DURATION[weather]
        )
        self.world.add_event(
            "god",
            f"God changed weather at ({x}, {y}) to {weather} for {tile.weather_duration} ticks",
            faction_id=tile.owner,
        )

    def claim_tile(self, faction_id: str, x: int, y: int) -> None:
        if faction_id not in self.world.factions:
            raise ValueError(f"unknown faction {faction_id!r}")
        tile = self.world.tile_at(x, y)
        if not tile.is_passable():
            raise ValueError("cannot assign impassable territory")
        previous = tile.owner
        if previous != faction_id and not _borders_owned_tile(
            self.world,
            faction_id,
            x,
            y,
        ):
            raise ValueError("territory claim target must border owned territory")
        moved = 0
        if tile.population_of(faction_id) <= 0:
            donor = _donor_tile(
                self.world,
                faction_id,
                target=(x, y),
                exclude=(x, y),
            )
            if donor is None:
                raise ValueError(
                    f"faction {faction_id!r} has no idle population for territory claim"
                )
            _remove_idle_people(donor, faction_id, SETTLEMENT_IDLE_COST)
            tile.set_population(faction_id, SETTLEMENT_IDLE_COST)
            moved = SETTLEMENT_IDLE_COST
        for other_id in list(tile.population):
            if other_id != faction_id:
                tile.set_population(other_id, 0)
        for other_id in list(tile.soldiers):
            if other_id != faction_id:
                tile.soldiers.pop(other_id, None)
        tile.owner = faction_id
        self.world.add_event(
            "god",
            f"God assigned tile ({x}, {y}) from {previous} to {faction_id} with {moved} moved people",
            faction_id=faction_id,
        )
        if previous is not None:
            self.world.eliminate_faction_if_home_captured(previous, faction_id)
        self.world.enforce_population_ownership()

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
            duration = request.get("duration")
            self.set_weather(
                x,
                y,
                weather,
                int(duration) if duration is not None else None,
            )
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


def _borders_owned_tile(
    world: WorldState,
    faction_id: str,
    x: int,
    y: int,
) -> bool:
    return any(tile.owner == faction_id for tile in world.neighbors(x, y))


def _donor_tile(
    world: WorldState,
    faction_id: str,
    *,
    target: tuple[int, int],
    exclude: tuple[int, int],
):
    candidates = [
        tile
        for tile in world.faction_tiles(faction_id)
        if (tile.x, tile.y) != exclude
        and abs(tile.x - target[0]) + abs(tile.y - target[1]) == 1
        and _idle_count(tile, faction_id) >= SETTLEMENT_IDLE_COST
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda tile: _idle_count(tile, faction_id))


def _idle_count(tile, faction_id: str) -> int:
    return tile.professions_of(faction_id).get("idle", 0)


def _remove_idle_people(tile, faction_id: str, amount: int) -> int:
    tile.ensure_professions(faction_id)
    jobs = tile.professions.get(faction_id)
    if jobs is None:
        return 0
    moved = min(amount, jobs.get("idle", 0), tile.population_of(faction_id))
    if moved <= 0:
        return 0
    jobs["idle"] = jobs.get("idle", 0) - moved
    tile.population[faction_id] = tile.population_of(faction_id) - moved
    if tile.population[faction_id] <= 0:
        tile.population.pop(faction_id, None)
    tile.ensure_professions(faction_id)
    return moved
