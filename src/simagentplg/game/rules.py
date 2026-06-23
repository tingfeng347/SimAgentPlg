from __future__ import annotations

from dataclasses import dataclass

from simagentplg.game.leader import LeaderDecision, validate_resource_name
from simagentplg.game.migration import (
    CIVILIAN_PROFESSION_PRIORITY,
    CIVILIAN_PROFESSION_TYPES,
)
from simagentplg.game.world import (
    RESOURCE_TYPES,
    SETTLEMENT_IDLE_COST,
    WEATHER_TYPES,
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
}
PROFESSION_TASKS = {"farm", "gather_wood", "mine_stone", "build"}
RESOURCE_ACTIONS = {"reserve", "spend", "trade", "tribute"}
TERRITORY_ACTIONS = {"claim", "settle", "fortify", "abandon"}
MILITARY_ACTIONS = {"muster", "defend", "attack", "raid", "retreat", "move"}
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
        if world.factions[faction_id].eliminated:
            return RuleCheck(False, (f"faction {faction_id!r} has been eliminated",))
        if not decision.turn_intent:
            errors.append("turn_intent is required")

        errors.extend(self._validate_population_orders(world, faction_id, decision))
        errors.extend(self._validate_resource_orders(world, faction_id, decision))
        errors.extend(self._validate_territory_orders(world, faction_id, decision))
        errors.extend(self._validate_military_orders(world, faction_id, decision))
        errors.extend(self._validate_diplomacy_orders(world, faction_id, decision))
        errors.extend(self._validate_petitions(world, faction_id, decision))
        errors.extend(self._validate_plan_matches_actions(decision))
        errors.extend(self._validate_idle_budget(world, faction_id, decision))
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
        faction.last_plan_snapshot = {
            "tick": world.tick,
            "resources": faction.resources.as_dict(),
            "population": world.total_population(faction_id),
            "soldiers": world.total_soldiers(faction_id),
            "jobs": world.total_jobs(faction_id),
            "houses": world.total_houses(faction_id),
            "population_capacity": world.population_capacity(faction_id),
            "strategy_summary": decision.strategy_summary or decision.turn_intent,
            "public_decree": decision.public_decree,
            "orders": decision.as_dict(),
        }
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
            target_tile = None
            if order.target is not None:
                errors.extend(
                    self._validate_visible_target(
                        world,
                        faction_id,
                        order.target,
                        f"population_order {index}",
                    )
                )
                if world.in_bounds(*order.target):
                    target_tile = world.tile_at(*order.target)
                    if order.task in {"farm", "gather_wood", "mine_stone", "build", "train", "defend"}:
                        if target_tile.owner != faction_id:
                            errors.append(
                                f"population_order {index}: task {order.task!r} target must be owned by {faction_id}"
                            )
            elif order.task in PROFESSION_TASKS:
                target_tile = _largest_population_tile(world, faction_id)
            if target_tile is not None and order.task in PROFESSION_TASKS:
                idle = target_tile.professions_of(faction_id).get("idle", 0)
                if order.workers > idle:
                    errors.append(
                        f"population_order {index}: task {order.task!r} assigns {order.workers} workers but target has only {idle} idle population"
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
        has_build_order = any(
            order.task == "build" and order.workers > 0
            for order in decision.population_orders
        )
        for index, order in enumerate(decision.resource_orders, start=1):
            if not validate_resource_name(order.resource):
                errors.append(f"resource_order {index}: unknown resource {order.resource!r}")
                continue
            if order.action not in RESOURCE_ACTIONS:
                errors.append(f"resource_order {index}: unknown action {order.action!r}")
            if order.amount < 0:
                errors.append(f"resource_order {index}: amount must not be negative")
            if has_build_order and order.resource == "wood" and order.action == "spend":
                errors.append(
                    f"resource_order {index}: build orders spend wood automatically; do not add a separate wood spend"
                )
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
                if order.amount <= 0:
                    errors.append(f"{label}: amount must be positive for settlement")
                if not tile.is_passable():
                    errors.append(f"{label}: target terrain is not passable")
                if order.action == "claim" and tile.owner is not None:
                    errors.append(f"{label}: claim target must be unowned")
                if order.action == "settle" and tile.owner not in {None, faction_id}:
                    errors.append(f"{label}: target is owned by {tile.owner}")
                if order.profession is not None and order.profession not in CIVILIAN_PROFESSION_TYPES:
                    errors.append(f"{label}: unknown civilian profession {order.profession!r}")
                if order.origin is not None:
                    errors.extend(
                        self._validate_visible_target(
                            world,
                            faction_id,
                            order.origin,
                            f"{label} origin",
                        )
                    )
                    if world.in_bounds(*order.origin):
                        origin_tile = world.tile_at(*order.origin)
                        if origin_tile.owner != faction_id:
                            errors.append(f"{label}: origin must be owned by {faction_id}")
                        if not self._adjacent(order.origin, order.target):
                            errors.append(f"{label}: origin must be adjacent to target")
                elif not self._adjacent_to_owned(world, faction_id, order.target):
                    errors.append(f"{label}: target must border owned territory")
                if not _has_movable_population(world, faction_id, order.amount):
                    errors.append(f"{label}: faction has no movable civilian for settlement")
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
            if order.action in {"attack", "raid", "move"}:
                if order.origin is None or order.target is None:
                    errors.append(f"{label}: {order.action} requires origin and target")
                    continue
                if not world.in_bounds(*order.origin) or not world.in_bounds(*order.target):
                    continue
                if not self._adjacent(order.origin, order.target):
                    errors.append(f"{label}: target must be adjacent to origin")
                if order.action == "move":
                    origin_tile = world.tile_at(*order.origin)
                    target_tile = world.tile_at(*order.target)
                    if origin_tile.owner != faction_id:
                        errors.append(f"{label}: origin must be owned by {faction_id}")
                    if target_tile.owner != faction_id:
                        errors.append(f"{label}: move target must be owned by {faction_id}")
                    continue
                target_tile = world.tile_at(*order.target)
                if target_tile.owner in {None, faction_id}:
                    errors.append(f"{label}: target must be enemy-owned")
                elif target_tile.owner not in world.factions[faction_id].known_factions:
                    errors.append(f"{label}: target faction has not been discovered")
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
                continue
            if order.target_faction == faction_id:
                errors.append(f"diplomacy_order {index}: cannot target self")
            if order.target_faction not in world.factions[faction_id].known_factions:
                errors.append(f"diplomacy_order {index}: target faction has not been discovered")
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

    def _validate_plan_matches_actions(self, decision: LeaderDecision) -> list[str]:
        text = " ".join(
            (
                decision.turn_intent,
                decision.strategy_summary,
                decision.public_decree,
                " ".join(order.message for order in decision.diplomacy_orders),
            )
        ).lower()
        errors: list[str] = []
        military_actions = {order.action for order in decision.military_orders}
        territory_actions = {order.action for order in decision.territory_orders}
        population_tasks = {order.task for order in decision.population_orders}
        petition_types = {petition.kind for petition in decision.petitions}
        diplomacy = {order.proposal for order in decision.diplomacy_orders}

        if _contains_action_mention(text, ("war", "attack", "raid", "开战", "战争", "进攻", "攻打", "突袭", "攻占")):
            if not (military_actions & {"attack", "raid"} or "war" in diplomacy):
                errors.append(
                    "plan mentions war/attack/raid. If this is a military action, submit attack/raid or war diplomacy; if this is peaceful expansion, use claim/settle wording and avoid war words."
                )
        if _mentions_military_capture(text):
            if "attack" not in military_actions:
                errors.append(
                    "plan mentions capturing enemy territory but has no attack military order"
                )
        if _contains_action_mention(text, ("build house", "houses", "住房", "房屋", "建房")):
            if "build" not in population_tasks:
                errors.append("plan mentions building houses but has no build population order")
        if _contains_action_mention(text, ("farm", "farmland", "耕", "农田", "种田")):
            if "farm" not in population_tasks:
                errors.append("plan mentions farming but has no farm population order")
        if _contains_action_mention(
            text,
            (
                "weather petition",
                "set weather",
                "make rain",
                "summon rain",
                "请求天气",
                "祈求天气",
                "改变天气",
                "天气神迹",
                "请求降雨",
                "祈求降雨",
                "赐予降雨",
                "制造风暴",
                "驱散风暴",
                "降雨",
                "求雨",
            ),
        ):
            if "weather" not in petition_types:
                errors.append("plan mentions weather but has no weather petition")
        if _contains_action_mention(text, ("expand", "settle", "claim", "扩张", "开拓", "定居", "纳入疆域", "占据空地", "占领新土地", "占领空地")):
            if not (territory_actions & {"claim", "settle"}):
                errors.append("plan mentions expansion or settlement but has no claim/settle territory order")
        return errors

    def _validate_idle_budget(
        self,
        world: WorldState,
        faction_id: str,
        decision: LeaderDecision,
    ) -> list[str]:
        jobs_by_tile = {
            (tile.x, tile.y): {
                profession: tile.professions_of(faction_id).get(profession, 0)
                for profession in CIVILIAN_PROFESSION_PRIORITY
            }
            for tile in world.faction_tiles(faction_id)
        }
        population_by_tile = {
            (tile.x, tile.y): tile.population_of(faction_id)
            for tile in world.faction_tiles(faction_id)
        }
        soldiers_by_tile = {
            (tile.x, tile.y): tile.soldiers_of(faction_id)
            for tile in world.faction_tiles(faction_id)
        }
        owned = set(jobs_by_tile)
        total_civilians = sum(sum(jobs.values()) for jobs in jobs_by_tile.values())
        budget = _idle_budget_need(world, decision)
        errors: list[str] = []

        for index, order in sorted(
            enumerate(decision.population_orders, start=1),
            key=lambda item: item[1].priority,
        ):
            if order.workers <= 0:
                continue
            label = f"population_order {index}"
            if order.task in PROFESSION_TASKS:
                tile = _order_owned_tile(world, faction_id, order.target)
                if tile is None:
                    continue
                _consume_idle_budget(
                    jobs_by_tile,
                    (tile.x, tile.y),
                    order.workers,
                    label,
                    errors,
                )
            elif order.task in {"train", "defend"} and order.target is not None:
                if not world.in_bounds(*order.target):
                    continue
                tile = world.tile_at(*order.target)
                if tile.owner != faction_id:
                    continue
                _consume_idle_budget(
                    jobs_by_tile,
                    (tile.x, tile.y),
                    _trained_count(order.workers),
                    label,
                    errors,
                )
                _consume_population_budget(
                    population_by_tile,
                    owned,
                    (tile.x, tile.y),
                    _trained_count(order.workers),
                    label,
                    errors,
                )
            elif order.task == "settle" and order.target is not None:
                _consume_settlement_budget(
                    world,
                    order.target,
                    label,
                    action="settle",
                    origin=None,
                    profession=None,
                    amount=SETTLEMENT_IDLE_COST,
                    jobs_by_tile=jobs_by_tile,
                    population_by_tile=population_by_tile,
                    soldiers_by_tile=soldiers_by_tile,
                    owned=owned,
                    errors=errors,
                )

        for index, order in sorted(
            enumerate(decision.territory_orders, start=1),
            key=lambda item: item[1].priority,
        ):
            if order.action not in {"claim", "settle"}:
                continue
            _consume_settlement_budget(
                world,
                order.target,
                f"territory_order {index}",
                action=order.action,
                origin=order.origin if order.action == "settle" else None,
                profession=order.profession if order.action == "settle" else None,
                amount=order.amount,
                jobs_by_tile=jobs_by_tile,
                population_by_tile=population_by_tile,
                soldiers_by_tile=soldiers_by_tile,
                owned=owned,
                errors=errors,
            )

        if errors and budget["total"] > total_civilians:
            errors.append(
                (
                    f"civilian budget exceeded: current civilians={total_civilians}, "
                    f"claim/settle migration need={budget['settlement']} "
                    f"({budget['settlement_count']} orders), "
                    f"jobs/training need={budget['jobs_training']}, "
                    f"total needed={budget['total']}"
                )
            )
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


def _has_movable_population(world: WorldState, faction_id: str, amount: int) -> bool:
    return any(
        any(
            tile.professions_of(faction_id).get(profession, 0) >= amount
            for profession in CIVILIAN_PROFESSION_PRIORITY
        )
        and (
            tile.population_of(faction_id) > amount
            or tile.soldiers_of(faction_id) > 0
        )
        for tile in world.faction_tiles(faction_id)
    )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _contains_action_mention(text: str, needles: tuple[str, ...]) -> bool:
    for needle in needles:
        start = 0
        while True:
            index = text.find(needle, start)
            if index < 0:
                break
            context = text[max(0, index - 10): index + len(needle) + 10]
            if not _contains_any(
                context,
                (
                    "不",
                    "无",
                    "没有",
                    "无需",
                    "暂不",
                    "避免",
                    "防止",
                    "防备",
                    "警惕",
                    "准备",
                    "未来",
                    "考虑",
                    "not ",
                    "no ",
                    "avoid",
                    "without",
                    "prepare",
                    "future",
                    "consider",
                ),
            ):
                return True
            start = index + len(needle)
    return False


def _mentions_military_capture(text: str) -> bool:
    return _contains_any(
        text,
        (
            "capture enemy",
            "capture hostile",
            "capture rival",
            "enemy territory",
            "hostile territory",
            "军事占领",
            "攻占",
            "占领敌",
            "占领对方",
            "占领其领",
            "夺取敌",
            "夺取对方",
            "夺取其领",
        ),
    )


def _idle_budget_need(
    world: WorldState,
    decision: LeaderDecision,
) -> dict[str, int]:
    jobs_training = 0
    settlement_count = 0
    settlement = 0
    for order in decision.population_orders:
        if order.workers <= 0:
            continue
        if order.task in PROFESSION_TASKS:
            jobs_training += order.workers
        elif order.task in {"train", "defend"}:
            jobs_training += _trained_count(order.workers)
        elif order.task == "settle" and order.target is not None:
            if _counts_as_new_territory(world, order.target):
                settlement_count += 1
                settlement += SETTLEMENT_IDLE_COST
    for order in decision.territory_orders:
        if order.action in {"claim", "settle"}:
            settlement_count += 1
            settlement += max(order.amount, 0)
    return {
        "jobs_training": jobs_training,
        "settlement_count": settlement_count,
        "settlement": settlement,
        "total": jobs_training + settlement,
    }


def _counts_as_new_territory(
    world: WorldState,
    target: tuple[int, int],
) -> bool:
    if not world.in_bounds(*target):
        return False
    tile = world.tile_at(*target)
    return tile.owner is None and tile.is_passable()


def _largest_population_tile(world: WorldState, faction_id: str):
    owned = world.faction_tiles(faction_id)
    if not owned:
        return None
    return max(owned, key=lambda tile: tile.population_of(faction_id))


def _order_owned_tile(
    world: WorldState,
    faction_id: str,
    target: tuple[int, int] | None,
):
    if target is None:
        return _largest_population_tile(world, faction_id)
    if not world.in_bounds(*target):
        return None
    tile = world.tile_at(*target)
    if tile.owner != faction_id:
        return None
    return tile


def _consume_idle_budget(
    jobs_by_tile: dict[tuple[int, int], dict[str, int]],
    key: tuple[int, int],
    amount: int,
    label: str,
    errors: list[str],
) -> None:
    if amount <= 0:
        return
    jobs = jobs_by_tile.setdefault(key, {job: 0 for job in CIVILIAN_PROFESSION_PRIORITY})
    jobs["idle"] = jobs.get("idle", 0) - amount
    if jobs["idle"] < 0:
        errors.append(f"{label}: idle population budget is overcommitted")


def _consume_population_budget(
    population_by_tile: dict[tuple[int, int], int],
    owned: set[tuple[int, int]],
    key: tuple[int, int],
    amount: int,
    label: str,
    errors: list[str],
) -> None:
    if amount <= 0 or key not in owned:
        return
    population_by_tile[key] = population_by_tile.get(key, 0) - amount
    if population_by_tile[key] <= 0:
        errors.append(f"{label}: would leave source tile without population")


def _consume_settlement_budget(
    world: WorldState,
    target: tuple[int, int],
    label: str,
    *,
    action: str,
    origin: tuple[int, int] | None,
    profession: str | None,
    amount: int,
    jobs_by_tile: dict[tuple[int, int], dict[str, int]],
    population_by_tile: dict[tuple[int, int], int],
    soldiers_by_tile: dict[tuple[int, int], int],
    owned: set[tuple[int, int]],
    errors: list[str],
) -> None:
    if not world.in_bounds(*target):
        return
    if amount <= 0:
        return
    tile = world.tile_at(*target)
    if action == "claim" and tile.owner is not None:
        return
    if not tile.is_passable():
        return
    if tile.owner is not None and target not in owned:
        return
    if population_by_tile.get(target, 0) + amount > tile.capacity():
        errors.append(f"{label}: target has no population capacity for settlement")
        return
    donor_key = _best_adjacent_civilian_donor(
        target,
        jobs_by_tile,
        population_by_tile,
        soldiers_by_tile,
        owned,
        origin=origin,
        profession=profession,
        amount=amount,
    )
    if donor_key is None:
        errors.append(
            f"{label}: faction has no movable civilian to settle target while leaving the source tile held"
        )
        return
    selected = _choose_budget_profession(jobs_by_tile, donor_key, profession, amount)
    if selected is None:
        errors.append(
            f"{label}: faction has no movable civilian to settle target while leaving the source tile held"
        )
        return
    jobs_by_tile[donor_key][selected] = jobs_by_tile[donor_key].get(selected, 0) - amount
    population_by_tile[donor_key] -= amount
    if (
        population_by_tile.get(donor_key, 0) <= 0
        and soldiers_by_tile.get(donor_key, 0) <= 0
    ):
        errors.append(f"{label}: would leave source tile without civilians or soldiers")
        return
    target_jobs = jobs_by_tile.setdefault(
        target,
        {job: 0 for job in CIVILIAN_PROFESSION_PRIORITY},
    )
    target_jobs[selected] = target_jobs.get(selected, 0) + amount
    population_by_tile[target] = population_by_tile.get(target, 0) + amount
    owned.add(target)


def _best_adjacent_civilian_donor(
    target: tuple[int, int],
    jobs_by_tile: dict[tuple[int, int], dict[str, int]],
    population_by_tile: dict[tuple[int, int], int],
    soldiers_by_tile: dict[tuple[int, int], int],
    owned: set[tuple[int, int]],
    *,
    origin: tuple[int, int] | None,
    profession: str | None,
    amount: int,
) -> tuple[int, int] | None:
    if origin is not None:
        keys = [origin] if origin in owned else []
    else:
        keys = list(owned)
    candidates = []
    for key in keys:
        if abs(key[0] - target[0]) + abs(key[1] - target[1]) != 1:
            continue
        if _choose_budget_profession(jobs_by_tile, key, profession, amount) is None:
            continue
        if (
            population_by_tile.get(key, 0) - amount <= 0
            and soldiers_by_tile.get(key, 0) <= 0
        ):
            continue
        candidates.append(key)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda key: (
            jobs_by_tile.get(key, {}).get(
                _choose_budget_profession(jobs_by_tile, key, profession, amount) or "idle",
                0,
            ),
            population_by_tile.get(key, 0),
        ),
    )


def _choose_budget_profession(
    jobs_by_tile: dict[tuple[int, int], dict[str, int]],
    key: tuple[int, int],
    profession: str | None,
    amount: int,
) -> str | None:
    jobs = jobs_by_tile.get(key, {})
    if profession:
        if profession in CIVILIAN_PROFESSION_TYPES and jobs.get(profession, 0) >= amount:
            return profession
        return None
    for candidate in CIVILIAN_PROFESSION_PRIORITY:
        if jobs.get(candidate, 0) >= amount:
            return candidate
    return None


def _trained_count(workers: int) -> int:
    if workers <= 0:
        return 0
    return max(1, workers // 5)
