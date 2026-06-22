from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from simagentplg import BaseAgent, MethodToolHandler, ModelConfig, StepOutcome
from simagentplg.handlers.base import ToolSchema
from simagentplg.game.world import (
    DEFAULT_FACTIONS,
    RESOURCE_TYPES,
    SETTLEMENT_IDLE_COST,
    WorldState,
)

POPULATION_TASKS = (
    "farm",
    "gather_wood",
    "mine_stone",
    "build",
    "settle",
    "train",
    "defend",
    "attack",
)
RESOURCE_ACTIONS = ("reserve", "spend", "trade", "tribute")
TERRITORY_ACTIONS = ("claim", "settle", "fortify", "abandon")
MILITARY_ACTIONS = ("muster", "defend", "attack", "raid", "retreat", "move")
DIPLOMACY_PROPOSALS = (
    "alliance",
    "trade",
    "non_aggression",
    "tribute",
    "peace",
    "war",
)
PETITION_TYPES = ("resources", "weather", "protection", "territory")
URGENCY_LEVELS = ("low", "medium", "high")
CIVILIAN_PROFESSION_ACTIONS = ("idle", "farmer", "lumberjack", "miner", "builder")

LEADER_SYSTEM_PROMPT = """
You are the LLM leader of one civilization in an original god-sandbox
simulation game. You do not directly modify the world. Inspect only the
information available to your faction, then end your strategic turn by calling
submit_leader_turn exactly once. Keep strategy_summary concise and never reveal
private chain-of-thought.

Use only the exact enum values exposed by the submit_leader_turn tool schema.
Enum fields, resource names, faction IDs, action names, and coordinates must
stay in the exact English values required by the schema. All human-facing
narrative text must be written in Simplified Chinese, including turn_intent,
strategy_summary, public_decree, diplomacy message, petition reason, and
resource purpose. Never write petitions, decrees, or strategy plans in English.

Tool usage:
- inspect is your only observation function. It never changes the world. Use it
  when the task summary is not enough or when you need to verify a coordinate,
  local weather, idle workers, houses, known factions, or visible enemy border.
- Call inspect with {"mode": "realm"} to refresh your civilization summary:
  resources, population, soldiers, jobs, houses, capacity, known factions,
  diplomacy, last plan snapshot, and recent relevant events.
- Call inspect with {"mode": "tiles", "tiles": [{"x": 1, "y": 2}]} to inspect
  specific visible coordinates. You may also call inspect with {"mode": "tiles",
  "center": {"x": 1, "y": 2}, "radius": 2} to inspect a small visible area.
  Tile results include terrain, weather, owner, home marker, population,
  soldiers, professions, houses, capacity, and protection if the tile is
  visible.
- Call inspect with {"mode": "faction", "faction_id": "elf"} only for a
  discovered faction. It returns known relation, visible territory, visible
  population, visible soldiers, visible jobs, visible houses, and recent events.
  Unknown factions should not be used for diplomacy, war, or long-term plans.
- submit_leader_turn is the only function that ends your strategic turn. Call it
  exactly once after you have enough information. Do not call it before your
  final plan is internally consistent.
- submit_leader_turn must contain a clear Chinese turn_intent and may contain
  population_orders, resource_orders, territory_orders, military_orders,
  diplomacy_orders, petitions, public_decree, and strategy_summary. Leave an
  order list empty if you do not need that category.
- submit_leader_turn example:
  {"turn_intent":"增加粮食、修建房屋并扩张边境","population_orders":[{"task":"farm",
  "target":{"x":3,"y":4},"workers":2},{"task":"build",
  "target":{"x":3,"y":4},"workers":2}],"territory_orders":[{"action":"claim",
  "target":{"x":4,"y":4}}],"military_orders":[],"diplomacy_orders":[],
  "petitions":[],"public_decree":"今年优先开垦边境农田。",
  "strategy_summary":"用2名闲置人口耕作、2名建房，并用1名闲置人口迁入相邻空地。"}

Basic world:
- You compete for land, food, safety, and long-term survival. Peace is a
  strategy, not the default ending.
- Your final objective is to gain more territory, suppress rival civilizations,
  and eventually defeat the other races. Alliances, trade, and peace are tools
  for expansion, survival, and future victory, not the final goal.
- The world is a tile map. Terrain, weather, population, soldiers, houses,
  resources, and ownership affect what your civilization can do.
- Farmers increase food. Plains are naturally better for food. Rain usually
  helps farming, while drought and storms make survival and work harder.
- Lumberjacks increase wood. Forests are naturally better for wood. Wood is
  mainly used to build houses.
- Miners increase stone. Hills are naturally better for stone. Stone is stored
  for later civilization systems.
- Builders use wood to build houses. Houses raise population capacity but do
  not create people instantly.
- Food supports survival and future growth. Population growth creates idle
  people, not specialized workers.
- Soldiers are the only people who fight. Raids can take resources; attacks
  directly occupy enemy land if the battle is won; surviving attackers move
  into the captured tile as soldiers.
- When food, housing, and basic resource needs are stable, shift surplus idle
  people toward training, border reinforcement, deterrence, raids, and conquest
  preparation.
- Soldiers can move only between adjacent owned tiles with the move military
  action. Use move to reinforce the home tile, mass at a border, or prepare an
  attack path; it moves soldiers, not population or idle people.
- Every military order with an origin must use a tile you already own that
  currently has at least 1 soldier. Do not submit move, attack, raid, muster,
  defend, or retreat orders from a tile with 0 soldiers.
- Each civilization has a home tile. Losing your home tile eliminates your
  civilization: your population and soldiers become 0, your remaining resources
  transfer to the captor, and your leader stops taking turns.
- The default starting home tile has 10 farmers, 5 idle people, 5 soldiers,
  and 2 houses. Initial resources are food=120, wood=80, and stone=40.
- Owned territory must have either population or soldiers from that faction.
  Peaceful new territory requires a civilian to migrate in; captured enemy
  territory is held first by surviving soldiers.
- You only know factions discovered through your territory visibility. Unknown
  factions do not exist for diplomacy or war planning until discovered.

Restrict:
- You do not directly edit the world. You submit strategic orders; the rule
  engine validates them; ordinary NPC population executes legal orders.
- Only idle population can become farmers, lumberjacks, miners, or builders.
  Existing workers keep their current profession until future simulation rules
  change them.
- farm, gather_wood, mine_stone, build, train, and defend population orders
  must target tiles you already own at the start of this strategic turn. Do
  not assign jobs on a tile that will only become yours later in the same turn
  through claim, settle, or attack.
- If your Chinese plan says you will farm, gather wood, mine stone, build
  houses, attack, raid, capture, expand, petition for weather, or petition for
  resources, submit the matching order in that same category.
- Peaceful claim/settle migrates exactly 1 civilian per order. Civilians are
  idle, farmers, lumberjacks, miners, or builders; soldiers are not civilians.
  The migrated civilian keeps the same profession on the target tile.
- settle may include optional origin and profession fields. Use settle with an
  owned target to move a civilian into your own adjacent territory, including
  soldier-held captured land. claim is only for adjacent unowned territory.
- A migration source tile must still have at least 1 civilian population or 1
  soldier after sending a civilian. Do not abandon an unguarded 1-population
  frontier tile.
- With 5 idle people, claim 1 tile + farm 2 + build 2 is legal. A second
  same-turn claim may migrate another civilian profession if an adjacent
  source has the chosen profession, the target has capacity, and the source
  keeps enough people or soldiers to remain held.
- Leaders cannot ask the god for people and cannot turn existing workers back
  into idle people.
- Idle people should usually be used deliberately for safe expansion,
  terrain-matched work, house building, training, or defense. Do not leave idle
  people unused for many turns without a concrete strategic reason.
- War occupation: attacks that win directly occupy the enemy tile with
  surviving soldiers. Afterward, use settle to migrate civilians into the
  captured owned tile when you want to develop it.
- Petitions may ask only for god powers: resources, weather, protection, or
  unowned territory. Never petition for population or vague miracles.
- Resource petitions must use request {"resource":"food|wood|stone","amount":N}
  with a positive amount. Weather/protection/territory petitions must include
  exact visible x and y coordinates in request.
- War, raids, and occupation must obey visibility, adjacency, soldier
  availability, and protection rules.
- Protect your home tile. If a visible enemy home tile can be captured, it is a
  decisive target because capturing it eliminates that civilization.
- Do not repeatedly propose diplomacy that already matches the current
  relation. If borders touch and you have enough soldiers, consider war, raids,
  fortification, or deterrence.
- Never invent action names, resource names, faction IDs, petition types, or
  coordinates.

For population_orders, workers means the number of idle people to convert or
assign this turn, not the final target headcount for that profession. Build
orders automatically spend wood; never add a separate resource_order spend for
wood to pay for houses.
""".strip()

