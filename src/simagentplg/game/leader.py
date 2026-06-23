from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
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
simulation game. You do not directly modify the world. Use only your faction's
current task facts and visible information, then end the strategic turn by
calling submit_leader_turn exactly once. Keep strategy_summary concise and
never reveal private chain-of-thought. Use only enum/action/resource/faction IDs
and coordinates accepted by the tool schema. All human-facing narrative text
must be written in Simplified Chinese: turn_intent, strategy_summary,
public_decree, diplomacy message, petition reason, and resource purpose.

Tool usage:
- submit_leader_turn is the only function that ends your strategic turn. Call it
  exactly once after your final plan is internally consistent.

Strategic priorities:
- Your final objective is to gain territory, build decisive strength, suppress
  rival civilizations, and eventually defeat the other races. Peace, alliances,
  trade, and tribute are tools for survival and future victory, not the final
  goal.
- Current battle conditions, the observation snapshot, and the task summary are
  authoritative. Historical notes never decide legality; never let old
  coordinates, old sources, or old plans override current resources, owned
  tiles, visible enemies, legal move candidates, rule feedback, recent events,
  or battlefield facts.
- Terrain, weather, population, soldiers, houses, resources, and ownership all
  matter. Farmers make food, lumberjacks make wood, miners make stone, builders
  spend wood to add houses, and houses raise capacity but do not create people
  instantly. Natural population growth creates idle people, not specialized
  workers, and can happen on safe owned populated tiles, not only at home.
- Use idle people deliberately for safe expansion, terrain-matched work, house
  building, training, or defense. When food, housing, and basic resources are
  stable, shift surplus idle people toward training, border reinforcement,
  deterrence, raids, and conquest preparation.
- The home tile is vital, but outer territory is also vital. Safe owned
  frontier tiles can grow naturally over time, become future expansion sources,
  add resource bases, and create defensive depth.
- Soldiers are the only fighters. Soldiers can move only between adjacent owned
  tiles with military action move. Raids can take resources; attacks that win
  directly occupy enemy land, and surviving attackers move into the captured
  tile as soldiers. Protect your home tile: losing it eliminates your
  civilization and transfers remaining resources to the captor.

High-risk legality rules:
- Only idle population can become farmers, lumberjacks, miners, or builders.
- To assign wood workers, use population task gather_wood. Do not use the profession name lumberjack as a population task; profession names are only for territory_orders[].profession on settle.
- farm, gather_wood, mine_stone, build, train, and defend population orders
  must target tiles already owned at turn start; do not assign jobs on land
  that will only be claimed, settled, or captured later this turn.
- For population_orders, workers means the number of idle people assigned this
  turn, not final target headcount. Build orders automatically spend wood; do
  not add a separate resource_order spend for house wood.
- Your public plan must match submitted actions. If turn_intent,
  strategy_summary, public_decree, or diplomacy text says you will farm, gather
  wood, mine stone, build houses, attack, raid, capture, expand, request
  weather, or request resources, submit the matching concrete order or avoid
  that wording.
- For every claim/settle, the source tile must be adjacent to the target. If
  settle.origin is set, origin and target must be neighbors; distant same-faction
  tiles cannot supply migrants. Choose claim/settle amount from the source's
  safe_civilian_migrations, target capacity, and strategic value. A source with
  too few movable civilians and 0 soldiers cannot send migrants; a source with
  soldiers may send its final civilian group; a specified profession must have
  enough people on the source.
- Every military order with origin must start from your owned tile that
  currently has at least 1 soldier. Never submit move, attack, raid, muster,
  defend, or retreat from a tile with 0 soldiers. War, raids, and occupation
  must obey visibility, adjacency, soldier availability, relation, and
  protection rules.
- Leaders cannot ask the god for people and cannot turn existing workers back
  into idle people.
- Petitions may ask only for god powers: resources, weather, protection, or
  unowned territory. Never petition for population or vague miracles.
- Resource petitions must use request {"resource":"food|wood|stone","amount":N}
  with a positive amount. Weather/protection/territory petitions need exact
  visible x/y coordinates.
