from __future__ import annotations

from dataclasses import dataclass

from simagentplg.game.leader import LeaderDecision, validate_resource_name
from simagentplg.game.world import (
    RESOURCE_TYPES,
    WEATHER_TYPES,
    Petition,
    WorldState,
)

POPULATION_TASKS = {
    "farm",
    "gather_wood",
    "mine_stone",
    "build",
    "settle",
    "train",
    "defend",
    "attack",
    "scout",
    "idle",
}
RESOURCE_ACTIONS = {"reserve", "spend", "trade", "tribute"}
TERRITORY_ACTIONS = {"claim", "settle", "fortify", "abandon", "scout"}
MILITARY_ACTIONS = {"muster", "defend", "attack", "raid", "retreat"}
DIPLOMACY_PROPOSALS = {
    "alliance",
    "trade",
    "non_aggression",
    "tribute",
    "peace",
    "war",
}
PETITION_TYPES = {"resources", "weather", "protection", "territory"}


@dataclass(frozen=True, slots=True)
class RuleCheck:
    accepted: bool
    errors: tuple[str, ...] = ()


class RuleEngine:
    """Validate and stage LLM leader decisions against hard world rules."""

    def validate_decision(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> RuleCheck:
        errors: list[str] = []
        if faction_id not in world.factions:
            return RuleCheck(False, (f"unknown faction {faction_id!r}",))
        if not decision.turn_intent:
            errors.append("turn_intent is required")

        errors.extend(self._validate_population_orders(world, faction_id, decision))
        errors.extend(self._validate_resource_orders(world, faction_id, decision))
        errors.extend(self._validate_territory_orders(world, faction_id, decision))
        errors.extend(self._validate_military_orders(world, faction_id, decision))
        errors.extend(self._validate_diplomacy_orders(world, faction_id, decision))
        errors.extend(self._validate_petitions(world, faction_id, decision))
        return RuleCheck(not errors, tuple(errors))

    def apply_decision(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> RuleCheck:
        check = self.validate_decision(world, faction_id, decision)
        if not check.accepted:
            return check

        faction = world.factions[faction_id]
        faction.active_orders = decision.as_dict()
        for order in decision.diplomacy_orders:
            relation, changed = self._apply_diplomacy(
                world,
                faction_id,
                order.target_faction,
                order.proposal,
            )
            if not changed:
                continue
            world.add_event(
                "diplomacy",
                f"{faction_id} set relation with {order.target_faction} to {relation}",
                faction_id=faction_id,
            )

        for petition in decision.petitions:
            world.add_petition(
                faction_id=faction_id,
                kind=petition.kind,
                request=petition.request,
                reason=petition.reason or decision.turn_intent,
                urgency=petition.urgency,
            )

        if decision.public_decree:
            world.add_event(
                "decree",
                f"{faction_id} decreed: {decision.public_decree}",
                faction_id=faction_id,
            )
        world.add_event(
            "leader",
            f"{faction_id} submitted plan: {decision.strategy_summary or decision.turn_intent}",
            faction_id=faction_id,
        )
        return check

    def _validate_population_orders(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> list[str]:
        errors: list[str] = []
        total_workers = 0
        available = world.total_population(faction_id)
        for index, order in enumerate(decision.population_orders, start=1):
            if order.task not in POPULATION_TASKS:
                errors.append(f"population_order {index}: unknown task {order.task!r}")
            if order.workers < 0:
                errors.append(f"population_order {index}: workers must not be negative")
            total_workers += max(order.workers, 0)
            if order.target is not None:
                errors.extend(
                    self._validate_visible_target(
                        world,
                        faction_id,
                        order.target,
                        f"population_order {index}",
                    )
                )
        if total_workers > available:
            errors.append(
                f"population orders assign {total_workers} workers but only {available} population exist"
            )
        return errors

    def _validate_resource_orders(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> list[str]:
        errors: list[str] = []
        reserved: dict[str, int] = {resource: 0 for resource in RESOURCE_TYPES}
        faction = world.factions[faction_id]
        for index, order in enumerate(decision.resource_orders, start=1):
            if not validate_resource_name(order.resource):
                errors.append(f"resource_order {index}: unknown resource {order.resource!r}")
                continue
            if order.action not in RESOURCE_ACTIONS:
                errors.append(f"resource_order {index}: unknown action {order.action!r}")
            if order.amount < 0:
                errors.append(f"resource_order {index}: amount must not be negative")
            if order.action in {"spend", "trade", "tribute"}:
                reserved[order.resource] += max(order.amount, 0)
                if reserved[order.resource] > faction.resources.amount(order.resource):
                    errors.append(
                        f"resource_order {index}: not enough {order.resource}"
                    )
        return errors

    def _validate_territory_orders(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> list[str]:
        errors: list[str] = []
        for index, order in enumerate(decision.territory_orders, start=1):
            label = f"territory_order {index}"
            if order.action not in TERRITORY_ACTIONS:
                errors.append(f"{label}: unknown action {order.action!r}")
            errors.extend(self._validate_visible_target(world, faction_id, order.target, label))
            if errors and not world.in_bounds(*order.target):
                continue
            if not world.in_bounds(*order.target):
                continue
            tile = world.tile_at(*order.target)
            if order.action in {"claim", "settle"}:
                if not tile.is_passable():
                    errors.append(f"{label}: target terrain is not passable")
                if tile.owner not in {None, faction_id}:
                    errors.append(f"{label}: target is owned by {tile.owner}")
                if not self._adjacent_to_owned(world, faction_id, order.target):
                    errors.append(f"{label}: target must border owned territory")
            if order.action in {"fortify", "abandon"} and tile.owner != faction_id:
                errors.append(f"{label}: target must be owned by {faction_id}")
        return errors

    def _validate_military_orders(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> list[str]:
        errors: list[str] = []
        declared_war_targets = {
            order.target_faction
            for order in decision.diplomacy_orders
            if order.proposal == "war"
        }
        for index, order in enumerate(decision.military_orders, start=1):
            label = f"military_order {index}"
            if order.action not in MILITARY_ACTIONS:
                errors.append(f"{label}: unknown action {order.action!r}")
            if not 0 < order.force_ratio <= 1:
                errors.append(f"{label}: force_ratio must be > 0 and <= 1")
            if order.origin is not None:
                errors.extend(self._validate_visible_target(world, faction_id, order.origin, label))
                if world.in_bounds(*order.origin):
                    origin_tile = world.tile_at(*order.origin)
                    if origin_tile.owner != faction_id:
                        errors.append(f"{label}: origin must be owned by {faction_id}")
                    if origin_tile.soldiers_of(faction_id) <= 0:
                        errors.append(f"{label}: origin has no soldiers")
            if order.target is not None:
                errors.extend(self._validate_visible_target(world, faction_id, order.target, label))
            if order.action in {"attack", "raid"}:
                if order.origin is None or order.target is None:
                    errors.append(f"{label}: attack and raid require origin and target")
                    continue
                if not world.in_bounds(*order.origin) or not world.in_bounds(*order.target):
                    continue
                if not self._adjacent(order.origin, order.target):
                    errors.append(f"{label}: target must be adjacent to origin")
                target_tile = world.tile_at(*order.target)
                if target_tile.owner in {None, faction_id}:
                    errors.append(f"{label}: target must be enemy-owned")
                elif (
                    world.factions[faction_id].relation_to(target_tile.owner) == "allied"
                    and target_tile.owner not in declared_war_targets
                ):
                    errors.append(f"{label}: must declare war before attacking an ally")
                if target_tile.protected:
                    errors.append(f"{label}: target is protected by god")
        return errors

    def _validate_diplomacy_orders(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> list[str]:
        errors: list[str] = []
        for index, order in enumerate(decision.diplomacy_orders, start=1):
            if order.target_faction not in world.factions:
                errors.append(f"diplomacy_order {index}: unknown target faction")
            if order.target_faction == faction_id:
                errors.append(f"diplomacy_order {index}: cannot target self")
            if order.proposal not in DIPLOMACY_PROPOSALS:
                errors.append(f"diplomacy_order {index}: unknown proposal {order.proposal!r}")
        return errors

    def _validate_petitions(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> list[str]:
        errors: list[str] = []
        for index, petition in enumerate(decision.petitions, start=1):
            label = f"petition {index}"
            if petition.kind not in PETITION_TYPES:
                errors.append(f"{label}: unknown type {petition.kind!r}")
                continue
            if not petition.reason:
                errors.append(f"{label}: reason is required")

            if petition.kind == "resources":
                resource = str(petition.request.get("resource", "")).strip()
                amount = _int(petition.request.get("amount"), 0)
                if resource not in RESOURCE_TYPES:
                    errors.append(f"{label}: resource petition needs a valid resource")
                if amount <= 0:
                    errors.append(f"{label}: resource petition amount must be positive")
                if amount > 250:
                    errors.append(f"{label}: resource petition amount exceeds god-grant limit")
            elif petition.kind == "weather":
                target = _request_target(petition.request)
                weather = str(petition.request.get("weather", "")).strip()
                if target is None:
                    errors.append(f"{label}: weather petition needs x and y")
                else:
                    errors.extend(self._validate_visible_target(world, faction_id, target, label))
                if weather not in WEATHER_TYPES:
                    errors.append(f"{label}: weather petition needs a valid weather")
            elif petition.kind == "protection":
                target = _request_target(petition.request)
                if target is None:
                    errors.append(f"{label}: protection petition needs x and y")
                else:
                    errors.extend(self._validate_visible_target(world, faction_id, target, label))
            elif petition.kind == "territory":
                target = _request_target(petition.request)
                if target is None:
                    errors.append(f"{label}: territory petition needs x and y")
                    continue
                errors.extend(self._validate_visible_target(world, faction_id, target, label))
                if not world.in_bounds(*target):
                    continue
                tile = world.tile_at(*target)
                if tile.owner is not None:
                    errors.append(f"{label}: territory petition target must be unowned")
                if not tile.is_passable():
                    errors.append(f"{label}: territory petition target must be passable")
                if not self._adjacent_to_owned(world, faction_id, target):
                    errors.append(f"{label}: territory petition target must border owned territory")
        return errors

    def _validate_visible_target(
        self,
        world: WorldState,
        faction_id: str,
        target: tuple[int, int],
        label: str,
    ) -> list[str]:
        x, y = target
        if not world.in_bounds(x, y):
            return [f"{label}: target ({x}, {y}) is out of bounds"]
        if not world.is_visible(faction_id, x, y):
            return [f"{label}: target ({x}, {y}) is not visible"]
        return []

    def _adjacent_to_owned(
        self,
        world: WorldState,
        faction_id: str,
        target: tuple[int, int],
    ) -> bool:
        x, y = target
        return any(tile.owner == faction_id for tile in world.neighbors(x, y))

    @staticmethod
    def _adjacent(first: tuple[int, int], second: tuple[int, int]) -> bool:
        return abs(first[0] - second[0]) + abs(first[1] - second[1]) == 1

    def _apply_diplomacy(
        self,
        world: WorldState,
        faction_id: str,
        other_id: str,
        proposal: str,
    ) -> tuple[str, bool]:
        current = world.factions[faction_id].relation_to(other_id)
        if proposal == "alliance":
            relation = "allied" if current in {"non_aggression", "trade", "allied"} else "non_aggression"
        else:
            relation = {
                "war": "war",
                "peace": "neutral",
                "non_aggression": "non_aggression",
                "trade": "trade",
                "tribute": "tribute",
            }[proposal]
        if (
            world.factions[faction_id].relation_to(other_id) == relation
            and world.factions[other_id].relation_to(faction_id) == relation
        ):
            return relation, False
        world.factions[faction_id].diplomacy[other_id] = relation
        world.factions[other_id].diplomacy[faction_id] = relation
        return relation, True


def _request_target(request: dict[str, object]) -> tuple[int, int] | None:
    try:
        return (int(request["x"]), int(request["y"]))
    except (KeyError, TypeError, ValueError):
        return None


def _int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