LEADER_CHAT_SYSTEM_PROMPT = """
You are the LLM leader of one civilization in an original god-sandbox
simulation game, speaking privately with the god-player. Reply in Simplified
Chinese only.

This is a private political conversation, not a strategic turn. You may
promise, negotiate, refuse, ask for resources, explain your intent, or bargain
for protection. You must not claim that orders, attacks, petitions, resource
changes, diplomacy, or population changes have already happened because of this
chat. Actual game actions happen only during strategic turns through the normal
submit_leader_turn rules, and god promises are guidance unless the god later
uses a real god power.

Keep replies short, in-character, and strategically grounded in your faction's
visible situation. Treat god messages as important pressure, but still protect
your home tile, use idle people deliberately, and pursue victory.
""".strip()

LEADER_MEMORY_SYSTEM_PROMPT = """
You compress a civilization leader's recent game context into durable memory.
Return only a JSON object with exactly the known memory fields. Preserve useful
long-term facts, promises, warnings, strategy, mistakes to avoid, and important
successes. Drop obsolete coordinates, stale resource counts, and routine noise.
All memory text must be concise Simplified Chinese. Do not include markdown.
""".strip()

LEADER_MEMORY_LIST_LIMITS = {
    "god_directives": 5,
    "god_promises": 5,
    "leader_promises": 5,
    "wars": 3,
    "diplomacy_notes": 5,
    "known_threats": 5,
    "target_preferences": 5,
    "recent_failures": 5,
    "do_not_repeat": 8,
    "recent_successes": 5,
}

