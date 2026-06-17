from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

RESOURCE_TYPES = ("food", "wood", "stone")
WEATHER_TYPES = ("clear", "rain", "drought", "storm")
TERRAIN_TYPES = ("plain", "forest", "hill", "water", "mountain")
DEFAULT_FACTIONS = ("human", "elf", "orc")


@dataclass(slots=True)
class ResourceStockpile:
    food: int = 0
    wood: int = 0
    stone: int = 0

    def as_dict(self) -> dict[str, int]:
        return {resource: getattr(self, resource) for resource in RESOURCE_TYPES}

    def amount(self, resource: str) -> int:
        _check_resource(resource)
        return int(getattr(self, resource))

    def add(self, resource: str, amount: int) -> None:
        _check_resource(resource)
        if amount < 0:
            raise ValueError("amount must not be negative")
        setattr(self, resource, self.amount(resource) + amount)

    def remove(self, resource: str, amount: int) -> None:
        _check_resource(resource)
        if amount < 0:
            raise ValueError("amount must not be negative")
        current = self.amount(resource)
        if current < amount:
            raise ValueError(f"not enough {resource}")
        setattr(self, resource, current - amount)


@dataclass(slots=True)
class WeatherState:
    kind: str = "clear"
    duration: int = 0

    def __post_init__(self) -> None:
        if self.kind not in WEATHER_TYPES:
            raise ValueError(f"unknown weather {self.kind!r}")
        if self.duration < 0:
            raise ValueError("duration must not be negative")


@dataclass(slots=True)
class Tile:
    x: int
    y: int
    terrain: str
    owner: str | None = None
    weather: str = "clear"
    population: dict[str, int] = field(default_factory=dict)
    soldiers: dict[str, int] = field(default_factory=dict)
    protected: bool = False

    def __post_init__(self) -> None:
        if self.terrain not in TERRAIN_TYPES:
            raise ValueError(f"unknown terrain {self.terrain!r}")
        if self.weather not in WEATHER_TYPES:
            raise ValueError(f"unknown weather {self.weather!r}")

    def population_of(self, faction_id: str) -> int:
        return self.population.get(faction_id, 0)

    def soldiers_of(self, faction_id: str) -> int:
        return self.soldiers.get(faction_id, 0)

    def is_passable(self) -> bool:
        return self.terrain not in {"water", "mountain"}


@dataclass(slots=True)
class PopulationGroup:
    faction_id: str
    workers: int
    task: str = "idle"
    target: tuple[int, int] | None = None


@dataclass(slots=True)
class Petition:
    petition_id: int
    faction_id: str
    kind: str
    request: dict[str, Any]
    reason: str
    urgency: str = "medium"
    status: str = "pending"
    created_tick: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "petition_id": self.petition_id,
            "faction_id": self.faction_id,
            "kind": self.kind,
            "request": dict(self.request),
            "reason": self.reason,
            "urgency": self.urgency,
            "status": self.status,
            "created_tick": self.created_tick,
        }


@dataclass(slots=True)
class GameEvent:
    tick: int
    kind: str
    message: str
    faction_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "kind": self.kind,
            "message": self.message,
            "faction_id": self.faction_id,
        }


@dataclass(slots=True)
class Faction:
    faction_id: str
    name: str
    leader_name: str
    resources: ResourceStockpile = field(default_factory=ResourceStockpile)
    diplomacy: dict[str, str] = field(default_factory=dict)
    active_orders: dict[str, Any] = field(default_factory=dict)

    def relation_to(self, other_faction: str) -> str:
        return self.diplomacy.get(other_faction, "neutral")


