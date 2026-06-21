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
  can occupy enemy land if the battle is won and idle people can move in.
- When food, housing, and basic resource needs are stable, shift surplus idle
  people toward training, border reinforcement, deterrence, raids, and conquest
  preparation.
- Soldiers can move only between adjacent owned tiles with the move military
  action. Use move to reinforce the home tile, mass at a border, or prepare an
  attack path; it moves soldiers, not population or idle people.
- Each civilization has a home tile. Losing your home tile eliminates your
  civilization: your population and soldiers become 0, your remaining resources
  transfer to the captor, and your leader stops taking turns.
- The default starting home tile has 10 farmers, 5 idle people, 5 soldiers,
  and 2 houses. Initial resources are food=120, wood=80, and stone=40.
- Owned territory must have population from that faction. New territory and
  captured territory require idle people to occupy it.
- You only know factions discovered through your territory visibility. Unknown
  factions do not exist for diplomacy or war planning until discovered.

Restrict:
- You do not directly edit the world. You submit strategic orders; the rule
  engine validates them; ordinary NPC population executes legal orders.
- Only idle population can become farmers, lumberjacks, miners, or builders.
  Existing workers keep their current profession until future simulation rules
  change them.
- Peaceful claim/settle consumes exactly 1 idle person per new territory. This
  cost shares the same idle budget as farming, lumberjacking, mining, building,
  training, and defending.
- A claim/settle source tile must still have at least 1 civilian population
  after sending 1 idle settler. Do not drain a 1-population frontier tile to
  settle another tile. Soldiers do not keep ownership by
  themselves.
- With 5 idle people, claim 1 tile + farm 2 + build 2 is legal. Claim 2 tiles
  + farm 2 + build 2 is illegal because it overuses idle people.
- Leaders cannot ask the god for people and cannot turn existing workers back
  into idle people.
- Idle people should usually be used deliberately for safe expansion,
  terrain-matched work, house building, training, or defense. Do not leave idle
  people unused for many turns without a concrete strategic reason.
- Exception: if border war has happened or you plan to attack and occupy an
  adjacent enemy tile, you may preserve at least 1 idle person on the attacking
  origin tile. Captured land can only be occupied by idle people moving from
  the battle origin tile into the battle target tile; idle people elsewhere in
  the realm cannot occupy that captured tile.
- Petitions may ask only for god powers: resources, weather, protection, or
  unowned territory. Never petition for population or vague miracles.
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
                            "request": {"type": "object"},
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
    ) -> None:
        self.faction_id = faction_id
        self.agent = agent
        self.chat_agent = chat_agent

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
        return cls(faction_id=faction_id, agent=agent, chat_agent=chat_agent)

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
    idle_focus_tiles = _idle_focus_tiles(world, faction_id)
    expansion_candidates = _expansion_candidates(world, faction_id)
    border_targets = _border_enemy_targets(world, faction_id)
    dangerous_weather = _dangerous_weather_tiles(world, faction_id)
    previous_execution = _previous_execution_snapshot(faction)
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
        "Use military action move to transfer soldiers only between adjacent owned tiles. It cannot move population or idle people.",
        "Protect your home tile: if it is captured by another faction, your civilization is eliminated and all remaining resources go to the captor.",
        "Do not refresh diplomacy that already matches the current relation.",
        "Alliance from neutral is only a first trust step; war is valid when border pressure, crowding, or resource competition make peace costly.",
        "Population growth is automatic when food and safety allow it. Do not petition for population or vague miracles.",
        "Petitions are only for exact god powers: resources, weather, protection, or an unowned visible territory tile.",
        "If idle people exist, normally use them for safe expansion, terrain-matched work, house building, training, or defense.",
        "When food, housing, and basic resource needs are stable, shift surplus idle people toward training, border reinforcement, deterrence, raids, and conquest preparation.",
        "War occupation exception: if adjacent border war has happened, or you plan to attack and occupy an adjacent enemy tile, reserve at least 1 idle person on that exact attacking origin tile. Captured land can only be occupied by idle people moving from the battle origin tile into the battle target tile; idle people on other tiles do not count.",
        "Your public plan must match your submitted actions. If you say you will build houses, include a build population order. If you say you will attack, raid, or capture enemy territory, include the matching military order. If you ask for weather, include a weather petition.",
        "For population_orders.workers, use the number of people newly assigned this turn, not the final desired job total.",
        "Only idle people can become farmers, lumberjacks, miners, or builders. Do not assign more workers to a profession than the target tile has idle population.",
        f"Each claim/settle territory order consumes exactly {SETTLEMENT_IDLE_COST} idle people from an adjacent owned tile. Count this cost together with profession and training orders before submitting.",
        "A claim/settle source tile must keep at least 1 civilian after settlers leave. Do not use a 1-person new outpost as the source for another same-turn claim, and do not combine its last civilian with training.",
        "There is no dismiss-worker or assign-back-to-idle order. Population growth is the normal source of new idle people.",
        "For houses, submit a build population order only. Do not submit a separate wood spend order; the build action spends wood automatically.",
        "Only discovered factions may be used in diplomacy or war planning.",
        "Good Chinese output examples: public_decree='粮仓告急，全族优先耕作。', strategy_summary='扩大东侧农田并防备兽人边境。', petition reason='粮食不足，请求上帝赐予应急粮食。'",
        f"Resources: {faction.resources.as_dict()}",
        f"Population: {world.total_population(faction_id)}",
        f"Soldiers: {world.total_soldiers(faction_id)}",
        f"Jobs: {world.total_jobs(faction_id)}",
        f"Current idle budget: {world.total_jobs(faction_id).get('idle', 0)} idle people; each claim/settle costs {SETTLEMENT_IDLE_COST} idle people.",
        f"Idle focus tiles: {idle_focus_tiles[:10]}",
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


