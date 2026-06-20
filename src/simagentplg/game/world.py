from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

RESOURCE_TYPES = ("food", "wood", "stone")
WEATHER_TYPES = ("clear", "rain", "drought", "storm")
TERRAIN_TYPES = ("plain", "forest", "hill", "water", "mountain")
DEFAULT_FACTIONS = ("human", "elf", "orc")
PROFESSION_TYPES = ("farmer", "lumberjack", "miner", "builder", "idle")
SETTLEMENT_IDLE_COST = 3
BASE_TILE_CAPACITY = {
    "plain": 60,
    "forest": 45,
    "hill": 35,
    "water": 0,
    "mountain": 0,
}
HOUSE_CAPACITY = 5


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
    weather_duration: int = 0
    population: dict[str, int] = field(default_factory=dict)
    soldiers: dict[str, int] = field(default_factory=dict)
    professions: dict[str, dict[str, int]] = field(default_factory=dict)
    houses: int = 0
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

    def professions_of(self, faction_id: str) -> dict[str, int]:
        self.ensure_professions(faction_id)
        return dict(self.professions.get(faction_id, {}))

    def ensure_professions(self, faction_id: str) -> None:
        population = self.population_of(faction_id)
        current = self.professions.setdefault(
            faction_id,
            {profession: 0 for profession in PROFESSION_TYPES},
        )
        for profession in PROFESSION_TYPES:
            current.setdefault(profession, 0)
        total = sum(current.values())
        if total < population:
            current["idle"] += population - total
        elif total > population:
            surplus = total - population
            for profession in ("idle", "builder", "miner", "lumberjack", "farmer"):
                remove = min(current.get(profession, 0), surplus)
                current[profession] = current.get(profession, 0) - remove
                surplus -= remove
                if surplus <= 0:
                    break
        if population <= 0:
            self.professions.pop(faction_id, None)

    def set_population(self, faction_id: str, amount: int) -> None:
        self.population[faction_id] = max(0, amount)
        if self.population[faction_id] <= 0:
            self.population.pop(faction_id, None)
        self.ensure_professions(faction_id)

    def change_population(self, faction_id: str, delta: int) -> int:
        before = self.population_of(faction_id)
        after = max(0, before + delta)
        self.set_population(faction_id, after)
        return after - before

    def capacity(self) -> int:
        return BASE_TILE_CAPACITY[self.terrain] + self.houses * HOUSE_CAPACITY

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
    known_factions: set[str] = field(default_factory=set)
    last_plan_snapshot: dict[str, Any] = field(default_factory=dict)

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

    def total_houses(self, faction_id: str) -> int:
        return sum(tile.houses for tile in self.faction_tiles(faction_id))

    def population_capacity(self, faction_id: str) -> int:
        return sum(tile.capacity() for tile in self.faction_tiles(faction_id))

    def total_jobs(self, faction_id: str) -> dict[str, int]:
        totals = {profession: 0 for profession in PROFESSION_TYPES}
        for tile in self.faction_tiles(faction_id):
            for profession, amount in tile.professions_of(faction_id).items():
                totals[profession] = totals.get(profession, 0) + amount
        return totals

    def visible_tiles(self, faction_id: str, radius: int = 2) -> set[tuple[int, int]]:
        visible: set[tuple[int, int]] = set()
        for owned in self.faction_tiles(faction_id):
            for y in range(owned.y - radius, owned.y + radius + 1):
                for x in range(owned.x - radius, owned.x + radius + 1):
                    if self.in_bounds(x, y) and abs(owned.x - x) + abs(owned.y - y) <= radius:
                        visible.add((x, y))
        return visible

    def is_visible(self, faction_id: str, x: int, y: int) -> bool:
        return (x, y) in self.visible_tiles(faction_id)

    def discover_factions(self) -> list[tuple[str, str]]:
        discoveries: list[tuple[str, str]] = []
        for faction_id, faction in self.factions.items():
            faction.known_factions.add(faction_id)
            visible = self.visible_tiles(faction_id)
            for other_id in self.factions:
                if other_id == faction_id or other_id in faction.known_factions:
                    continue
                if any((tile.x, tile.y) in visible for tile in self.faction_tiles(other_id)):
                    faction.known_factions.add(other_id)
                    discoveries.append((faction_id, other_id))
        return discoveries

    def enforce_population_ownership(self) -> None:
        for tile in self.tiles:
            for faction_id in list(tile.population):
                if tile.population_of(faction_id) <= 0:
                    tile.population.pop(faction_id, None)
                    tile.professions.pop(faction_id, None)
            if tile.owner is not None and tile.population_of(tile.owner) <= 0:
                previous = tile.owner
                tile.owner = None
                tile.houses = 0
                self.add_event(
                    "territory",
                    f"{previous} lost tile ({tile.x}, {tile.y}) because no people remained",
                    faction_id=previous,
                )

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
        signature = _petition_signature(faction_id, kind, request)
        for petition in self.petitions:
            if petition.status != "pending":
                continue
            if _petition_signature(petition.faction_id, petition.kind, petition.request) != signature:
                continue
            petition.request = _merge_petition_request(kind, petition.request, request)
            petition.reason = reason
            petition.urgency = _max_urgency(petition.urgency, urgency)
            petition.created_tick = self.tick
            self.add_event(
                "petition",
                f"{faction_id} updated petition for {kind}: {reason}",
                faction_id=faction_id,
            )
            return petition

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
        faction.known_factions = {faction_id}

    starts = _random_start_positions(world, rng, DEFAULT_FACTIONS)
    for faction_id, start in starts.items():
        _seed_faction_land(world, faction_id, start)

    world.discover_factions()

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
    tile = world.tile_at(cx, cy)
    if not tile.is_passable():
        tile.terrain = "plain"
    tile.owner = faction_id
    tile.houses = 16
    tile.set_population(faction_id, 40)
    tile.professions[faction_id]["farmer"] = 30
    tile.ensure_professions(faction_id)
    tile.soldiers[faction_id] = 20


