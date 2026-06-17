from __future__ import annotations

from typing import Any

from simagentplg.game.world import WorldState


class NPCExecutor:
    """Deterministic population simulation that obeys validated leader orders."""

    def apply_passive_tick(self, world: WorldState) -> None:
        for faction_id, faction in world.factions.items():
            food = 0
            wood = 0
            stone = 0
            for tile in world.faction_tiles(faction_id):
                population = tile.population_of(faction_id)
                if population <= 0:
                    continue
                weather_multiplier = _weather_multiplier(tile.weather)
                if tile.terrain == "plain":
                    food += max(1, int(population * weather_multiplier / 12))
                elif tile.terrain == "forest":
                    wood += max(1, population // 14)
                    food += max(0, int(population * weather_multiplier / 30))
                elif tile.terrain == "hill":
                    stone += max(1, population // 18)
                if tile.weather == "storm":
                    food = max(0, food - 1)

            if food:
                faction.resources.add("food", food)
            if wood:
                faction.resources.add("wood", wood)
            if stone:
                faction.resources.add("stone", stone)

    def execute_active_orders(self, world: WorldState, faction_id: str) -> None:
        faction = world.factions[faction_id]
        orders = faction.active_orders
        if not orders:
            return

        self._execute_resource_orders(world, faction_id, orders.get("resource_orders", []))
        self._execute_population_orders(world, faction_id, orders.get("population_orders", []))
        self._execute_territory_orders(world, faction_id, orders.get("territory_orders", []))
        self._execute_military_orders(world, faction_id, orders.get("military_orders", []))
        faction.active_orders = {}

    def _execute_resource_orders(
        self,
        world: WorldState,
        faction_id: str,
        orders: list[dict[str, Any]],
    ) -> None:
        faction = world.factions[faction_id]
        for order in orders:
            resource = order.get("resource")
            action = order.get("action")
            amount = int(order.get("amount", 0))
            if action in {"spend", "trade", "tribute"} and amount > 0:
                faction.resources.remove(resource, amount)
                world.add_event(
                    "resource",
                    f"{faction_id} used {amount} {resource} for {action}",
                    faction_id=faction_id,
                )

    def _execute_population_orders(
        self,
        world: WorldState,
        faction_id: str,
        orders: list[dict[str, Any]],
    ) -> None:
        faction = world.factions[faction_id]
        for order in sorted(orders, key=lambda item: item.get("priority", 1)):
            task = order.get("task", "idle")
            target = _target_tuple(order.get("target"))
            workers = max(0, int(order.get("workers", 0)))
            if workers <= 0:
                continue
            if task == "farm":
                faction.resources.add("food", max(1, workers // 3))
            elif task == "gather_wood":
                faction.resources.add("wood", max(1, workers // 4))
            elif task == "mine_stone":
                faction.resources.add("stone", max(1, workers // 5))
            elif task in {"train", "defend"} and target is not None:
                self._train_soldiers(world, faction_id, target, workers)
            elif task == "settle" and target is not None:
                self._settle_tile(world, faction_id, target)
            elif task == "scout":
                world.add_event(
                    "scout",
                    f"{faction_id} scouts around {target}",
                    faction_id=faction_id,
                )

    def _execute_territory_orders(
        self,
        world: WorldState,
        faction_id: str,
        orders: list[dict[str, Any]],
    ) -> None:
        for order in sorted(orders, key=lambda item: item.get("priority", 1)):
            action = order.get("action")
            target = _target_tuple(order.get("target"))
            if target is None:
                continue
            if action in {"claim", "settle"}:
                self._settle_tile(world, faction_id, target)
            elif action == "fortify":
                self._train_soldiers(world, faction_id, target, workers=10)
            elif action == "abandon":
                tile = world.tile_at(*target)
                tile.owner = None
                world.add_event(
                    "territory",
                    f"{faction_id} abandoned tile {target}",
                    faction_id=faction_id,
                )

    def _execute_military_orders(
        self,
        world: WorldState,
        faction_id: str,
        orders: list[dict[str, Any]],
    ) -> None:
        for order in sorted(orders, key=lambda item: item.get("priority", 1)):
            action = order.get("action")
            if action in {"attack", "raid"}:
                origin = _target_tuple(order.get("origin"))
                target = _target_tuple(order.get("target"))
                force_ratio = float(order.get("force_ratio", 0.5))
                if origin is not None and target is not None:
                    self._resolve_attack(world, faction_id, origin, target, force_ratio)
            elif action in {"muster", "defend"}:
                origin = _target_tuple(order.get("origin"))
                if origin is not None:
                    self._train_soldiers(world, faction_id, origin, workers=8)

    def _settle_tile(
        self,
        world: WorldState,
        faction_id: str,
        target: tuple[int, int],
    ) -> None:
        target_tile = world.tile_at(*target)
        if target_tile.owner is None:
            donor = _largest_population_tile(world, faction_id)
            moved = 0
            if donor is not None and donor.population_of(faction_id) > 8:
                moved = min(8, donor.population_of(faction_id) // 4)
                donor.population[faction_id] -= moved
                target_tile.population[faction_id] = target_tile.population_of(faction_id) + moved
            target_tile.owner = faction_id
            world.add_event(
                "territory",
                f"{faction_id} settled tile {target} with {moved} people",
                faction_id=faction_id,
            )

    def _train_soldiers(
        self,
        world: WorldState,
        faction_id: str,
        target: tuple[int, int],
        workers: int,
    ) -> None:
        tile = world.tile_at(*target)
        if tile.owner != faction_id:
            return
        trained = max(1, workers // 5)
        population = tile.population_of(faction_id)
        trained = min(trained, max(0, population // 3))
        if trained <= 0:
            return
        tile.population[faction_id] = population - trained
        tile.soldiers[faction_id] = tile.soldiers_of(faction_id) + trained
        world.add_event(
            "military",
            f"{faction_id} trained {trained} soldiers at {target}",
            faction_id=faction_id,
        )

    def _resolve_attack(
        self,
        world: WorldState,
        faction_id: str,
        origin: tuple[int, int],
        target: tuple[int, int],
        force_ratio: float,
    ) -> None:
        origin_tile = world.tile_at(*origin)
        target_tile = world.tile_at(*target)
        defender_id = target_tile.owner
        if defender_id is None or defender_id == faction_id:
            return

        available = origin_tile.soldiers_of(faction_id)
        attackers = max(1, int(available * force_ratio))
        defenders = target_tile.soldiers_of(defender_id)
        terrain_bonus = 1.35 if target_tile.terrain in {"hill", "forest"} else 1.0
        weather_penalty = 0.75 if target_tile.weather == "storm" else 1.0
        attack_power = attackers * weather_penalty
        defense_power = max(1.0, defenders * terrain_bonus)
        origin_tile.soldiers[faction_id] = max(0, available - attackers)

        if attack_power > defense_power:
            survivors = max(1, int(attackers - defenders * 0.6))
            target_tile.owner = faction_id
            target_tile.soldiers[defender_id] = 0
            target_tile.soldiers[faction_id] = target_tile.soldiers_of(faction_id) + survivors
            if target_tile.population_of(defender_id):
                losses = max(1, target_tile.population_of(defender_id) // 5)
                target_tile.population[defender_id] -= losses
            world.add_event(
                "battle",
                f"{faction_id} captured {target} from {defender_id}",
                faction_id=faction_id,
            )
        else:
            target_tile.soldiers[defender_id] = max(0, defenders - attackers // 3)
            world.add_event(
                "battle",
                f"{faction_id} attacked {defender_id} at {target} and failed",
                faction_id=faction_id,
            )


def _weather_multiplier(weather: str) -> float:
    if weather == "rain":
        return 1.3
    if weather == "drought":
        return 0.45
    if weather == "storm":
        return 0.7
    return 1.0


def _target_tuple(payload: Any) -> tuple[int, int] | None:
    if not isinstance(payload, dict):
        return None
    try:
        return (int(payload["x"]), int(payload["y"]))
    except (KeyError, TypeError, ValueError):
        return None


def _largest_population_tile(world: WorldState, faction_id: str):
    owned = world.faction_tiles(faction_id)
    if not owned:
        return None
    return max(owned, key=lambda tile: tile.population_of(faction_id))
