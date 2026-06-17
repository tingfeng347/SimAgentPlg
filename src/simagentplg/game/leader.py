from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from simagentplg import BaseAgent, MethodToolHandler, ModelConfig, StepOutcome
from simagentplg.handlers.base import ToolSchema
from simagentplg.game.world import DEFAULT_FACTIONS, RESOURCE_TYPES, WorldState

POPULATION_TASKS = (
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
)
RESOURCE_ACTIONS = ("reserve", "spend", "trade", "tribute")
TERRITORY_ACTIONS = ("claim", "settle", "fortify", "abandon", "scout")
MILITARY_ACTIONS = ("muster", "defend", "attack", "raid", "retreat")
DIPLOMACY_PROPOSALS = (
    "alliance",
    "trade",
    "non_aggression",
    "tribute",
    "peace",
    "war",
)
PETITION_TYPES = ("resources", "weather", "protection", "territory", "miracle", "peace")
URGENCY_LEVELS = ("low", "medium", "high")

LEADER_SYSTEM_PROMPT = """
You are the LLM leader of one civilization in an original god-sandbox
simulation game. You do not directly modify the world. Inspect only the
information available to your faction, then end your strategic turn by calling
submit_leader_turn exactly once. Keep strategy_summary concise and never reveal
private chain-of-thought.

Use only the exact enum values exposed by the submit_leader_turn tool schema.
If no legal action is obvious, submit a conservative no-op plan with empty
order arrays. Never invent action names, resource names, faction IDs, or
coordinates.
""".strip()


@dataclass(frozen=True, slots=True)
class PopulationOrder:
    task: str
    target: tuple[int, int] | None = None
    workers: int = 0
    priority: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "target": _target_to_dict(self.target),
            "workers": self.workers,
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class ResourceOrder:
    resource: str
    action: str
    amount: int
    purpose: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "action": self.action,
            "amount": self.amount,
            "purpose": self.purpose,
        }


@dataclass(frozen=True, slots=True)
class TerritoryOrder:
    action: str
    target: tuple[int, int]
    priority: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": _target_to_dict(self.target),
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class MilitaryOrder:
    action: str
    origin: tuple[int, int] | None = None
    target: tuple[int, int] | None = None
    force_ratio: float = 0.5
    priority: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "origin": _target_to_dict(self.origin),
            "target": _target_to_dict(self.target),
            "force_ratio": self.force_ratio,
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class DiplomacyOrder:
    target_faction: str
    proposal: str
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_faction": self.target_faction,
            "proposal": self.proposal,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PetitionRequest:
    kind: str
    request: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    urgency: str = "medium"

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "request": dict(self.request),
            "reason": self.reason,
            "urgency": self.urgency,
        }


@dataclass(frozen=True, slots=True)
class LeaderDecision:
    turn_intent: str
    population_orders: tuple[PopulationOrder, ...] = ()
    resource_orders: tuple[ResourceOrder, ...] = ()
    territory_orders: tuple[TerritoryOrder, ...] = ()
    military_orders: tuple[MilitaryOrder, ...] = ()
    diplomacy_orders: tuple[DiplomacyOrder, ...] = ()
    petitions: tuple[PetitionRequest, ...] = ()
    public_decree: str = ""
    strategy_summary: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "LeaderDecision":
        return cls(
            turn_intent=str(payload.get("turn_intent", "")).strip(),
            population_orders=tuple(
                _parse_population_order(item)
                for item in _list(payload.get("population_orders"))
            ),
            resource_orders=tuple(
                _parse_resource_order(item)
                for item in _list(payload.get("resource_orders"))
            ),
            territory_orders=tuple(
                _parse_territory_order(item)
                for item in _list(payload.get("territory_orders"))
            ),
            military_orders=tuple(
                _parse_military_order(item)
                for item in _list(payload.get("military_orders"))
            ),
            diplomacy_orders=tuple(
                _parse_diplomacy_order(item)
                for item in _list(payload.get("diplomacy_orders"))
            ),
            petitions=tuple(
                _parse_petition(item)
                for item in _list(payload.get("petitions"))
            ),
            public_decree=str(payload.get("public_decree", "")).strip(),
            strategy_summary=str(payload.get("strategy_summary", "")).strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_intent": self.turn_intent,
            "population_orders": [
                order.as_dict() for order in self.population_orders
            ],
            "resource_orders": [
                order.as_dict() for order in self.resource_orders
            ],
            "territory_orders": [
                order.as_dict() for order in self.territory_orders
            ],
            "military_orders": [
                order.as_dict() for order in self.military_orders
            ],
            "diplomacy_orders": [
                order.as_dict() for order in self.diplomacy_orders
            ],
            "petitions": [
                petition.as_dict() for petition in self.petitions
            ],
            "public_decree": self.public_decree,
            "strategy_summary": self.strategy_summary,
        }