def _random_start_positions(
    world: WorldState,
    rng: random.Random,
    faction_ids: tuple[str, ...],
) -> dict[str, tuple[int, int]]:
    coordinates = [(tile.x, tile.y) for tile in world.tiles]
    rng.shuffle(coordinates)
    starts: dict[str, tuple[int, int]] = {}
    for faction_id in faction_ids:
        target = _pop_random_start(
            coordinates,
            starts.values(),
            min_distance=3,
        )
        if target is None:
            target = _pop_random_start(
                coordinates,
                starts.values(),
                min_distance=1,
            )
        if target is None:
            raise ValueError("world is too small to place all factions")
        starts[faction_id] = target
    return starts


def _pop_random_start(
    coordinates: list[tuple[int, int]],
    existing: Any,
    *,
    min_distance: int,
) -> tuple[int, int] | None:
    existing_positions = list(existing)
    for index, candidate in enumerate(coordinates):
        if all(_manhattan(candidate, other) >= min_distance for other in existing_positions):
            return coordinates.pop(index)
    return None


def _manhattan(first: tuple[int, int], second: tuple[int, int]) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def _check_resource(resource: str) -> None:
    if resource not in RESOURCE_TYPES:
        raise ValueError(f"unknown resource {resource!r}")


def _petition_signature(
    faction_id: str,
    kind: str,
    request: dict[str, Any],
) -> tuple[object, ...]:
    if kind == "resources":
        return (faction_id, kind, request.get("resource"))
    if kind in {"weather", "protection", "territory"}:
        return (faction_id, kind, request.get("x"), request.get("y"))
    return (faction_id, kind)


def _merge_petition_request(
    kind: str,
    current: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    merged.update(incoming)
    if kind == "resources" and current.get("resource") == incoming.get("resource"):
        try:
            merged["amount"] = max(int(current.get("amount", 0)), int(incoming.get("amount", 0)))
        except (TypeError, ValueError):
            pass
    return merged


def _max_urgency(first: str, second: str) -> str:
    ranks = {"low": 0, "medium": 1, "high": 2}
    return first if ranks.get(first, 1) >= ranks.get(second, 1) else second