@dataclass(slots=True)
class WorldState:
    width: int
    height: int
    seed: int = 0
    tick: int = 0
    tiles: list[Tile] = field(default_factory=list)
    factions: dict[str, Faction] = field(default_factory=dict)
    events: list[GameEvent] = field(default_factory=list)
    petitions: list[Petition] = field(default_factory=list)
    paused: bool = False
    pause_reason: str | None = None
    _next_petition_id: int = 1

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("world dimensions must be positive")
        if not self.tiles:
            self.tiles = [
                Tile(x=x, y=y, terrain="plain")
                for y in range(self.height)
                for x in range(self.width)
            ]
        if len(self.tiles) != self.width * self.height:
            raise ValueError("tile count does not match world dimensions")

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def tile_at(self, x: int, y: int) -> Tile:
        if not self.in_bounds(x, y):
            raise IndexError(f"tile ({x}, {y}) is outside the world")
        return self.tiles[y * self.width + x]

    def neighbors(self, x: int, y: int) -> list[Tile]:
        positions = ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
        return [
            self.tile_at(nx, ny)
            for nx, ny in positions
            if self.in_bounds(nx, ny)
        ]

    def faction_tiles(self, faction_id: str) -> list[Tile]:
        return [tile for tile in self.tiles if tile.owner == faction_id]

    def total_population(self, faction_id: str) -> int:
        return sum(tile.population_of(faction_id) for tile in self.tiles)

    def total_soldiers(self, faction_id: str) -> int:
        return sum(tile.soldiers_of(faction_id) for tile in self.tiles)

    def visible_tiles(self, faction_id: str, radius: int = 2) -> set[tuple[int, int]]:
        visible: set[tuple[int, int]] = set()
        for owned in self.faction_tiles(faction_id):
            for y in range(owned.y - radius, owned.y + radius + 1):
                for x in range(owned.x - radius, owned.x + radius + 1):
                    if self.in_bounds(x, y):
                        visible.add((x, y))
        return visible

    def is_visible(self, faction_id: str, x: int, y: int) -> bool:
        return (x, y) in self.visible_tiles(faction_id)

    def add_event(
        self,
        kind: str,
        message: str,
        *,
        faction_id: str | None = None,
    ) -> GameEvent:
        event = GameEvent(
            tick=self.tick,
            kind=kind,
            message=message,
            faction_id=faction_id,
        )
        self.events.append(event)
        return event

    def add_petition(
        self,
        *,
        faction_id: str,
        kind: str,
        request: dict[str, Any],
        reason: str,
        urgency: str = "medium",
    ) -> Petition:
        petition = Petition(
            petition_id=self._next_petition_id,
            faction_id=faction_id,
            kind=kind,
            request=request,
            reason=reason,
            urgency=urgency,
            created_tick=self.tick,
        )
        self._next_petition_id += 1
        self.petitions.append(petition)
        self.add_event(
            "petition",
            f"{faction_id} petitioned for {kind}: {reason}",
            faction_id=faction_id,
        )
        return petition

    def pause(self, reason: str) -> None:
        self.paused = True
        self.pause_reason = reason
        self.add_event("pause", reason)

    def resume(self) -> None:
        self.paused = False
        self.pause_reason = None
        self.add_event("resume", "Simulation resumed")


def create_default_world(
    *,
    width: int = 32,
    height: int = 20,
    seed: int = 0,
) -> WorldState:
    rng = random.Random(seed)
    world = WorldState(width=width, height=height, seed=seed)

    for tile in world.tiles:
        roll = rng.random()
        if roll < 0.08:
            tile.terrain = "water"
        elif roll < 0.16:
            tile.terrain = "mountain"
        elif roll < 0.38:
            tile.terrain = "forest"
        elif roll < 0.55:
            tile.terrain = "hill"
        else:
            tile.terrain = "plain"

    starts = {
        "human": (max(1, width // 4), max(1, height // 2)),
        "elf": (min(width - 2, (width * 3) // 4), max(1, height // 3)),
        "orc": (min(width - 2, (width * 3) // 4), min(height - 2, (height * 2) // 3)),
    }
    names = {
        "human": ("Human", "High Steward"),
        "elf": ("Elf", "Moon Speaker"),
        "orc": ("Orc", "Iron Chieftain"),
    }

    for faction_id in DEFAULT_FACTIONS:
        name, leader_name = names[faction_id]
        faction = Faction(
            faction_id=faction_id,
            name=name,
            leader_name=leader_name,
            resources=ResourceStockpile(food=120, wood=80, stone=40),
        )
        world.factions[faction_id] = faction

    for faction_id, faction in world.factions.items():
        faction.diplomacy = {
            other_id: "neutral"
            for other_id in world.factions
            if other_id != faction_id
        }

    for faction_id, center in starts.items():
        _seed_faction_land(world, faction_id, center)

    world.add_event(
        "world",
        f"World created with seed {seed}, {width}x{height} tiles",
    )
    return world


def _seed_faction_land(
    world: WorldState,
    faction_id: str,
    center: tuple[int, int],
) -> None:
    cx, cy = center
    claimed = [(cx, cy), (cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)]
    first = True
    for x, y in claimed:
        if not world.in_bounds(x, y):
            continue
        tile = world.tile_at(x, y)
        tile.terrain = "plain" if first else tile.terrain
        if not tile.is_passable():
            tile.terrain = "plain"
        tile.owner = faction_id
        tile.population[faction_id] = 60 if first else 10
        tile.soldiers[faction_id] = 12 if first else 2
        first = False


def _check_resource(resource: str) -> None:
    if resource not in RESOURCE_TYPES:
        raise ValueError(f"unknown resource {resource!r}")