INSPECT_REALM_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "inspect_realm",
        "description": "Inspect your faction's resources, people, diplomacy, and recent events.",
        "parameters": {"type": "object", "properties": {}},
    },
}

INSPECT_TILES_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "inspect_tiles",
        "description": "Inspect visible tiles by coordinates or by a center point and radius.",
        "parameters": {
            "type": "object",
            "properties": {
                "tiles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                        },
                        "required": ["x", "y"],
                    },
                },
                "center": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                },
                "radius": {"type": "integer", "minimum": 0},
            },
        },
    },
}

INSPECT_FACTION_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "inspect_faction",
        "description": "Inspect known information about another faction.",
        "parameters": {
            "type": "object",
            "properties": {
                "faction_id": {"type": "string"},
            },
            "required": ["faction_id"],
        },
    },
}


def _target_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["x", "y"],
    }


SUBMIT_LEADER_TURN_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "submit_leader_turn",
        "description": "Submit the final structured plan for this leader turn.",
        "parameters": {
            "type": "object",
            "properties": {
                "turn_intent": {"type": "string"},
                "population_orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string", "enum": list(POPULATION_TASKS)},
                            "target": _target_schema(),
                            "workers": {"type": "integer", "minimum": 0},
                            "priority": {"type": "integer", "minimum": 1},
                        },
                        "required": ["task", "workers"],
                    },
                },
                "resource_orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "resource": {"type": "string", "enum": list(RESOURCE_TYPES)},
                            "action": {"type": "string", "enum": list(RESOURCE_ACTIONS)},
                            "amount": {"type": "integer", "minimum": 0},
                            "purpose": {"type": "string"},
                        },
                        "required": ["resource", "action", "amount"],
                    },
                },
                "territory_orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": list(TERRITORY_ACTIONS)},
                            "target": _target_schema(),
                            "priority": {"type": "integer", "minimum": 1},
                        },
                        "required": ["action", "target"],
                    },
                },
                "military_orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": list(MILITARY_ACTIONS)},
                            "origin": _target_schema(),
                            "target": _target_schema(),
                            "force_ratio": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 1,
                            },
                            "priority": {"type": "integer", "minimum": 1},
                        },
                        "required": ["action"],
                    },
                },
                "diplomacy_orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_faction": {
                                "type": "string",
                                "enum": list(DEFAULT_FACTIONS),
                            },
                            "proposal": {
                                "type": "string",
                                "enum": list(DIPLOMACY_PROPOSALS),
                            },
                            "message": {"type": "string"},
                        },
                        "required": ["target_faction", "proposal"],
                    },
                },
                "petitions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": list(PETITION_TYPES)},
                            "request": {"type": "object"},
                            "reason": {"type": "string"},
                            "urgency": {"type": "string", "enum": list(URGENCY_LEVELS)},
                        },
                        "required": ["type", "reason"],
                    },
                },
                "public_decree": {"type": "string"},
                "strategy_summary": {"type": "string"},
            },
            "required": ["turn_intent"],
        },
    },
}