- Do not repeatedly propose diplomacy that already matches the current
  relation. If borders touch and you have enough soldiers, consider war, raids,
  fortification, or deterrence. If a visible enemy home tile can be captured,
  it is decisive because capturing it eliminates that civilization.

Compact submit_leader_turn examples:
- Economy: {"turn_intent":"增加粮食并修建房屋","population_orders":[{"task":"farm","target":{"x":3,"y":4},"workers":2},{"task":"build","target":{"x":3,"y":4},"workers":2}],"strategy_summary":"在已拥有的(3,4)安排2人耕作、2人建房。"}
- Claim: {"turn_intent":"向东侧平原扩张","territory_orders":[{"action":"claim","target":{"x":4,"y":4},"amount":2,"priority":1}],"strategy_summary":"从相邻己方地块迁入2名可迁平民，占领(4,4)。"}
- Settle owned land: {"territory_orders":[{"action":"settle","origin":{"x":3,"y":4},"target":{"x":4,"y":4},"profession":"farmer","amount":4,"priority":1}],"strategy_summary":"从相邻的(3,4)迁4名农夫到己方平原(4,4)，扩大粮食产量。"}
- Attack: {"military_orders":[{"action":"attack","origin":{"x":3,"y":4},"target":{"x":3,"y":3},"force_ratio":0.8}],"diplomacy_orders":[{"target_faction":"orc","proposal":"war","message":"边境冲突已无法避免。"}]}
- Resource petition: {"petitions":[{"type":"resources","request":{"resource":"food","amount":80},"reason":"粮食不足会影响扩张与守军。","urgency":"high"}]}
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

