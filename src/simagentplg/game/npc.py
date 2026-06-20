from __future__ import annotations

from typing import Any

from simagentplg.game.world import PROFESSION_TYPES, RESOURCE_TYPES, WorldState

HOUSE_WOOD_COST = 10

TASK_TO_PROFESSION = {
    "farm": "farmer",
    "gather_wood": "lumberjack",
    "mine_stone": "miner",
    "build": "builder",
}


class NPCExecutor:
    """Deterministic population simulation that obeys validated leader orders."""

    def apply_passive_tick(self, world: WorldState) -> None:
        for faction_id, faction in world.factions.items():
            produced = {resource: 0 for resource in RESOURCE_TYPES}
            for tile in world.faction_tiles(faction_id):
                population = tile.population_of(faction_id)
                if population <= 0:
                    continue
                tile.ensure_professions(faction_id)
                jobs = tile.professions_of(faction_id)
                produced["food"] += _food_output(tile, jobs["farmer"])
                produced["wood"] += _wood_output(tile, jobs["lumberjack"])
                produced["stone"] += _stone_output(tile, jobs["miner"])
                if tile.weather == "storm":
                    self._apply_storm_damage(world, faction_id, tile)
                elif tile.weather == "drought" and world.tick % 3 == 0:
                    self._apply_drought_damage(world, faction_id, tile)

            for resource, amount in produced.items():
                if amount > 0:
                    faction.resources.add(resource, amount)
            if any(produced.values()):
                world.add_event(
                    "resource",
                    (
                        f"{faction_id} produced food={produced['food']} "
                        f"wood={produced['wood']} stone={produced['stone']}"
                    ),
                    faction_id=faction_id,
                )
            self._consume_food(world, faction_id)
            if world.tick % 5 == 0:
                self._grow_population(world, faction_id)
        world.enforce_population_ownership()

    def execute_active_orders(self, world: WorldState, faction_id: str) -> None:
        faction = world.factions[faction_id]
        orders = faction.active_orders
        if not orders:
            return

        self._execute_resource_orders(world, faction_id, orders.get("resource_orders", []))
        self._execute_population_orders(world, faction_id, orders.get("population_orders", []))
        self._execute_territory_orders(world, faction_id, orders.get("territory_orders", []))
        self._execute_military_orders(world, faction_id, orders.get("military_orders", []))
        faction.last_plan_snapshot["after_execution"] = _faction_execution_snapshot(
            world,
            faction_id,
        )
        faction.active_orders = {}
        world.enforce_population_ownership()

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
        for order in sorted(orders, key=lambda item: item.get("priority", 1)):
            task = order.get("task", "idle")
            target = _target_tuple(order.get("target"))
            workers = max(0, int(order.get("workers", 0)))
            if workers <= 0:
                continue
            if task in TASK_TO_PROFESSION:
                tile = _owned_target_or_largest(world, faction_id, target)
                if tile is None:
                    continue
                assigned = self._assign_profession(
                    tile,
                    faction_id,
                    TASK_TO_PROFESSION[task],
                    workers,
                )
                world.add_event(
                    "population",
                    f"{faction_id} assigned {assigned} {TASK_TO_PROFESSION[task]} at ({tile.x}, {tile.y})",
                    faction_id=faction_id,
                )
                if task == "build":
                    self._build_houses(world, faction_id, tile, assigned)
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
                tile.houses = 0
                tile.set_population(faction_id, 0)
                tile.soldiers.pop(faction_id, None)
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
                    self._resolve_attack(
                        world,
                        faction_id,
                        origin,
                        target,
                        force_ratio,
                        capture=action == "attack",
                    )
            elif action in {"muster", "defend"}:
                origin = _target_tuple(order.get("origin"))
                if origin is not None:
                    self._train_soldiers(world, faction_id, origin, workers=8)

    def _assign_profession(
        self,
        tile,
        faction_id: str,
        profession: str,
        workers: int,
    ) -> int:
        if profession not in PROFESSION_TYPES:
            return 0
        tile.ensure_professions(faction_id)
        jobs = tile.professions[faction_id]
        assigned = min(workers, tile.population_of(faction_id))
        moved = min(jobs.get("idle", 0), assigned)
        jobs["idle"] = jobs.get("idle", 0) - moved
        jobs[profession] = jobs.get(profession, 0) + moved
        tile.ensure_professions(faction_id)
        return moved

    def _build_houses(self, world: WorldState, faction_id: str, tile, builders: int) -> None:
        faction = world.factions[faction_id]
        if builders <= 0 or faction.resources.wood < HOUSE_WOOD_COST:
            return
        houses = min(max(1, builders // 5), faction.resources.wood // HOUSE_WOOD_COST)
        if houses <= 0:
            return
        faction.resources.remove("wood", houses * HOUSE_WOOD_COST)
        tile.houses += houses
        world.add_event(
            "build",
            f"{faction_id} built {houses} houses at ({tile.x}, {tile.y})",
            faction_id=faction_id,
        )

    def _settle_tile(
        self,
        world: WorldState,
        faction_id: str,
        target: tuple[int, int],
    ) -> None:
        target_tile = world.tile_at(*target)
        if target_tile.owner is not None:
            return
        donor = _largest_population_tile(world, faction_id)
        moved = 0
        if donor is not None and donor.population_of(faction_id) > 8:
            moved = min(8, donor.population_of(faction_id) // 4, target_tile.capacity())
            donor.set_population(faction_id, donor.population_of(faction_id) - moved)
            target_tile.set_population(faction_id, moved)
        if moved <= 0:
            world.add_event(
                "territory",
                f"{faction_id} failed to settle tile {target} because no movable people were available",
                faction_id=faction_id,
            )
            return
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
        tile.set_population(faction_id, population - trained)
        tile.soldiers[faction_id] = tile.soldiers_of(faction_id) + trained
        world.add_event(
            "military",
            f"{faction_id} trained {trained} soldiers at {target}",
            faction_id=faction_id,
        )

    def _consume_food(self, world: WorldState, faction_id: str) -> None:
        faction = world.factions[faction_id]
        population = world.total_population(faction_id)
        soldiers = world.total_soldiers(faction_id)
        if population <= 0 and soldiers <= 0:
            return
        needed = max(1, (population + soldiers) // 30)
        available = faction.resources.food
        if available >= needed:
            faction.resources.remove("food", needed)
            return

        if available > 0:
            faction.resources.remove("food", available)
        shortage = needed - available
        losses = max(1, shortage // 2)
        lost = self._remove_population(world, faction_id, losses)
        if lost:
            world.add_event(
                "population",
                f"{faction_id} lost {lost} people to starvation",
                faction_id=faction_id,
            )

    def _grow_population(self, world: WorldState, faction_id: str) -> None:
        faction = world.factions[faction_id]
        if faction.resources.food < 20:
            return
        grown = 0
        food_spent = 0
        for tile in world.faction_tiles(faction_id):
            population = tile.population_of(faction_id)
            if population <= 0 or tile.weather in {"drought", "storm"}:
                continue
            capacity = tile.capacity()
            if population >= capacity:
                continue
            base_growth = max(1, population // 40)
            if tile.weather == "rain":
                base_growth += 1
            food_budget = max(0, (faction.resources.food - food_spent - 20) // 2)
            growth = min(base_growth, capacity - population, food_budget)
            if growth <= 0:
                continue
            tile.change_population(faction_id, growth)
            grown += growth
            food_spent += growth * 2
        if food_spent:
            faction.resources.remove("food", food_spent)
        if grown:
            world.add_event(
                "population",
                f"{faction_id} population grew by {grown}",
                faction_id=faction_id,
            )

    def _apply_storm_damage(self, world: WorldState, faction_id: str, tile) -> None:
        population = tile.population_of(faction_id)
        soldiers = tile.soldiers_of(faction_id)
        population_loss = 1 if population > 0 else 0
        soldier_loss = 1 if soldiers > 0 and world.tick % 2 == 0 else 0
        if population_loss:
            tile.set_population(faction_id, population - population_loss)
        if soldier_loss:
            tile.soldiers[faction_id] = max(0, soldiers - soldier_loss)
        if population_loss or soldier_loss:
            world.add_event(
                "weather",
                (
                    f"storm at ({tile.x}, {tile.y}) cost {faction_id} "
                    f"{population_loss} people and {soldier_loss} soldiers"
                ),
                faction_id=faction_id,
            )

    def _apply_drought_damage(self, world: WorldState, faction_id: str, tile) -> None:
        population = tile.population_of(faction_id)
        if population <= 0:
            return
        loss = max(1, population // 30)
        tile.set_population(faction_id, population - loss)
        world.add_event(
            "weather",
            f"drought at ({tile.x}, {tile.y}) cost {faction_id} {loss} people",
            faction_id=faction_id,
        )

    def _remove_population(
        self,
        world: WorldState,
        faction_id: str,
        amount: int,
    ) -> int:
        remaining = amount
        lost = 0
        for tile in sorted(
            world.faction_tiles(faction_id),
            key=lambda item: item.population_of(faction_id),
            reverse=True,
        ):
            if remaining <= 0:
                break
            population = tile.population_of(faction_id)
            if population <= 0:
                continue
            remove = min(population, remaining)
            tile.set_population(faction_id, population - remove)
            remaining -= remove
            lost += remove
        world.enforce_population_ownership()
        return lost

    def _resolve_attack(
        self,
        world: WorldState,
        faction_id: str,
        origin: tuple[int, int],
        target: tuple[int, int],
        force_ratio: float,
        *,
        capture: bool,
    ) -> None:
        origin_tile = world.tile_at(*origin)
        target_tile = world.tile_at(*target)
        defender_id = target_tile.owner
        if defender_id is None or defender_id == faction_id:
            return
        world.factions[faction_id].diplomacy[defender_id] = "war"
        world.factions[defender_id].diplomacy[faction_id] = "war"
        world.factions[faction_id].known_factions.add(defender_id)
        world.factions[defender_id].known_factions.add(faction_id)

        available = origin_tile.soldiers_of(faction_id)
        attackers = max(1, int(available * force_ratio))
        attackers = min(attackers, available)
        if attackers <= 0:
            return
        defenders = target_tile.soldiers_of(defender_id)
        terrain_bonus = 1.35 if target_tile.terrain in {"hill", "forest"} else 1.0
        weather_penalty = 0.75 if target_tile.weather == "storm" else 1.0
        attack_power = attackers * weather_penalty
        defense_power = max(1.0, defenders * terrain_bonus)
        origin_tile.soldiers[faction_id] = max(0, available - attackers)

        if attack_power <= defense_power:
            target_tile.soldiers[defender_id] = max(0, defenders - attackers // 3)
            world.add_event(
                "battle",
                f"{faction_id} attacked {defender_id} at {target} and failed",
                faction_id=faction_id,
            )
            return

        survivors = max(1, int(attackers - defenders * 0.6))
        target_tile.soldiers[defender_id] = 0
        loot = _loot_from_defender(world, defender_id)
        for resource, amount in loot.items():
            if amount <= 0:
                continue
            world.factions[defender_id].resources.remove(resource, amount)
            world.factions[faction_id].resources.add(resource, amount)

        if not capture:
            origin_tile.soldiers[faction_id] = origin_tile.soldiers_of(faction_id) + survivors
            _damage_defender_population(target_tile, defender_id, severe=False)
            world.add_event(
                "battle",
                f"{faction_id} raided {target} from {defender_id} and took {_format_loot(loot)}",
                faction_id=faction_id,
            )
            return

        migrants = min(3, max(0, origin_tile.population_of(faction_id) - 1), target_tile.capacity())
        if migrants <= 0:
            origin_tile.soldiers[faction_id] = origin_tile.soldiers_of(faction_id) + survivors
            _damage_defender_population(target_tile, defender_id, severe=False)
            world.add_event(
                "battle",
                f"{faction_id} won at {target} but could not occupy without movable people and took {_format_loot(loot)}",
                faction_id=faction_id,
            )
            return

        origin_tile.set_population(faction_id, origin_tile.population_of(faction_id) - migrants)
        for other_id in list(target_tile.population):
            if other_id != faction_id:
                target_tile.set_population(other_id, 0)
        for other_id in list(target_tile.soldiers):
            if other_id != faction_id:
                target_tile.soldiers.pop(other_id, None)
        target_tile.owner = faction_id
        target_tile.set_population(
            faction_id,
            target_tile.population_of(faction_id) + migrants,
        )
        target_tile.soldiers[faction_id] = target_tile.soldiers_of(faction_id) + survivors
        world.add_event(
            "battle",
            f"{faction_id} captured {target} from {defender_id} with {migrants} settlers and took {_format_loot(loot)}",
            faction_id=faction_id,
        )


def _weather_multiplier(weather: str) -> float:
    if weather == "rain":
        return 1.35
    if weather == "drought":
        return 0.35
    if weather == "storm":
        return 0.45
    return 1.0


def _food_output(tile, farmers: int) -> int:
    if farmers <= 0:
        return 0
    terrain_factor = {
        "plain": 1.0,
        "forest": 0.45,
        "hill": 0.25,
    }.get(tile.terrain, 0.0)
    return max(0, int(farmers * terrain_factor * _weather_multiplier(tile.weather) / 5))


def _wood_output(tile, lumberjacks: int) -> int:
    if lumberjacks <= 0:
        return 0
    divisor = 5 if tile.terrain == "forest" else 10
    if tile.weather == "storm":
        divisor += 3
    return max(0, lumberjacks // divisor)


def _stone_output(tile, miners: int) -> int:
    if miners <= 0:
        return 0
    divisor = 5 if tile.terrain == "hill" else 14
    if tile.weather == "storm":
        divisor += 4
    return max(0, miners // divisor)


def _target_tuple(payload: Any) -> tuple[int, int] | None:
    if not isinstance(payload, dict):
        return None
    try:
        return (int(payload["x"]), int(payload["y"]))
    except (KeyError, TypeError, ValueError):
        return None


def _owned_target_or_largest(world: WorldState, faction_id: str, target: tuple[int, int] | None):
    if target is not None and world.in_bounds(*target):
        tile = world.tile_at(*target)
        if tile.owner == faction_id:
            return tile
    return _largest_population_tile(world, faction_id)


def _largest_population_tile(world: WorldState, faction_id: str):
    owned = world.faction_tiles(faction_id)
    if not owned:
        return None
    return max(owned, key=lambda tile: tile.population_of(faction_id))


def _loot_from_defender(world: WorldState, defender_id: str) -> dict[str, int]:
    territory = max(1, len(world.faction_tiles(defender_id)))
    return {
        resource: world.factions[defender_id].resources.amount(resource) // territory
        for resource in RESOURCE_TYPES
    }


def _damage_defender_population(tile, defender_id: str, *, severe: bool) -> None:
    population = tile.population_of(defender_id)
    if population <= 0:
        return
    divisor = 3 if severe else 5
    loss = max(1, population // divisor)
    tile.set_population(defender_id, population - loss)


def _format_loot(loot: dict[str, int]) -> str:
    return ",".join(f"{resource}={amount}" for resource, amount in loot.items())


def _faction_execution_snapshot(world: WorldState, faction_id: str) -> dict[str, Any]:
    return {
        "tick": world.tick,
        "resources": world.factions[faction_id].resources.as_dict(),
        "population": world.total_population(faction_id),
        "soldiers": world.total_soldiers(faction_id),
        "jobs": world.total_jobs(faction_id),
        "houses": world.total_houses(faction_id),
        "population_capacity": world.population_capacity(faction_id),
        "territory_count": len(world.faction_tiles(faction_id)),
    }