class LeaderToolHandler(MethodToolHandler):
    """Read-only intelligence tools and the terminal leader-turn submit tool."""

    def __init__(
        self,
        *,
        faction_id: str,
        world_provider: Callable[[], WorldState],
    ) -> None:
        super().__init__(
            (
                INSPECT_REALM_TOOL,
                INSPECT_TILES_TOOL,
                INSPECT_FACTION_TOOL,
                SUBMIT_LEADER_TURN_TOOL,
            )
        )
        self.faction_id = faction_id
        self._world_provider = world_provider

    async def do_inspect_realm(self, arguments: Mapping[str, Any]) -> StepOutcome:
        world = self._world_provider()
        faction = world.factions[self.faction_id]
        owned = world.faction_tiles(self.faction_id)
        recent_events = [
            event.as_dict()
            for event in world.events[-8:]
            if event.faction_id in {None, self.faction_id}
        ]
        return StepOutcome(
            {
                "faction_id": self.faction_id,
                "name": faction.name,
                "leader_name": faction.leader_name,
                "tick": world.tick,
                "resources": faction.resources.as_dict(),
                "population": world.total_population(self.faction_id),
                "soldiers": world.total_soldiers(self.faction_id),
                "territory_count": len(owned),
                "diplomacy": dict(faction.diplomacy),
                "recent_events": recent_events,
            }
        )

    async def do_inspect_tiles(self, arguments: Mapping[str, Any]) -> StepOutcome:
        world = self._world_provider()
        coordinates = _requested_coordinates(arguments)
        tiles = []
        for x, y in coordinates:
            if not world.in_bounds(x, y):
                tiles.append({"x": x, "y": y, "visible": False, "error": "out of bounds"})
                continue
            if not world.is_visible(self.faction_id, x, y):
                tiles.append({"x": x, "y": y, "visible": False})
                continue
            tiles.append(_summarize_tile(world, x, y))
        return StepOutcome({"tiles": tiles})

    async def do_inspect_faction(self, arguments: Mapping[str, Any]) -> StepOutcome:
        world = self._world_provider()
        other_id = str(arguments.get("faction_id", "")).strip()
        if other_id not in world.factions:
            return StepOutcome({"status": "error", "error": "unknown faction"})
        other = world.factions[other_id]
        visible_owned = [
            tile
            for tile in world.faction_tiles(other_id)
            if world.is_visible(self.faction_id, tile.x, tile.y)
        ]
        return StepOutcome(
            {
                "faction_id": other_id,
                "name": other.name,
                "relation": world.factions[self.faction_id].relation_to(other_id),
                "visible_territory_count": len(visible_owned),
                "visible_population": sum(tile.population_of(other_id) for tile in visible_owned),
                "visible_soldiers": sum(tile.soldiers_of(other_id) for tile in visible_owned),
                "recent_events": [
                    event.as_dict()
                    for event in world.events[-8:]
                    if event.faction_id == other_id
                ],
            }
        )

    async def do_submit_leader_turn(self, arguments: Mapping[str, Any]) -> StepOutcome:
        decision = LeaderDecision.from_mapping(dict(arguments))
        if not decision.turn_intent:
            return StepOutcome(
                {"status": "error", "error": "turn_intent is required"}
            )
        return StepOutcome(
            {"status": "submitted", "decision": decision.as_dict()},
            should_exit=True,
        )