LEADER_RECENT_CONTEXT_TURNS = 1
LEADER_RULE_ERROR_LIMIT = 3
LEADER_GOD_DIALOGUE_MEMORY_LIMIT = 12
LEADER_OBSERVATION_EVENT_LIMIT = 8


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
    amount: int = SETTLEMENT_IDLE_COST
    priority: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": _target_to_dict(self.target),
            "origin": _target_to_dict(self.origin),
            "profession": self.profession,
            "amount": self.amount,
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
                            "amount": {
                                "type": "integer",
                                "minimum": 1,
                                "description": f"Only for claim/settle. Number of civilians to migrate; omit to use the conservative default {SETTLEMENT_IDLE_COST}. Choose based on source safety, target capacity, and strategic value.",
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
    """Game-only terminal leader-turn submit tool."""

    def __init__(
        self,
        *,
        faction_id: str,
        world_provider: Callable[[], WorldState],
    ) -> None:
        super().__init__((SUBMIT_LEADER_TURN_TOOL,))

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
        return cls(
            faction_id=faction_id,
            agent=agent,
            chat_agent=chat_agent,
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
        faction = world.factions[self.faction_id]
        before = json.dumps(faction.leader_memory, ensure_ascii=False, sort_keys=True)
        sync_leader_narrative_memory(world, self.faction_id)
        faction.leader_context_window = faction.leader_context_window[
            -LEADER_RECENT_CONTEXT_TURNS:
        ]
        after = json.dumps(faction.leader_memory, ensure_ascii=False, sort_keys=True)
        if before == after:
            return False
        world.add_event(
            "memory",
            f"{self.faction_id} refreshed narrative memory programmatically",
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
    recent_context = _format_leader_context_window(faction.leader_context_window)
    direct_god_dialogue = _format_god_chat_history(
        world,
        faction_id,
        limit=LEADER_GOD_DIALOGUE_MEMORY_LIMIT,
    )
    recent_events = _recent_relevant_events(world, faction_id)
    known_faction_summaries = _known_faction_visible_summaries(world, faction_id)
    diplomacy_view = {
        other: faction.relation_to(other)
        for other in sorted(faction.known_factions)
        if other != faction_id
    }
    lines = [
        f"You lead faction {faction.name} ({faction_id}) at world tick {world.tick}.",
        f"Faction doctrine: {_faction_doctrine(faction_id)}",
        "Use the current task facts below, then call submit_leader_turn.",
        "Long-term objective: gain more territory, build decisive strength, and defeat rival civilizations.",
        "Decision priority: current battle conditions and this task summary are authoritative. Historical memory is not used for strategic legality; never let old coordinates, old sources, or old plans override current resources, owned tiles, visible enemies, legal candidates, rule feedback, or recent events.",
        "Important language rule: write every player-facing sentence in Simplified Chinese / 简体中文。",
        "必须使用简体中文的字段：turn_intent, strategy_summary, public_decree, petition.reason, diplomacy message, resource purpose。",
        "Keep only enum/action/resource/faction IDs in English so the rule engine can parse them.",
        "Legal values:",
        f"- population task: {', '.join(POPULATION_TASKS)}",
        "- To assign wood workers, use population task gather_wood. Do not use the profession name lumberjack as a population task; profession names are only for territory_orders[].profession on settle.",
        f"- resource action: {', '.join(RESOURCE_ACTIONS)}",
        f"- territory action: {', '.join(TERRITORY_ACTIONS)}",
        f"- military action: {', '.join(MILITARY_ACTIONS)}",
        f"- diplomacy proposal: {', '.join(DIPLOMACY_PROPOSALS)}",
        f"- petition type: {', '.join(PETITION_TYPES)}",
        "Use only visible coordinates.",
        "Use military action move to transfer soldiers only between adjacent owned tiles. Use territory action settle with optional origin/profession to migrate a civilian group between adjacent tiles.",
        "Every military_order origin must be an owned tile with current soldiers > 0. Do not issue move, attack, raid, muster, defend, or retreat from a tile that has no soldiers.",
        "Protect your home tile: if it is captured by another faction, your civilization is eliminated and all remaining resources go to the captor.",
        "The home tile is important, but outer territory is also important. Safe owned frontier tiles can grow naturally, create expansion chains, become resource bases, and give defensive depth.",
        "Do not refresh diplomacy that already matches the current relation.",
        "Alliance from neutral is only a first trust step; war is valid when border pressure, crowding, or resource competition make peace costly.",
        "Natural population growth is checked every tick on safe owned populated tiles, not only the home tile. Each eligible tile can gain 1 idle person per tick if food and capacity allow it; low food blocks growth, and drought/storm tiles do not grow. Do not petition for population or vague miracles.",
        "Petitions are only for exact god powers: resources, weather, protection, or an unowned visible territory tile.",
        "Resource petition request format must be exactly like {\"resource\":\"food\",\"amount\":50}; resource must be food, wood, or stone, and amount must be positive. Weather/protection/territory petition requests need visible x/y coordinates.",
        "If idle people exist, normally use them for safe expansion, terrain-matched work, house building, training, or defense.",
        "When food, housing, and basic resource needs are stable, shift surplus idle people toward training, border reinforcement, deterrence, raids, and conquest preparation.",
        f"Peaceful migration: claim/settle can include amount so you decide how many civilians to move; omit amount to use default {SETTLEMENT_IDLE_COST}. settle can include origin and profession. Movable civilian professions are idle, farmer, lumberjack, miner, and builder; migrated civilians keep their profession.",
        "Migration benefits: moving idle civilians can expand territory, surround and buffer the home tile, seed frontier growth, and create expansion chains. Moving farmers onto plains can raise food output; lumberjacks fit forests, miners fit hills, and builders help housing where wood is available.",
        "For claim/settle, the source tile must be directly adjacent to the target. If settle specifies origin, origin and target must be neighbors; do not use distant owned tiles as migrant sources.",
        "Before submitting claim/settle orders, check each source's movable_jobs and safe_civilian_migrations. safe_civilian_migrations is the maximum number of civilians that source can safely send this turn. A source with too few movable civilians and 0 soldiers cannot send migrants; a source with soldiers may send its final civilian group.",
        "War occupation: if an attack wins, surviving soldiers directly occupy the enemy tile. You do not need idle civilians on the attack origin. After capture, use settle to move civilians into that owned tile for development.",
        "Your public plan must match your submitted actions. If you say you will build houses, include a build population order. If you say you will attack, raid, or capture enemy territory, include the matching military order. If you ask for weather, include a weather petition.",
        "For population_orders.workers, use the number of people newly assigned this turn, not the final desired job total.",
        "Only idle people can become farmers, lumberjacks, miners, or builders. Do not assign more workers to a profession than the target tile has idle population.",
        "Job and training population orders must target tiles you already own before this turn executes. Do not farm, gather_wood, mine_stone, build, train, or defend on a tile that will only be claimed, settled, or captured later in this same plan.",
        "If your Chinese strategy_summary or turn_intent says you will farm, build houses, attack, raid, expand, or request weather/resources, include the matching concrete order; expansion wording needs claim/settle, resource requests need a valid resource petition, otherwise avoid that wording.",
        f"Each claim/settle territory order migrates order.amount adjacent non-soldier civilians; if amount is omitted, it uses default {SETTLEMENT_IDLE_COST}. settle may specify origin and profession; claim auto-selects movable civilians for unowned land.",
        "A claim/settle source tile must keep at least 1 civilian or 1 soldier after migration. A soldier-held captured tile can receive civilians later with settle.",
        "There is no dismiss-worker or assign-back-to-idle order. Population growth is the normal source of new idle people.",
        "For houses, submit a build population order only. Do not submit a separate wood spend order; the build action spends wood automatically.",
        "Only discovered factions may be used in diplomacy or war planning.",
        "Good Chinese output examples: public_decree='粮仓告急，全族优先耕作。', strategy_summary='扩大东侧农田并防备兽人边境。', petition reason='粮食不足，请求上帝赐予应急粮食。'",
        "Recent strategic turn (continuity only; do not use old coordinates, source tiles, soldier origins, idle budgets, or targets as legal now):",
        json.dumps(recent_context, ensure_ascii=False),
        "Direct god dialogue (highest narrative priority; obey it when choosing goals, wording, diplomacy, and petitions, but all submitted orders must still be legal under current task facts):",
        json.dumps(direct_god_dialogue, ensure_ascii=False),
        "Current task facts (highest priority):",
        f"Resources: {faction.resources.as_dict()}",
        f"Population: {world.total_population(faction_id)}",
        f"Soldiers: {world.total_soldiers(faction_id)}",
        f"Jobs: {world.total_jobs(faction_id)}",
        f"Civilian migration overview: {world.total_population(faction_id)} total non-soldier civilians; claim/settle can choose amount, default {SETTLEMENT_IDLE_COST} if omitted. safe_civilian_migrations shows the maximum safe amount from a source this turn.",
        f"Civilian movement focus tiles: {civilian_movement_tiles[:10]}",
        f"Houses: {world.total_houses(faction_id)}",
        f"Population capacity: {world.population_capacity(faction_id)}",
        f"Territory tiles: {len(world.faction_tiles(faction_id))}",
        f"Home tile status: {_home_tile_status(world, faction_id)}",
        f"Owned tiles: {owned_tiles}",
        f"Legal expansion candidates: {expansion_candidates[:8]}",
        f"Expansion recommendation: {_expansion_recommendation(expansion_candidates, dangerous_weather, border_targets)}",
        f"Border enemy targets for legal war/raid planning: {border_targets[:8]}",
        f"Dangerous weather on your land: {dangerous_weather[:8]}",
        f"Visible tile count: {len(world.visible_tiles(faction_id))}",
        f"Recent relevant events: {recent_events}",
        f"Known faction visible summaries: {known_faction_summaries}",
        f"Previous strategic result: {previous_execution}",
        f"Known factions: {sorted(faction.known_factions)}",
        f"Diplomacy: {diplomacy_view}",
        "Pre-submit legality checklist: use current legal candidates, not old coordinates. For each claim/settle, target must be adjacent, passable, non-enemy for peaceful expansion, amount must fit target capacity and the source's safe_civilian_migrations, and the source must remain held by at least 1 civilian or soldier. If recommended_orders are listed, prefer submitting those territory orders unless weather, home defense, or decisive war is more urgent. For each military order, origin must currently have soldiers and target must be adjacent. Total idle assignments plus migrations must not exceed current available idle/civilian budget. Public wording must match submitted orders; peaceful expansion should avoid war/attack/raid wording.",
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


def _known_faction_visible_summaries(
    world: WorldState,
    faction_id: str,
) -> list[dict[str, Any]]:
    summaries = []
    for other_id in sorted(world.factions[faction_id].known_factions):
        if other_id == faction_id or other_id not in world.factions:
            continue
        other = world.factions[other_id]
        visible_owned = [
            tile
            for tile in world.faction_tiles(other_id)
            if world.is_visible(faction_id, tile.x, tile.y)
        ]
        summaries.append(
            {
                "faction_id": other_id,
                "name": other.name,
                "relation": world.factions[faction_id].relation_to(other_id),
                "visible_home_tile": (
                    _target_to_dict(other.home_tile)
                    if other.home_tile is not None
                    and world.is_visible(faction_id, *other.home_tile)
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
                    for event in world.events[-LEADER_OBSERVATION_EVENT_LIMIT:]
                    if event.faction_id == other_id
                ],
            }
        )
    return summaries


def _recent_relevant_events(world: WorldState, faction_id: str) -> list[dict[str, Any]]:
    return [
        event.as_dict()
        for event in world.events[-LEADER_OBSERVATION_EVENT_LIMIT:]
        if event.faction_id in {None, faction_id}
    ]


def _format_leader_context_window(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted = []
    for entry in entries[-LEADER_RECENT_CONTEXT_TURNS:]:
        feedback_categories = _feedback_categories(
            str(item)
            for item in _list(entry.get("feedback_attempts"))
        )
        formatted.append(
            {
                "tick": entry.get("tick"),
                "accepted": entry.get("accepted"),
                "strategy_summary": entry.get("strategy_summary"),
                "feedback_categories": feedback_categories,
                "after_execution": entry.get("after_execution"),
                "events": entry.get("events", [])[-8:],
            }
        )
    return formatted


def _feedback_categories(feedback_attempts: Iterable[str]) -> list[str]:
    categories: set[str] = set()
    for feedback in feedback_attempts:
        text = feedback.lower()
        if not text:
            continue
        if "target must be adjacent" in text or "目标必须与" in text:
            categories.add("military_or_order_adjacency")
        if "origin has no soldiers" in text or "origin 必须有士兵" in text or "源地块必须有士兵" in text:
            categories.add("military_origin_soldiers")
        if "idle population budget" in text or "闲置人口预算" in text or "overcommitted" in text:
            categories.add("idle_budget")
        if "not passable" in text or "不可通行" in text:
            categories.add("impassable_target")
        if "source tile held" in text or "without civilians or soldiers" in text or "至少保留" in text:
            categories.add("settlement_source_hold")
        if "origin must be adjacent" in text or "源必须与目标相邻" in text:
            categories.add("settlement_origin_adjacency")
        if "enemy-owned" in text or "敌方已占" in text or "target must be enemy-owned" in text:
            categories.add("enemy_claim_or_attack_target")
        if "plan mentions" in text or "has no" in text or "计划中提到" in text:
            categories.add("plan_order_mismatch")
    return sorted(categories)


def sync_leader_narrative_memory(
    world: WorldState,
    faction_id: str,
) -> None:
    faction = world.factions[faction_id]
    rule_errors = [
        item
        for item in _list(faction.leader_memory.get("rule_errors"))
        if isinstance(item, Mapping)
    ][-LEADER_RULE_ERROR_LIMIT:]
    faction.leader_memory.clear()
    faction.leader_memory.update(
        {
            "god_dialogue": _format_god_chat_history(
                world,
                faction_id,
                limit=LEADER_GOD_DIALOGUE_MEMORY_LIMIT,
            ),
            "rule_errors": rule_errors,
        }
    )


def record_leader_rule_error(
    memory: dict[str, Any],
    error: str,
    *,
    tick: int,
) -> None:
    text = str(error).strip()
    if not text:
        return
    rule_errors = [
        dict(item)
        for item in _list(memory.get("rule_errors"))
        if isinstance(item, Mapping)
    ]
    god_dialogue = [
        item
        for item in _list(memory.get("god_dialogue"))
        if isinstance(item, Mapping)
    ][-LEADER_GOD_DIALOGUE_MEMORY_LIMIT:]
    categories = _feedback_categories([text])
    existing = next(
        (
            item
            for item in reversed(rule_errors)
            if item.get("tick") == tick
        ),
        None,
    )
    if existing is None:
        rule_errors.append(
            {
                "tick": tick,
                "error": text,
                "categories": categories,
                "count": 1,
            }
        )
    else:
        existing["error"] = text
        existing["categories"] = sorted(
            set(_list(existing.get("categories"))) | set(categories)
        )
        existing["count"] = int(existing.get("count", 1) or 1) + 1
    memory.clear()
    memory.update(
        {
            "god_dialogue": [
                dict(item)
                for item in god_dialogue
            ],
            "rule_errors": rule_errors[-LEADER_RULE_ERROR_LIMIT:],
        }
    )


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
            safe_migration_amount = sum(
                source["safe_civilian_migrations"] for source in sources
            )
            if safe_migration_amount <= 0:
                continue
            candidates[(neighbor.x, neighbor.y)] = {
                "x": neighbor.x,
                "y": neighbor.y,
                "terrain": neighbor.terrain,
                "weather": neighbor.weather,
                "max_safe_migration_amount_from_sources": safe_migration_amount,
                "settlement_sources": sources,
            }
    return [candidates[key] for key in sorted(candidates)]


def _expansion_recommendation(
    expansion_candidates: list[dict[str, Any]],
    dangerous_weather: list[dict[str, Any]],
    border_targets: list[dict[str, Any]],
) -> dict[str, Any]:
    if not expansion_candidates:
        return {
            "priority": "prepare",
            "reason": "No safe visible unowned adjacent tile has a movable civilian source this turn. Develop owned land, protect the home tile, and let safe populated frontier tiles grow naturally.",
        }
    claim_order = _claim_recommendation(expansion_candidates[0])
    if dangerous_weather:
        return {
            "priority": "conditional",
            "reason": "Handle dangerous weather first if it threatens the home tile or most population; otherwise still claim one safe adjacent tile.",
            "recommended_order": claim_order,
        }
    if border_targets:
        return {
            "priority": "balanced",
            "reason": "Military pressure exists, but safe peaceful expansion is still useful; prefer claiming one safe adjacent tile unless an immediate attack is decisive.",
            "recommended_order": claim_order,
        }
    return {
        "priority": "claim_now",
        "reason": "Safe legal expansion exists. Prefer at least one claim this strategic turn instead of only internal jobs.",
        "recommended_order": claim_order,
    }


def _claim_recommendation(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action": "claim",
        "target": {
            "x": candidate.get("x"),
            "y": candidate.get("y"),
        },
        "amount": max(
            1,
            min(
                SETTLEMENT_IDLE_COST,
                int(candidate.get("max_safe_migration_amount_from_sources", SETTLEMENT_IDLE_COST)),
            ),
        ),
        "priority": 1,
    }


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
        safe_migrations = _safe_civilian_migrations(tile, faction_id)
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
                "safe_civilian_migrations": safe_migrations,
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
        safe_migrations = _safe_civilian_migrations(source, faction_id)
        sources.append(
            {
                "x": source.x,
                "y": source.y,
                "population": population,
                "soldiers": source.soldiers_of(faction_id),
                "movable_jobs": movable_jobs,
                "safe_civilian_migrations": safe_migrations,
                "can_settle_this_turn": safe_migrations > 0,
            }
        )
    return sorted(sources, key=lambda item: (item["x"], item["y"]))


def _safe_civilian_migrations(tile, faction_id: str) -> int:
    population = tile.population_of(faction_id)
    movable_total = sum(_movable_civilian_jobs(tile, faction_id).values())
    held_after_limit = (
        population
        if tile.soldiers_of(faction_id) > 0
        else max(0, population - 1)
    )
    return min(movable_total, held_after_limit)


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
        amount=_int(item.get("amount"), SETTLEMENT_IDLE_COST),
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