def _idle_focus_tiles(
    world: WorldState,
    faction_id: str,
) -> list[dict[str, Any]]:
    tiles = []
    for tile in world.faction_tiles(faction_id):
        population = tile.population_of(faction_id)
        idle = tile.professions_of(faction_id).get("idle", 0)
        if idle <= 0:
            continue
        safe_idle = min(idle, max(0, population - 1))
        tiles.append(
            {
                "x": tile.x,
                "y": tile.y,
                "terrain": tile.terrain,
                "weather": tile.weather,
                "population": population,
                "idle": idle,
                "soldiers": tile.soldiers_of(faction_id),
                "jobs": tile.professions_of(faction_id),
                "houses": tile.houses,
                "capacity": tile.capacity(),
                "safe_settlement_groups": safe_idle // SETTLEMENT_IDLE_COST,
                "recommended_idle_uses": _recommended_idle_uses(tile.terrain),
            }
        )
    return sorted(tiles, key=lambda item: (-item["idle"], item["x"], item["y"]))


def _recommended_idle_uses(terrain: str) -> list[str]:
    if terrain == "plain":
        return ["farm", "build", "train"]
    if terrain == "forest":
        return ["gather_wood", "build", "train"]
    if terrain == "hill":
        return ["mine_stone", "build", "train"]
    return ["build", "train"]


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
        idle = source.professions_of(faction_id).get("idle", 0)
        safe_idle = min(idle, max(0, population - 1))
        sources.append(
            {
                "x": source.x,
                "y": source.y,
                "population": population,
                "idle": idle,
                "safe_settlement_groups": safe_idle // SETTLEMENT_IDLE_COST,
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
                "origin_idle": origin.professions_of(faction_id).get("idle", 0),
                "origin_can_supply_occupation": (
                    origin.professions_of(faction_id).get("idle", 0) >= SETTLEMENT_IDLE_COST
                    and origin.population_of(faction_id) > SETTLEMENT_IDLE_COST
                    and target.capacity() >= SETTLEMENT_IDLE_COST
                ),
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
            "targets are weak; 4) preserve 1 idle settler on the attack origin "
            "before planned occupation; 5) use remaining idle for food, "
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
            "border war, train or move soldiers and preserve 1 idle settler on "
            "the attack origin for occupation; 5) punish nearby threats and "
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
            "militarily ahead; 5) prepare soldiers and 1 idle settler on the "
            "attack origin before occupying enemy territory or enemy home "
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