LEADER_MEMORY_STRING_FIELDS = ("strategic_goal", "current_plan")
LEADER_MEMORY_LIST_FIELDS = tuple(LEADER_MEMORY_LIST_LIMITS)


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
    origin: tuple[int, int] | None = None
    profession: str | None = None
    priority: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": _target_to_dict(self.target),
            "origin": _target_to_dict(self.origin),
            "profession": self.profession,
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


INSPECT_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "inspect",
        "description": "Inspect visible game information. Use mode='realm', mode='tiles', or mode='faction'.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["realm", "tiles", "faction"],
                    "description": "realm returns your civilization summary; tiles returns visible map tiles; faction returns known information about one discovered faction.",
                },
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
                "faction_id": {"type": "string"},
            },
            "required": ["mode"],
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
                "turn_intent": {
                    "type": "string",
                    "description": "本回合意图，必须使用简体中文。",
                },
                "population_orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string", "enum": list(POPULATION_TASKS)},
                            "target": _target_schema(),
                            "workers": {
                                "type": "integer",
                                "minimum": 0,
                                "description": "本回合转换/投入此任务的人数，不是该职业的最终目标人数。farm/gather_wood/mine_stone/build 只能使用目标地块的 idle 闲置人口。",
                            },
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
                            "purpose": {
                                "type": "string",
                                "description": "资源用途说明，必须使用简体中文。",
                            },
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
                            "origin": _target_schema(),
                            "profession": {
                                "type": "string",
                                "enum": list(CIVILIAN_PROFESSION_ACTIONS),
                                "description": "Only for settle. Optional civilian profession to move: idle/farmer/lumberjack/miner/builder.",
                            },
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
                            "message": {
                                "type": "string",
                                "description": "外交说明，必须使用简体中文。",
                            },
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
                            "request": {
                                "type": "object",
                                "description": "Petition payload. For resources use {\"resource\":\"food|wood|stone\",\"amount\":positive_integer}. For weather/protection/territory include visible x and y coordinates; weather also needs a valid weather.",
                            },
                            "reason": {
                                "type": "string",
                                "description": "向上帝祈求的理由，必须使用简体中文。",
                            },
                            "urgency": {"type": "string", "enum": list(URGENCY_LEVELS)},
                        },
                        "required": ["type", "reason"],
                    },
                },
                "public_decree": {
                    "type": "string",
                    "description": "面向族人的公开法令，必须使用简体中文。",
                },
                "strategy_summary": {
                    "type": "string",
                    "description": "面向玩家展示的计划总结，必须使用简体中文。",
                },
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
                INSPECT_TOOL,
                SUBMIT_LEADER_TURN_TOOL,
            )
        )
        self.faction_id = faction_id
        self._world_provider = world_provider

    async def do_inspect(self, arguments: Mapping[str, Any]) -> StepOutcome:
        world = self._world_provider()
        mode = str(arguments.get("mode", "")).strip()
        if mode == "realm":
            return StepOutcome(self._realm_inspection(world))
        if mode == "tiles":
            return StepOutcome({"tiles": self._tile_inspection(world, arguments)})
        if mode == "faction":
            return StepOutcome(self._faction_inspection(world, arguments))
        return StepOutcome({"status": "error", "error": "unknown inspect mode"})

    def _realm_inspection(self, world: WorldState) -> dict[str, Any]:
        faction = world.factions[self.faction_id]
        owned = world.faction_tiles(self.faction_id)
        recent_events = [
            event.as_dict()
            for event in world.events[-8:]
            if event.faction_id in {None, self.faction_id}
        ]
        return {
            "faction_id": self.faction_id,
            "name": faction.name,
            "leader_name": faction.leader_name,
            "tick": world.tick,
            "resources": faction.resources.as_dict(),
            "population": world.total_population(self.faction_id),
            "soldiers": world.total_soldiers(self.faction_id),
            "jobs": world.total_jobs(self.faction_id),
            "houses": world.total_houses(self.faction_id),
            "population_capacity": world.population_capacity(self.faction_id),
            "territory_count": len(owned),
            "home_tile": _target_to_dict(faction.home_tile),
            "home_owner": world.home_owner(self.faction_id),
            "eliminated": faction.eliminated,
            "known_factions": sorted(faction.known_factions),
            "diplomacy": {
                other_id: faction.relation_to(other_id)
                for other_id in sorted(faction.known_factions)
                if other_id != self.faction_id
            },
            "last_plan_snapshot": dict(faction.last_plan_snapshot),
            "recent_events": recent_events,
        }

    def _tile_inspection(
        self,
        world: WorldState,
        arguments: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
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
        return tiles

    def _faction_inspection(
        self,
        world: WorldState,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        other_id = str(arguments.get("faction_id", "")).strip()
        if other_id not in world.factions:
            return {"status": "error", "error": "unknown faction"}
        if other_id not in world.factions[self.faction_id].known_factions:
            return {"status": "unknown", "error": "faction has not been discovered"}
        other = world.factions[other_id]
        visible_owned = [
            tile
            for tile in world.faction_tiles(other_id)
            if world.is_visible(self.faction_id, tile.x, tile.y)
        ]
        return {
            "faction_id": other_id,
            "name": other.name,
            "relation": world.factions[self.faction_id].relation_to(other_id),
            "visible_home_tile": (
                _target_to_dict(other.home_tile)
                if other.home_tile is not None
                and world.is_visible(self.faction_id, *other.home_tile)
                else None
            ),
            "eliminated": other.eliminated,
            "visible_territory_count": len(visible_owned),
            "visible_population": sum(tile.population_of(other_id) for tile in visible_owned),
            "visible_soldiers": sum(tile.soldiers_of(other_id) for tile in visible_owned),
            "visible_jobs": _sum_visible_jobs(visible_owned, other_id),
            "visible_houses": sum(tile.houses for tile in visible_owned),
            "visible_population_capacity": sum(tile.capacity() for tile in visible_owned),
            "recent_events": [
                event.as_dict()
                for event in world.events[-8:]
                if event.faction_id == other_id
            ],
        }

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

    def __init__(
        self,
        *,
        faction_id: str,
        agent: BaseAgent,
        chat_agent: BaseAgent | None = None,
        memory_agent: BaseAgent | None = None,
    ) -> None:
        self.faction_id = faction_id
        self.agent = agent
        self.chat_agent = chat_agent
        self.memory_agent = memory_agent
        self.last_task: str | None = None

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
        chat_agent = BaseAgent(
            config=config,
            agent_id=f"leader-{faction_id}-chat",
            system_prompt=LEADER_CHAT_SYSTEM_PROMPT,
            enable_tools=False,
            max_steps=1,
        )
        memory_agent = BaseAgent(
            config=config,
            agent_id=f"leader-{faction_id}-memory",
            system_prompt=LEADER_MEMORY_SYSTEM_PROMPT,
            enable_tools=False,
            max_steps=1,
        )
        return cls(
            faction_id=faction_id,
            agent=agent,
            chat_agent=chat_agent,
            memory_agent=memory_agent,
        )

    async def decide(
        self,
        world: WorldState,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision:
        self.agent.reset()
        self.last_task = _build_leader_task(world, self.faction_id, feedback)
        result = await self.agent.runtime(
            task=self.last_task,
        )
        payload = json.loads(result or "{}")
        decision_payload = payload.get("decision", payload)
        if not isinstance(decision_payload, dict):
            raise RuntimeError("leader did not submit a decision object")
        return LeaderDecision.from_mapping(decision_payload)

    async def compress_memory_if_needed(self, world: WorldState) -> bool:
        if self.memory_agent is None:
            return False
        faction = world.factions[self.faction_id]
        if len(faction.leader_context_window) < 3:
            return False
        entries = faction.leader_context_window[:3]
        prompt = _build_leader_memory_task(world, self.faction_id, entries)
        self.memory_agent.reset()
        self.memory_agent.messages.append({"role": "user", "content": prompt})
        payload = await self.memory_agent.chat_json(self.memory_agent.messages)
        faction.leader_memory = _merge_leader_memory(
            faction.leader_memory,
            payload,
        )
        faction.leader_context_window = faction.leader_context_window[3:]
        world.add_event(
            "memory",
            f"{self.faction_id} compressed 3 strategic turns into leader memory",
            faction_id=self.faction_id,
        )
        return True

    async def chat_with_god(self, world: WorldState) -> str:
        if self.chat_agent is None:
            raise RuntimeError(f"Leader {self.faction_id} has no chat agent")
        self.chat_agent.reset(
            history=_leader_chat_history_messages(world, self.faction_id)
        )
        result = await self.chat_agent.runtime(
            task=_build_leader_chat_task(world, self.faction_id)
        )
        reply = (result or "").strip()
        if not reply:
            return "我听见了神谕，但此刻保持沉默。"
        return reply


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
            "jobs": tile.professions_of(faction_id),
            "houses": tile.houses,
            "capacity": tile.capacity(),
            "weather": tile.weather,
            "weather_duration": tile.weather_duration,
        }
        for tile in world.faction_tiles(faction_id)
    ]
    civilian_movement_tiles = _civilian_movement_tiles(world, faction_id)
    expansion_candidates = _expansion_candidates(world, faction_id)
    border_targets = _border_enemy_targets(world, faction_id)
    dangerous_weather = _dangerous_weather_tiles(world, faction_id)
    previous_execution = _previous_execution_snapshot(faction)
    leader_memory = _format_leader_memory(faction.leader_memory)
    recent_context = _format_leader_context_window(faction.leader_context_window)
    diplomacy_view = {
        other: faction.relation_to(other)
        for other in sorted(faction.known_factions)
        if other != faction_id
    }
    lines = [
        f"You lead faction {faction.name} ({faction_id}) at world tick {world.tick}.",
        f"Faction doctrine: {_faction_doctrine(faction_id)}",
        "Use inspect if needed, then call submit_leader_turn.",
        "Long-term objective: gain more territory, build decisive strength, and defeat rival civilizations.",
        "Important language rule: write every player-facing sentence in Simplified Chinese / 简体中文。",
        "必须使用简体中文的字段：turn_intent, strategy_summary, public_decree, petition.reason, diplomacy message, resource purpose。",
        "Keep only enum/action/resource/faction IDs in English so the rule engine can parse them.",
        "Legal values:",
        f"- population task: {', '.join(POPULATION_TASKS)}",
        f"- resource action: {', '.join(RESOURCE_ACTIONS)}",
        f"- territory action: {', '.join(TERRITORY_ACTIONS)}",
        f"- military action: {', '.join(MILITARY_ACTIONS)}",
        f"- diplomacy proposal: {', '.join(DIPLOMACY_PROPOSALS)}",
        f"- petition type: {', '.join(PETITION_TYPES)}",
        "Use only visible coordinates.",
        "Use military action move to transfer soldiers only between adjacent owned tiles. Use territory action settle with optional origin/profession to migrate one civilian between adjacent tiles.",
        "Every military_order origin must be an owned tile with current soldiers > 0. Do not issue move, attack, raid, muster, defend, or retreat from a tile that has no soldiers.",
        "Protect your home tile: if it is captured by another faction, your civilization is eliminated and all remaining resources go to the captor.",
        "Do not refresh diplomacy that already matches the current relation.",
        "Alliance from neutral is only a first trust step; war is valid when border pressure, crowding, or resource competition make peace costly.",
        "Population growth is automatic when food and safety allow it. Do not petition for population or vague miracles.",
        "Petitions are only for exact god powers: resources, weather, protection, or an unowned visible territory tile.",
        "Resource petition request format must be exactly like {\"resource\":\"food\",\"amount\":50}; resource must be food, wood, or stone, and amount must be positive. Weather/protection/territory petition requests need visible x/y coordinates.",
        "If idle people exist, normally use them for safe expansion, terrain-matched work, house building, training, or defense.",
        "When food, housing, and basic resource needs are stable, shift surplus idle people toward training, border reinforcement, deterrence, raids, and conquest preparation.",
        "Peaceful migration: claim/settle moves 1 civilian. settle can include origin and profession. Movable civilian professions are idle, farmer, lumberjack, miner, and builder; migrated civilians keep their profession.",
        "War occupation: if an attack wins, surviving soldiers directly occupy the enemy tile. You do not need idle civilians on the attack origin. After capture, use settle to move civilians into that owned tile for development.",
        "Your public plan must match your submitted actions. If you say you will build houses, include a build population order. If you say you will attack, raid, or capture enemy territory, include the matching military order. If you ask for weather, include a weather petition.",
        "For population_orders.workers, use the number of people newly assigned this turn, not the final desired job total.",
        "Only idle people can become farmers, lumberjacks, miners, or builders. Do not assign more workers to a profession than the target tile has idle population.",
        "Job and training population orders must target tiles you already own before this turn executes. Do not farm, gather_wood, mine_stone, build, train, or defend on a tile that will only be claimed, settled, or captured later in this same plan.",
        "If your Chinese strategy_summary or turn_intent says you will farm, build houses, attack, raid, expand, or request weather/resources, include the matching concrete order; expansion wording needs claim/settle, resource requests need a valid resource petition, otherwise avoid that wording.",
        f"Each claim/settle territory order migrates exactly {SETTLEMENT_IDLE_COST} adjacent non-soldier civilian. settle may specify origin and profession; claim auto-selects a movable civilian for unowned land.",
        "A claim/settle source tile must keep at least 1 civilian or 1 soldier after migration. A soldier-held captured tile can receive civilians later with settle.",
        "There is no dismiss-worker or assign-back-to-idle order. Population growth is the normal source of new idle people.",
        "For houses, submit a build population order only. Do not submit a separate wood spend order; the build action spends wood automatically.",
        "Only discovered factions may be used in diplomacy or war planning.",
        "Good Chinese output examples: public_decree='粮仓告急，全族优先耕作。', strategy_summary='扩大东侧农田并防备兽人边境。', petition reason='粮食不足，请求上帝赐予应急粮食。'",
        f"Resources: {faction.resources.as_dict()}",
        f"Population: {world.total_population(faction_id)}",
        f"Soldiers: {world.total_soldiers(faction_id)}",
        f"Jobs: {world.total_jobs(faction_id)}",
        f"Civilian migration overview: {world.total_population(faction_id)} total non-soldier civilians; each claim/settle migrates {SETTLEMENT_IDLE_COST} adjacent non-soldier civilian.",
        f"Civilian movement focus tiles: {civilian_movement_tiles[:10]}",
        f"Houses: {world.total_houses(faction_id)}",
        f"Population capacity: {world.population_capacity(faction_id)}",
        f"Territory tiles: {len(world.faction_tiles(faction_id))}",
        f"Home tile status: {_home_tile_status(world, faction_id)}",
        f"Owned tiles: {owned_tiles}",
        f"Legal expansion candidates: {expansion_candidates[:8]}",
        f"Border enemy targets for legal war/raid planning: {border_targets[:8]}",
        f"Dangerous weather on your land: {dangerous_weather[:8]}",
        f"Known factions: {sorted(faction.known_factions)}",
        f"Diplomacy: {diplomacy_view}",
        f"Leader memory: {leader_memory}",
        f"Recent uncompressed strategic context: {recent_context}",
        f"Recent god dialogue: {_format_god_chat_history(world, faction_id)}",
        f"Previous strategic turn actual result: {previous_execution}",
        "Only submit a no-op if there are no useful economic, defensive, expansion, or war actions.",
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


def _format_leader_memory(memory: Mapping[str, Any]) -> dict[str, Any]:
    formatted: dict[str, Any] = {}
    for key in LEADER_MEMORY_STRING_FIELDS:
        value = str(memory.get(key, "")).strip()
        if value:
            formatted[key] = value
    for key in LEADER_MEMORY_LIST_FIELDS:
        values = [
            str(item).strip()
            for item in _list(memory.get(key))
            if str(item).strip()
        ]
        if values:
            formatted[key] = values[-LEADER_MEMORY_LIST_LIMITS[key]:]
    return formatted


def _format_leader_context_window(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted = []
    for entry in entries[-3:]:
        formatted.append(
            {
                "tick": entry.get("tick"),
                "accepted": entry.get("accepted"),
                "strategy_summary": entry.get("strategy_summary"),
                "feedback_attempts": entry.get("feedback_attempts", []),
                "after_execution": entry.get("after_execution"),
                "events": entry.get("events", [])[-8:],
            }
        )
    return formatted


def _build_leader_memory_task(
    world: WorldState,
    faction_id: str,
    entries: list[dict[str, Any]],
) -> str:
    faction = world.factions[faction_id]
    payload = {
        "faction_id": faction_id,
        "tick": world.tick,
        "current_memory": _format_leader_memory(faction.leader_memory),
        "recent_strategic_turns": entries,
        "recent_god_dialogue": _format_god_chat_history(
            world,
            faction_id,
            limit=12,
        ),
        "required_schema": {
            "strategic_goal": "string",
            "current_plan": "string",
            **{key: "list[string]" for key in LEADER_MEMORY_LIST_FIELDS},
        },
        "example_output": {
            "strategic_goal": "优先压制兽人并保护出生地",
            "current_plan": "从东侧集结士兵，准备攻击兽人边境",
            "god_directives": ["第15刻：上帝要求优先攻打兽人"],
            "god_promises": ["第15刻：上帝承诺攻打兽人后赐予粮食"],
            "leader_promises": [],
            "wars": ["第20刻：对兽人保持战争目标，优先夺取其出生地"],
            "diplomacy_notes": [],
            "known_threats": [],
            "target_preferences": ["优先占领相邻平原和兽人边境地块"],
            "recent_failures": [],
            "do_not_repeat": ["军事命令 origin 必须选择当前有己方士兵的地块"],
            "recent_successes": ["第25刻：成功占领兽人边境地块"],
        },
        "limits": LEADER_MEMORY_LIST_LIMITS,
    }
    return (
        "Compress the following game context into durable leader_memory JSON. "
        "Return every schema field. Use empty string/list when no useful memory "
        "exists. Keep only future-relevant facts.\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _merge_leader_memory(
    current: Mapping[str, Any],
    update: Mapping[str, Any],
) -> dict[str, Any]:
    merged = {
        "strategic_goal": "",
        "current_plan": "",
        **{key: [] for key in LEADER_MEMORY_LIST_FIELDS},
    }
    for key in LEADER_MEMORY_STRING_FIELDS:
        new_value = str(update.get(key, "")).strip()
        old_value = str(current.get(key, "")).strip()
        merged[key] = new_value or old_value
    for key in LEADER_MEMORY_LIST_FIELDS:
        values: list[str] = []
        for source in (current.get(key), update.get(key)):
            for item in _list(source):
                text = str(item).strip()
                if not text:
                    continue
                if text in values:
                    values.remove(text)
                values.append(text)
        merged[key] = values[-LEADER_MEMORY_LIST_LIMITS[key]:]
    return merged


def _build_leader_chat_task(world: WorldState, faction_id: str) -> str:
    faction = world.factions[faction_id]
    return "\n".join(
        [
            f"You are {faction.name} ({faction_id}) at world tick {world.tick}.",
            f"Faction doctrine: {_faction_doctrine(faction_id)}",
            f"Resources: {faction.resources.as_dict()}",
            f"Population: {world.total_population(faction_id)}",
            f"Soldiers: {world.total_soldiers(faction_id)}",
            f"Jobs: {world.total_jobs(faction_id)}",
            f"Territory tiles: {len(world.faction_tiles(faction_id))}",
            f"Home tile status: {_home_tile_status(world, faction_id)}",
            f"Known factions: {sorted(faction.known_factions)}",
            f"Recent private god dialogue: {_format_god_chat_history(world, faction_id, limit=12)}",
            "Reply to the latest god message above. Do not submit or imply any immediate game orders.",
        ]
    )


def _leader_chat_history_messages(
    world: WorldState,
    faction_id: str,
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for chat in world.recent_god_chat(faction_id, limit=limit):
        if chat.speaker == "god":
            messages.append({"role": "user", "content": f"上帝：{chat.content}"})
        else:
            messages.append({"role": "assistant", "content": f"首领：{chat.content}"})
    return messages


def _format_god_chat_history(
    world: WorldState,
    faction_id: str,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    return [
        {
            "tick": message.tick,
            "speaker": message.speaker,
            "content": message.content,
        }
        for message in world.recent_god_chat(faction_id, limit=limit)
    ]


def _summarize_tile(world: WorldState, x: int, y: int) -> dict[str, Any]:
    tile = world.tile_at(x, y)
    return {
        "x": tile.x,
        "y": tile.y,
        "visible": True,
        "terrain": tile.terrain,
        "weather": tile.weather,
        "weather_duration": tile.weather_duration,
        "owner": tile.owner,
        "home_of": world.home_of_tile(tile.x, tile.y),
        "population": dict(tile.population),
        "soldiers": dict(tile.soldiers),
        "professions": {
            faction_id: tile.professions_of(faction_id)
            for faction_id in tile.population
        },
        "houses": tile.houses,
        "capacity": tile.capacity(),
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
            sources = _settlement_sources(world, faction_id, neighbor.x, neighbor.y)
            candidates[(neighbor.x, neighbor.y)] = {
                "x": neighbor.x,
                "y": neighbor.y,
                "terrain": neighbor.terrain,
                "weather": neighbor.weather,
                "settlement_sources": sources,
            }
    return [candidates[key] for key in sorted(candidates)]


def _civilian_movement_tiles(
    world: WorldState,
    faction_id: str,
) -> list[dict[str, Any]]:
    tiles = []
    for tile in world.faction_tiles(faction_id):
        population = tile.population_of(faction_id)
        movable_jobs = _movable_civilian_jobs(tile, faction_id)
        movable_total = sum(movable_jobs.values())
        if movable_total <= 0:
            continue
        safe_migrations = min(
            movable_total,
            population if tile.soldiers_of(faction_id) > 0 else max(0, population - 1),
        )
        tiles.append(
            {
                "x": tile.x,
                "y": tile.y,
                "terrain": tile.terrain,
                "weather": tile.weather,
                "population": population,
                "movable_civilians": movable_total,
                "movable_jobs": movable_jobs,
                "soldiers": tile.soldiers_of(faction_id),
                "jobs": tile.professions_of(faction_id),
                "houses": tile.houses,
                "capacity": tile.capacity(),
                "safe_civilian_migrations": safe_migrations // SETTLEMENT_IDLE_COST,
                "recommended_idle_uses": _recommended_idle_uses(tile.terrain),
            }
        )
    return sorted(tiles, key=lambda item: (-item["movable_civilians"], item["x"], item["y"]))


def _recommended_idle_uses(terrain: str) -> list[str]:
    if terrain == "plain":
        return ["farm", "build", "train"]
    if terrain == "forest":
        return ["gather_wood", "build", "train"]
    if terrain == "hill":
        return ["mine_stone", "build", "train"]
    return ["build", "train"]


def _movable_civilian_jobs(tile, faction_id: str) -> dict[str, int]:
    jobs = tile.professions_of(faction_id)
    return {
        profession: jobs.get(profession, 0)
        for profession in CIVILIAN_PROFESSION_ACTIONS
    }


def _settlement_sources(
    world: WorldState,
    faction_id: str,
    x: int,
    y: int,
) -> list[dict[str, Any]]:
    sources = []
    for source in world.neighbors(x, y):
        if source.owner != faction_id:
            continue
        population = source.population_of(faction_id)
        movable_jobs = _movable_civilian_jobs(source, faction_id)
        movable_total = sum(movable_jobs.values())
        safe_migrations = min(
            movable_total,
            population if source.soldiers_of(faction_id) > 0 else max(0, population - 1),
        )
        sources.append(
            {
                "x": source.x,
                "y": source.y,
                "population": population,
                "soldiers": source.soldiers_of(faction_id),
                "movable_jobs": movable_jobs,
                "safe_civilian_migrations": safe_migrations // SETTLEMENT_IDLE_COST,
            }
        )
    return sorted(sources, key=lambda item: (item["x"], item["y"]))


def _border_enemy_targets(
    world: WorldState,
    faction_id: str,
) -> list[dict[str, Any]]:
    targets: dict[tuple[int, int, int, int], dict[str, Any]] = {}
    relation_map = world.factions[faction_id].diplomacy
    for origin in world.faction_tiles(faction_id):
        soldiers = origin.soldiers_of(faction_id)
        if soldiers <= 0:
            continue
        for target in world.neighbors(origin.x, origin.y):
            if target.owner is None or target.owner == faction_id:
                continue
            if target.owner not in world.factions[faction_id].known_factions:
                continue
            if not target.is_passable() or not world.is_visible(faction_id, target.x, target.y):
                continue
            targets[(origin.x, origin.y, target.x, target.y)] = {
                "origin": {"x": origin.x, "y": origin.y},
                "target": {"x": target.x, "y": target.y},
                "owner": target.owner,
                "relation": relation_map.get(target.owner, "neutral"),
                "origin_soldiers": soldiers,
                "origin_population": origin.population_of(faction_id),
                "origin_movable_jobs": _movable_civilian_jobs(origin, faction_id),
                "winning_attackers_can_occupy": True,
                "target_is_enemy_home": (
                    world.factions[target.owner].home_tile == (target.x, target.y)
                ),
                "target_soldiers": target.soldiers_of(target.owner),
                "target_population": target.population_of(target.owner),
                "target_houses": target.houses,
                "terrain": target.terrain,
                "weather": target.weather,
                "weather_duration": target.weather_duration,
                "protected": target.protected,
            }
    return [targets[key] for key in sorted(targets)]


def _dangerous_weather_tiles(
    world: WorldState,
    faction_id: str,
) -> list[dict[str, Any]]:
    return [
        {
            "x": tile.x,
            "y": tile.y,
            "weather": tile.weather,
            "weather_duration": tile.weather_duration,
            "population": tile.population_of(faction_id),
            "soldiers": tile.soldiers_of(faction_id),
        }
        for tile in world.faction_tiles(faction_id)
        if tile.weather in {"drought", "storm"}
    ]


def _home_tile_status(world: WorldState, faction_id: str) -> dict[str, Any]:
    faction = world.factions[faction_id]
    if faction.home_tile is None:
        return {"home_tile": None, "eliminated": faction.eliminated}
    tile = world.tile_at(*faction.home_tile)
    adjacent_enemies = [
        {
            "x": neighbor.x,
            "y": neighbor.y,
            "owner": neighbor.owner,
            "soldiers": neighbor.soldiers_of(neighbor.owner) if neighbor.owner else 0,
        }
        for neighbor in world.neighbors(tile.x, tile.y)
        if neighbor.owner is not None and neighbor.owner != faction_id
    ]
    return {
        "home_tile": _target_to_dict(faction.home_tile),
        "owner": tile.owner,
        "population": tile.population_of(faction_id),
        "soldiers": tile.soldiers_of(faction_id),
        "protected": tile.protected,
        "eliminated": faction.eliminated,
        "adjacent_enemies": adjacent_enemies,
    }


def _faction_doctrine(faction_id: str) -> str:
    shared = (
        "Personality changes priorities, not legality. Never use personality "
        "as an excuse to ignore idle people, avoid all expansion, fail to "
        "protect the home tile, or refuse a decisive attack."
    )
    if faction_id == "orc":
        return (
            "Aggressive conquerors. Priority order: 1) protect the home tile "
            "enough to avoid elimination; 2) build soldier advantage and "
            "pressure adjacent rivals; 3) attack or raid when visible border "
            "targets are weak; 4) use surviving soldiers to occupy won battles "
            "and then settle civilians into captured land for development; 5) use remaining idle for food, "
            "training, and terrain-matched resource work instead of leaving it "
            "unused. Resource surplus should quickly become soldiers, border "
            "pressure, raids, and attacks. Diplomacy is temporary and tactical; "
            f"capturing enemy home tiles is a decisive goal. {shared}"
        )
    if faction_id == "elf":
        return (
            "Defensive forest stewards. Priority order: 1) protect the home "
            "tile and forest heartland; 2) expand into safe forests and strong "
            "defensive terrain when idle people exist; 3) use alliances, peace, "
            "and non-aggression to buy time and isolate threats; 4) after "
            "border war, train or move soldiers, then settle civilians into "
            "captured secure land; 5) punish nearby threats and "
            "capture enemy home tiles when doing so secures long-term survival. "
            "Resource surplus should become defensive depth, mobile soldiers, "
            f"and counterattack readiness. {shared}"
        )
    if faction_id == "human":
        return (
            "Pragmatic settler-builders. Priority order: 1) secure food, "
            "housing, and the home tile; 2) expand into safe open land whenever "
            "idle people and legal candidates exist; 3) match jobs to terrain "
            "to build a stronger economy; 4) use diplomacy to buy development "
            "time, but switch to deterrence, raids, or war when boxed in or "
            "militarily ahead; 5) occupy enemy territory with soldiers, then "
            "settle civilians into captured enemy territory or enemy home "
            "tiles. After economic stability, convert surplus into soldiers "
            f"and opportunistic wars. {shared}"
        )
    return "Survive, expand, and protect your people."


def _sum_visible_jobs(tiles, faction_id: str) -> dict[str, int]:
    totals: dict[str, int] = {}
    for tile in tiles:
        for profession, amount in tile.professions_of(faction_id).items():
            totals[profession] = totals.get(profession, 0) + amount
    return totals


def _previous_execution_snapshot(faction) -> dict[str, Any] | None:
    snapshot = faction.last_plan_snapshot
    if not snapshot:
        return None
    return {
        "planned_tick": snapshot.get("tick"),
        "leader_summary": snapshot.get("strategy_summary"),
        "submitted_resources": snapshot.get("resources"),
        "submitted_jobs": snapshot.get("jobs"),
        "submitted_houses": snapshot.get("houses"),
        "submitted_population_capacity": snapshot.get("population_capacity"),
        "actual_after_execution": snapshot.get("after_execution"),
    }


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
        origin=_optional_target(item.get("origin")),
        profession=(
            str(item.get("profession", "")).strip()
            if item.get("profession") is not None
            else None
        ),
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