class LLMLeaderController:
    """Drive one faction leader through a game-only BaseAgent tool surface."""

    def __init__(self, *, faction_id: str, agent: BaseAgent) -> None:
        self.faction_id = faction_id
        self.agent = agent

    @classmethod
    def create(
        cls,
        *,
        config: ModelConfig,
        faction_id: str,
        world_provider: Callable[[], WorldState],
        max_steps: int = 8,
    ) -> "LLMLeaderController":
        handler = LeaderToolHandler(
            faction_id=faction_id,
            world_provider=world_provider,
        )
        agent = BaseAgent(
            config=config,
            agent_id=f"leader-{faction_id}",
            system_prompt=LEADER_SYSTEM_PROMPT,
            handlers=[handler],
            enable_tools=True,
            max_steps=max_steps,
        )
        return cls(faction_id=faction_id, agent=agent)

    async def decide(
        self,
        world: WorldState,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision:
        result = await self.agent.runtime(
            task=_build_leader_task(world, self.faction_id, feedback)
        )
        payload = json.loads(result or "{}")
        decision_payload = payload.get("decision", payload)
        if not isinstance(decision_payload, dict):
            raise RuntimeError("leader did not submit a decision object")
        return LeaderDecision.from_mapping(decision_payload)


def _build_leader_task(
    world: WorldState,
    faction_id: str,
    feedback: str | None,
) -> str:
    faction = world.factions[faction_id]
    owned_tiles = [
        {
            "x": tile.x,
            "y": tile.y,
            "terrain": tile.terrain,
            "population": tile.population_of(faction_id),
            "soldiers": tile.soldiers_of(faction_id),
        }
        for tile in world.faction_tiles(faction_id)
    ]
    expansion_candidates = _expansion_candidates(world, faction_id)
    lines = [
        f"You lead faction {faction.name} ({faction_id}) at world tick {world.tick}.",
        "Use inspect tools if needed, then call submit_leader_turn.",
        "Legal values:",
        f"- population task: {', '.join(POPULATION_TASKS)}",
        f"- resource action: {', '.join(RESOURCE_ACTIONS)}",
        f"- territory action: {', '.join(TERRITORY_ACTIONS)}",
        f"- military action: {', '.join(MILITARY_ACTIONS)}",
        f"- diplomacy proposal: {', '.join(DIPLOMACY_PROPOSALS)}",
        f"- petition type: {', '.join(PETITION_TYPES)}",
        "Use only visible coordinates. For a safe turn, submit empty order arrays.",
        f"Resources: {faction.resources.as_dict()}",
        f"Population: {world.total_population(faction_id)}",
        f"Soldiers: {world.total_soldiers(faction_id)}",
        f"Territory tiles: {len(world.faction_tiles(faction_id))}",
        f"Owned tiles: {owned_tiles}",
        f"Legal expansion candidates: {expansion_candidates[:8]}",
        f"Diplomacy: {faction.diplomacy}",
        "Safe no-op submit example: turn_intent='consolidate', all order arrays empty, strategy_summary='Hold position.'",
    ]
    if feedback:
        lines.extend(
            [
                "",
                "Your previous submitted plan was rejected by the rules engine.",
                f"Fix these rule errors and resubmit: {feedback}",
            ]
        )
    return "\n".join(lines)


def _summarize_tile(world: WorldState, x: int, y: int) -> dict[str, Any]:
    tile = world.tile_at(x, y)
    return {
        "x": tile.x,
        "y": tile.y,
        "visible": True,
        "terrain": tile.terrain,
        "weather": tile.weather,
        "owner": tile.owner,
        "population": dict(tile.population),
        "soldiers": dict(tile.soldiers),
        "protected": tile.protected,
    }


def _expansion_candidates(
    world: WorldState,
    faction_id: str,
) -> list[dict[str, Any]]:
    candidates: dict[tuple[int, int], dict[str, Any]] = {}
    for tile in world.faction_tiles(faction_id):
        for neighbor in world.neighbors(tile.x, tile.y):
            if neighbor.owner is not None or not neighbor.is_passable():
                continue
            if not world.is_visible(faction_id, neighbor.x, neighbor.y):
                continue
            candidates[(neighbor.x, neighbor.y)] = {
                "x": neighbor.x,
                "y": neighbor.y,
                "terrain": neighbor.terrain,
                "weather": neighbor.weather,
            }
    return [candidates[key] for key in sorted(candidates)]


def _requested_coordinates(arguments: Mapping[str, Any]) -> list[tuple[int, int]]:
    if isinstance(arguments.get("tiles"), list):
        return [
            _parse_target(item)
            for item in arguments["tiles"]
            if isinstance(item, Mapping)
        ]
    center = arguments.get("center")
    if isinstance(center, Mapping):
        cx, cy = _parse_target(center)
        radius = arguments.get("radius", 1)
        if not isinstance(radius, int) or isinstance(radius, bool) or radius < 0:
            radius = 1
        return [
            (x, y)
            for y in range(cy - radius, cy + radius + 1)
            for x in range(cx - radius, cx + radius + 1)
        ]
    return []


def _parse_population_order(payload: Any) -> PopulationOrder:
    item = _mapping(payload)
    return PopulationOrder(
        task=str(item.get("task", "idle")).strip(),
        target=_optional_target(item.get("target")),
        workers=_int(item.get("workers"), 0),
        priority=_int(item.get("priority"), 1),
    )


def _parse_resource_order(payload: Any) -> ResourceOrder:
    item = _mapping(payload)
    return ResourceOrder(
        resource=str(item.get("resource", "")).strip(),
        action=str(item.get("action", "")).strip(),
        amount=_int(item.get("amount"), 0),
        purpose=str(item.get("purpose", "")).strip(),
    )


def _parse_territory_order(payload: Any) -> TerritoryOrder:
    item = _mapping(payload)
    return TerritoryOrder(
        action=str(item.get("action", "")).strip(),
        target=_parse_target(item.get("target", {})),
        priority=_int(item.get("priority"), 1),
    )


def _parse_military_order(payload: Any) -> MilitaryOrder:
    item = _mapping(payload)
    return MilitaryOrder(
        action=str(item.get("action", "")).strip(),
        origin=_optional_target(item.get("origin")),
        target=_optional_target(item.get("target")),
        force_ratio=_float(item.get("force_ratio"), 0.5),
        priority=_int(item.get("priority"), 1),
    )


def _parse_diplomacy_order(payload: Any) -> DiplomacyOrder:
    item = _mapping(payload)
    return DiplomacyOrder(
        target_faction=str(item.get("target_faction", "")).strip(),
        proposal=str(item.get("proposal", "")).strip(),
        message=str(item.get("message", "")).strip(),
    )


def _parse_petition(payload: Any) -> PetitionRequest:
    item = _mapping(payload)
    return PetitionRequest(
        kind=str(item.get("type", item.get("kind", ""))).strip(),
        request=dict(item.get("request", {})) if isinstance(item.get("request"), Mapping) else {},
        reason=str(item.get("reason", "")).strip(),
        urgency=str(item.get("urgency", "medium")).strip(),
    )


def _parse_target(payload: Any) -> tuple[int, int]:
    item = _mapping(payload)
    return (_int(item.get("x"), 0), _int(item.get("y"), 0))


def _optional_target(payload: Any) -> tuple[int, int] | None:
    if payload is None:
        return None
    return _parse_target(payload)


def _target_to_dict(target: tuple[int, int] | None) -> dict[str, int] | None:
    if target is None:
        return None
    x, y = target
    return {"x": x, "y": y}


def _mapping(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    return {}


def _list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    return []


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_resource_name(resource: str) -> bool:
    return resource in RESOURCE_TYPES
