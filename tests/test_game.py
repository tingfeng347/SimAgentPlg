import json
import asyncio
import time
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from simagentplg import BaseAgent, ModelConfig
from simagentplg.game import (
    GameEngine,
    LLMLeaderController,
    LeaderDecision,
    LeaderToolHandler,
    NPCExecutor,
    RuleEngine,
    create_default_world,
    render_inbox,
    render_map,
    render_status,
)
from simagentplg.game.leader import (
    LEADER_SYSTEM_PROMPT,
    SUBMIT_LEADER_TURN_TOOL,
    _build_leader_task,
)

TEST_CONFIG = ModelConfig(
    model="test-model",
    api_key="test-key",
    base_url="https://example.invalid",
)


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction
    type: str = "function"


class FakeMessage:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[FakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": self.content,
            "tool_calls": self.tool_calls,
        }


class FakeCompletions:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self.responses = list(responses)

    async def create(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=self.responses.pop(0))]
        )


class FakeClient:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


class ScriptedLeader:
    def __init__(
        self,
        faction_id: str,
        decisions: list[LeaderDecision],
    ) -> None:
        self.faction_id = faction_id
        self.decisions = list(decisions)
        self.feedback: list[str | None] = []

    async def decide(
        self,
        world,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision:
        self.feedback.append(feedback)
        if len(self.decisions) > 1:
            return self.decisions.pop(0)
        return self.decisions[0]


class DelayedLeader:
    def __init__(self, faction_id: str, delay: float = 0.05) -> None:
        self.faction_id = faction_id
        self.delay = delay
        self.calls = 0

    async def decide(
        self,
        world,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return hold()


def hold() -> LeaderDecision:
    return LeaderDecision(turn_intent="守住当前领地")


class GameTests(unittest.IsolatedAsyncioTestCase):
    def test_default_world_is_seeded_and_renderable(self) -> None:
        first = create_default_world(seed=123)
        second = create_default_world(seed=123)
        third = create_default_world(seed=124)

        self.assertEqual(
            [tile.terrain for tile in first.tiles],
            [tile.terrain for tile in second.tiles],
        )
        self.assertEqual(
            _start_positions(first),
            _start_positions(second),
        )
        self.assertNotEqual(
            _start_positions(first),
            _start_positions(third),
        )
        for faction_id in first.factions:
            self.assertEqual(len(first.faction_tiles(faction_id)), 1)
            self.assertEqual(first.total_population(faction_id), 40)
            self.assertEqual(first.total_soldiers(faction_id), 20)
            jobs = first.total_jobs(faction_id)
            self.assertEqual(jobs["farmer"], 30)
            self.assertEqual(jobs["lumberjack"], 0)
            self.assertEqual(jobs["miner"], 0)
            self.assertEqual(jobs["builder"], 0)
            self.assertEqual(jobs["idle"], 10)
        self.assertIn("Tick 0", render_map(first))
        self.assertIn("Human", render_status(first))

    def test_god_commands_change_world_and_inbox(self) -> None:
        world = create_default_world(seed=1)
        engine = GameEngine(world)
        claim_target = _adjacent_empty_tile(world, "human")

        engine.god.give_resource("human", "food", 25)
        engine.god.set_weather(0, 0, "storm")
        engine.god.claim_tile("human", *claim_target)
        world.add_petition(
            faction_id="human",
            kind="resources",
            request={"resource": "wood", "amount": 5},
            reason="build a shrine",
        )

        self.assertEqual(world.factions["human"].resources.food, 145)
        self.assertEqual(world.tile_at(0, 0).weather, "storm")
        self.assertEqual(world.tile_at(*claim_target).owner, "human")
        self.assertIn("build a shrine", render_inbox(world))

        engine.god.answer_petition(1, True)
        self.assertEqual(world.petitions[0].status, "approved")
        self.assertEqual(world.factions["human"].resources.wood, 85)

    def test_god_claim_must_border_existing_territory(self) -> None:
        world = create_default_world(seed=1)
        engine = GameEngine(world)
        target = _non_adjacent_empty_tile(world, "human")

        with self.assertRaisesRegex(ValueError, "must border owned territory"):
            engine.god.claim_tile("human", *target)

    def test_world_merges_duplicate_pending_petitions(self) -> None:
        world = create_default_world(seed=11)

        first = world.add_petition(
            faction_id="human",
            kind="resources",
            request={"resource": "food", "amount": 30},
            reason="low granary",
            urgency="medium",
        )
        second = world.add_petition(
            faction_id="human",
            kind="resources",
            request={"resource": "food", "amount": 80},
            reason="famine worsened",
            urgency="high",
        )

        self.assertIs(first, second)
        self.assertEqual(len(world.petitions), 1)
        self.assertEqual(world.petitions[0].request["amount"], 80)
        self.assertEqual(world.petitions[0].urgency, "high")

    def test_rule_engine_rejects_illegal_leader_orders(self) -> None:
        world = create_default_world(seed=2)
        rules = RuleEngine()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "take impossible land",
                "resource_orders": [
                    {"resource": "stone", "action": "spend", "amount": 9999}
                ],
                "territory_orders": [
                    {"action": "claim", "target": {"x": 0, "y": 0}}
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(
            any("not enough stone" in error for error in check.errors)
        )
        self.assertTrue(
            any("not visible" in error for error in check.errors)
        )

    def test_npc_executes_valid_population_and_territory_orders(self) -> None:
        world = create_default_world(seed=3)
        rules = RuleEngine()
        npc = NPCExecutor()
        target = _adjacent_empty_tile(world, "human")
        worker_tile = max(
            world.faction_tiles("human"),
            key=lambda tile: tile.population_of("human"),
        )
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "耕作并定居",
                "population_orders": [
                    {
                        "task": "farm",
                        "target": {"x": worker_tile.x, "y": worker_tile.y},
                        "workers": 2,
                    }
                ],
                "territory_orders": [
                    {"action": "claim", "target": {"x": target[0], "y": target[1]}}
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertGreater(worker_tile.professions_of("human")["farmer"], 30)
        self.assertEqual(world.tile_at(*target).owner, "human")
        self.assertEqual(world.tile_at(*target).population_of("human"), 3)

    def test_idle_budget_allows_one_claim_with_jobs(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = None
        target.population.clear()
        target.professions.clear()
        target.soldiers.clear()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "占领新土地并纳入疆域，同时安排耕作和建房",
                "population_orders": [
                    {
                        "task": "farm",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 3,
                    },
                    {
                        "task": "build",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 3,
                    },
                ],
                "territory_orders": [
                    {"action": "claim", "target": {"x": target.x, "y": target.y}}
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "human")
        self.assertEqual(target.population_of("human"), 3)
        jobs = world.total_jobs("human")
        self.assertEqual(jobs["farmer"], 33)
        self.assertEqual(jobs["builder"], 3)

    def test_settlement_moves_idle_without_reducing_farmers(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        targets = world.neighbors(origin.x, origin.y)[:2]
        self.assertEqual(len(targets), 2)
        for tile in targets:
            tile.terrain = "plain"
            tile.owner = None
            tile.population.clear()
            tile.professions.clear()
            tile.soldiers.clear()
        before_jobs = world.total_jobs("human")
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "向两块相邻土地扩张",
                "territory_orders": [
                    {"action": "claim", "target": {"x": tile.x, "y": tile.y}}
                    for tile in targets
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        after_jobs = world.total_jobs("human")
        self.assertEqual(after_jobs["farmer"], before_jobs["farmer"])
        self.assertEqual(after_jobs["idle"], before_jobs["idle"])
        self.assertEqual(targets[0].population_of("human"), 3)
        self.assertEqual(targets[1].population_of("human"), 3)
        self.assertEqual(len(world.faction_tiles("human")), 3)

    def test_idle_budget_rejects_jobs_and_settlement_overcommit(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        origin = world.faction_tiles("human")[0]
        target = _adjacent_empty_tile(world, "human")
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "把全部闲置人口转为农民并扩张",
                "population_orders": [
                    {
                        "task": "farm",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 10,
                    }
                ],
                "territory_orders": [
                    {"action": "claim", "target": {"x": target[0], "y": target[1]}}
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("idle population" in error for error in check.errors))
        self.assertTrue(any("current idle=10" in error for error in check.errors))

    def test_idle_budget_rejects_common_turn_five_overcommit(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        origin = world.faction_tiles("human")[0]
        targets = world.neighbors(origin.x, origin.y)[:2]
        for tile in targets:
            tile.terrain = "plain"
            tile.owner = None
            tile.population.clear()
            tile.professions.clear()
            tile.soldiers.clear()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "扩张两块领土，增加农民，修建房屋并训练新兵",
                "population_orders": [
                    {
                        "task": "farm",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 3,
                    },
                    {
                        "task": "build",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 3,
                    },
                    {
                        "task": "train",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 5,
                    },
                ],
                "territory_orders": [
                    {"action": "claim", "target": {"x": tile.x, "y": tile.y}}
                    for tile in targets
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("claim/settle need=6" in error for error in check.errors))
        self.assertTrue(any("jobs/training need=7" in error for error in check.errors))

    def test_peaceful_occupation_text_with_claim_is_not_military_capture(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = None
        target.population.clear()
        target.professions.clear()
        target.soldiers.clear()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "占领新土地并纳入疆域",
                "territory_orders": [
                    {"action": "claim", "target": {"x": target.x, "y": target.y}}
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertTrue(check.accepted, check.errors)

    def test_military_capture_text_without_attack_is_rejected(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "攻占敌方领地并夺取对方资源",
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("capturing enemy territory" in error for error in check.errors))

    def test_npc_grows_population_from_food_and_capacity(self) -> None:
        world = create_default_world(seed=31)
        npc = NPCExecutor()
        world.tick = 5
        before = world.total_population("human")

        npc.apply_passive_tick(world)

        self.assertGreater(world.total_population("human"), before)
        self.assertTrue(any(event.kind == "population" for event in world.events))

    def test_weather_damages_population_on_owned_tiles(self) -> None:
        world = create_default_world(seed=32)
        npc = NPCExecutor()
        tile = world.faction_tiles("human")[0]
        tile.weather = "storm"
        world.tick = 1
        before = tile.population_of("human")

        npc.apply_passive_tick(world)

        self.assertLess(tile.population_of("human"), before)
        self.assertTrue(any(event.kind == "weather" for event in world.events))

    def test_military_attack_can_capture_enemy_territory(self) -> None:
        world = create_default_world(seed=33)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "elf"
        target.population = {"elf": 10}
        target.soldiers = {"elf": 1}
        origin.soldiers["human"] = 30
        world.factions["human"].known_factions.add("elf")
        world.factions["elf"].known_factions.add("human")
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "向精灵开战并占领边境",
                "diplomacy_orders": [
                    {"target_faction": "elf", "proposal": "war"}
                ],
                "military_orders": [
                    {
                        "action": "attack",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target.x, "y": target.y},
                        "force_ratio": 0.8,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "human")
        self.assertGreater(target.population_of("human"), 0)
        self.assertEqual(world.factions["human"].relation_to("elf"), "war")
        self.assertTrue(any(event.kind == "battle" for event in world.events))

    def test_successful_attack_loots_resource_share(self) -> None:
        world = create_default_world(seed=37)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "elf"
        target.set_population("elf", 10)
        target.soldiers = {"elf": 1}
        origin.soldiers["human"] = 30
        world.factions["human"].known_factions.add("elf")
        world.factions["elf"].known_factions.add("human")
        world.factions["elf"].resources.food = 60
        world.factions["elf"].resources.wood = 30
        world.factions["elf"].resources.stone = 12
        before_human = world.factions["human"].resources.as_dict()
        expected_loot = {
            resource: world.factions["elf"].resources.amount(resource)
            // len(world.faction_tiles("elf"))
            for resource in ("food", "wood", "stone")
        }

        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "占领精灵边境并夺取资源",
                "diplomacy_orders": [
                    {"target_faction": "elf", "proposal": "war"}
                ],
                "military_orders": [
                    {
                        "action": "attack",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target.x, "y": target.y},
                        "force_ratio": 0.8,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "human")
        self.assertEqual(world.factions["human"].resources.food, before_human["food"] + expected_loot["food"])
        self.assertEqual(world.factions["human"].resources.wood, before_human["wood"] + expected_loot["wood"])
        self.assertEqual(world.factions["human"].resources.stone, before_human["stone"] + expected_loot["stone"])

    def test_attack_without_movable_population_only_raids(self) -> None:
        world = create_default_world(seed=38)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "elf"
        target.set_population("elf", 10)
        target.soldiers = {"elf": 1}
        origin.set_population("human", 1)
        origin.soldiers["human"] = 30
        world.factions["human"].known_factions.add("elf")
        world.factions["elf"].known_factions.add("human")

        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "占领精灵边境",
                "diplomacy_orders": [
                    {"target_faction": "elf", "proposal": "war"}
                ],
                "military_orders": [
                    {
                        "action": "attack",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target.x, "y": target.y},
                        "force_ratio": 0.8,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "elf")
        self.assertTrue(
            any("could not occupy" in event.message for event in world.events)
        )

    def test_raid_loots_without_changing_owner(self) -> None:
        world = create_default_world(seed=39)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "elf"
        target.set_population("elf", 10)
        target.soldiers = {"elf": 1}
        origin.soldiers["human"] = 30
        world.factions["human"].known_factions.add("elf")
        world.factions["elf"].known_factions.add("human")

        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "突袭精灵边境",
                "diplomacy_orders": [
                    {"target_faction": "elf", "proposal": "war"}
                ],
                "military_orders": [
                    {
                        "action": "raid",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target.x, "y": target.y},
                        "force_ratio": 0.8,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "elf")
        self.assertTrue(any("raided" in event.message for event in world.events))

    def test_builders_spend_wood_to_increase_capacity(self) -> None:
        world = create_default_world(seed=40)
        rules = RuleEngine()
        npc = NPCExecutor()
        tile = world.faction_tiles("human")[0]
        before_houses = tile.houses
        before_capacity = tile.capacity()
        before_wood = world.factions["human"].resources.wood

        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "建房提升人口容量",
                "population_orders": [
                    {
                        "task": "build",
                        "target": {"x": tile.x, "y": tile.y},
                        "workers": 10,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertGreater(tile.houses, before_houses)
        self.assertGreater(tile.capacity(), before_capacity)
        self.assertLess(world.factions["human"].resources.wood, before_wood)
        after = world.factions["human"].last_plan_snapshot["after_execution"]
        self.assertEqual(after["houses"], world.total_houses("human"))
        self.assertEqual(after["resources"], world.factions["human"].resources.as_dict())

    def test_profession_orders_only_convert_idle_population(self) -> None:
        world = create_default_world(seed=46)
        npc = NPCExecutor()
        tile = world.faction_tiles("human")[0]
        tile.professions["human"] = {
            "farmer": 50,
            "lumberjack": 20,
            "miner": 10,
            "builder": 0,
            "idle": 5,
        }
        tile.population["human"] = 85
        world.factions["human"].active_orders = {
            "population_orders": [
                {
                    "task": "mine_stone",
                    "target": {"x": tile.x, "y": tile.y},
                    "workers": 20,
                    "priority": 1,
                }
            ],
            "resource_orders": [],
            "territory_orders": [],
            "military_orders": [],
        }

        npc.execute_active_orders(world, "human")

        jobs = tile.professions_of("human")
        self.assertEqual(jobs["idle"], 0)
        self.assertEqual(jobs["miner"], 15)
        self.assertEqual(jobs["farmer"], 50)
        self.assertEqual(jobs["lumberjack"], 20)

    def test_profession_order_rejects_more_workers_than_idle(self) -> None:
        world = create_default_world(seed=47)
        rules = RuleEngine()
        tile = world.faction_tiles("human")[0]
        tile.professions["human"] = {
            "farmer": 50,
            "lumberjack": 20,
            "miner": 10,
            "builder": 0,
            "idle": 3,
        }
        tile.population["human"] = 83
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "安排农民耕作",
                "population_orders": [
                    {
                        "task": "farm",
                        "target": {"x": tile.x, "y": tile.y},
                        "workers": 4,
                    }
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("only 3 idle population" in error for error in check.errors))

    def test_idle_population_order_is_not_supported(self) -> None:
        world = create_default_world(seed=49)
        rules = RuleEngine()
        tile = world.faction_tiles("human")[0]
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "尝试解除岗位",
                "population_orders": [
                    {
                        "task": "idle",
                        "target": {"x": tile.x, "y": tile.y},
                        "workers": 5,
                    }
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)
        schema = SUBMIT_LEADER_TURN_TOOL["function"]["parameters"]["properties"]
        task_enum = schema["population_orders"]["items"]["properties"]["task"]["enum"]

        self.assertFalse(check.accepted)
        self.assertTrue(any("unknown task 'idle'" in error for error in check.errors))
        self.assertNotIn("idle", task_enum)

    def test_build_order_rejects_extra_wood_spend(self) -> None:
        world = create_default_world(seed=45)
        rules = RuleEngine()
        tile = world.faction_tiles("human")[0]
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "建房提升人口容量",
                "population_orders": [
                    {
                        "task": "build",
                        "target": {"x": tile.x, "y": tile.y},
                        "workers": 10,
                    }
                ],
                "resource_orders": [
                    {
                        "resource": "wood",
                        "action": "spend",
                        "amount": 60,
                        "purpose": "建房",
                    }
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("spend wood automatically" in error for error in check.errors))

    def test_default_factions_start_unknown_until_visible(self) -> None:
        world = create_default_world(seed=41)

        self.assertEqual(world.factions["human"].known_factions, {"human"})
        target = world.neighbors(world.faction_tiles("human")[0].x, world.faction_tiles("human")[0].y)[0]
        target.owner = "elf"
        target.set_population("elf", 10)
        discoveries = world.discover_factions()

        self.assertIn(("human", "elf"), discoveries)
        self.assertIn("elf", world.factions["human"].known_factions)

    def test_unknown_faction_diplomacy_is_rejected(self) -> None:
        world = create_default_world(seed=42)
        rules = RuleEngine()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "尝试外交",
                "diplomacy_orders": [
                    {"target_faction": "elf", "proposal": "trade"}
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("not been discovered" in error for error in check.errors))

    def test_weather_duration_counts_down(self) -> None:
        world = create_default_world(seed=43)
        engine = GameEngine(world)
        tile = world.faction_tiles("human")[0]

        engine.god.set_weather(tile.x, tile.y, "rain", duration=2)
        self.assertEqual(tile.weather, "rain")
        self.assertEqual(tile.weather_duration, 2)

        asyncio.run(engine.tick(2))

        self.assertEqual(tile.weather, "clear")
        self.assertEqual(tile.weather_duration, 0)

    def test_plan_mentions_building_but_has_no_build_order_is_rejected(self) -> None:
        world = create_default_world(seed=44)
        rules = RuleEngine()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "建房提升人口容量",
                "population_orders": [],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("building houses" in error for error in check.errors))

    def test_petitions_are_limited_to_god_owned_powers(self) -> None:
        world = create_default_world(seed=34)
        rules = RuleEngine()
        miracle = LeaderDecision.from_mapping(
            {
                "turn_intent": "ask for people",
                "petitions": [
                    {"type": "miracle", "reason": "give us more citizens"}
                ],
            }
        )

        check = rules.validate_decision(world, "human", miracle)

        self.assertFalse(check.accepted)
        self.assertTrue(any("unknown type" in error for error in check.errors))

    def test_alliance_from_neutral_requires_trust_step(self) -> None:
        world = create_default_world(seed=35)
        rules = RuleEngine()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "寻求同盟",
                "diplomacy_orders": [
                    {"target_faction": "elf", "proposal": "alliance"}
                ],
            }
        )
        world.factions["human"].known_factions.add("elf")
        world.factions["elf"].known_factions.add("human")

        check = rules.apply_decision(world, "human", decision)

        self.assertTrue(check.accepted, check.errors)
        self.assertEqual(world.factions["human"].relation_to("elf"), "non_aggression")

    def test_leader_prompts_require_chinese_narrative_output(self) -> None:
        world = create_default_world(seed=36)
        task = _build_leader_task(world, "human", None)
        schema = SUBMIT_LEADER_TURN_TOOL["function"]["parameters"]["properties"]

        self.assertIn("Simplified Chinese", LEADER_SYSTEM_PROMPT)
        self.assertIn("简体中文", task)
        self.assertIn("turn_intent", task)
        self.assertIn("public_decree", task)
        self.assertIn("petition.reason", task)
        self.assertIn("必须使用简体中文", schema["turn_intent"]["description"])
        self.assertIn("必须使用简体中文", schema["public_decree"]["description"])
        self.assertIn("必须使用简体中文", schema["strategy_summary"]["description"])
        self.assertIn("Basic world:", LEADER_SYSTEM_PROMPT)
        self.assertIn("Restrict:", LEADER_SYSTEM_PROMPT)
        self.assertIn("Tool usage:", LEADER_SYSTEM_PROMPT)
        self.assertIn('{"mode": "realm"}', LEADER_SYSTEM_PROMPT)
        self.assertIn('{"mode": "faction", "faction_id": "elf"}', LEADER_SYSTEM_PROMPT)
        self.assertIn("submit_leader_turn is the only function that ends", LEADER_SYSTEM_PROMPT)
        self.assertIn("Leaders cannot ask the god for people", LEADER_SYSTEM_PROMPT)
        self.assertIn("There is no dismiss-worker or assign-back-to-idle order", task)
        self.assertNotIn("assign them back to idle", LEADER_SYSTEM_PROMPT)

    def test_leader_task_includes_previous_execution_snapshot(self) -> None:
        world = create_default_world(seed=48)
        world.factions["human"].last_plan_snapshot = {
            "tick": 5,
            "strategy_summary": "修建房屋",
            "resources": {"food": 100, "wood": 80, "stone": 40},
            "jobs": {"farmer": 50, "lumberjack": 20, "miner": 10, "builder": 0, "idle": 20},
            "houses": 16,
            "population_capacity": 180,
            "after_execution": {
                "tick": 5,
                "resources": {"food": 95, "wood": 60, "stone": 40},
                "jobs": {"farmer": 50, "lumberjack": 20, "miner": 10, "builder": 10, "idle": 10},
                "houses": 18,
                "population_capacity": 190,
            },
        }

        task = _build_leader_task(world, "human", None)

        self.assertIn("Previous strategic turn actual result", task)
        self.assertIn("actual_after_execution", task)
        self.assertIn("修建房屋", task)

    async def test_llm_leader_controller_submits_game_only_tool(self) -> None:
        world = create_default_world(seed=4)
        handler = LeaderToolHandler(
            faction_id="human",
            world_provider=lambda: world,
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="leader-human",
            handlers=[handler],
            enable_tools=True,
            client=FakeClient(
                [
                    FakeMessage(
                        tool_calls=[
                            FakeToolCall(
                                id="call-1",
                                function=FakeFunction(
                                    "submit_leader_turn",
                                    json.dumps(
                                        {
                                            "turn_intent": "hold",
                                            "strategy_summary": "wait",
                                        }
                                    ),
                                ),
                            )
                        ]
                    )
                ]
            ),
        )
        controller = LLMLeaderController(faction_id="human", agent=agent)

        decision = await controller.decide(world)

        self.assertEqual(decision.turn_intent, "hold")
        self.assertEqual(
            [tool["function"]["name"] for tool in agent.tools],
            [
                "inspect",
                "submit_leader_turn",
            ],
        )

    async def test_leader_inspect_tool_combines_observation_modes(self) -> None:
        world = create_default_world(seed=4)
        handler = LeaderToolHandler(
            faction_id="human",
            world_provider=lambda: world,
        )
        human_tile = world.faction_tiles("human")[0]
        elf_tile = world.neighbors(human_tile.x, human_tile.y)[0]
        elf_tile.terrain = "plain"
        elf_tile.owner = "elf"
        elf_tile.set_population("elf", 8)
        world.factions["human"].known_factions.add("elf")

        realm = await handler.do_inspect({"mode": "realm"})
        tiles = await handler.do_inspect(
            {
                "mode": "tiles",
                "tiles": [{"x": human_tile.x, "y": human_tile.y}],
            }
        )
        faction = await handler.do_inspect(
            {"mode": "faction", "faction_id": "elf"}
        )

        self.assertEqual(realm.data["population"], 40)
        self.assertEqual(realm.data["jobs"]["farmer"], 30)
        self.assertEqual(tiles.data["tiles"][0]["owner"], "human")
        self.assertEqual(faction.data["faction_id"], "elf")

    async def test_engine_retries_illegal_llm_decision_then_accepts(self) -> None:
        world = create_default_world(seed=5)
        bad = LeaderDecision.from_mapping(
            {
                "turn_intent": "break rules",
                "territory_orders": [
                    {"action": "claim", "target": {"x": 0, "y": 0}}
                ],
            }
        )
        good = hold()
        human = ScriptedLeader("human", [bad, good])
        engine = GameEngine(
            world,
            strategy_interval=1,
            leaders={
                "human": human,
                "elf": ScriptedLeader("elf", [hold()]),
                "orc": ScriptedLeader("orc", [hold()]),
            },
        )

        await engine.tick()

        self.assertFalse(world.paused)
        self.assertIsNone(human.feedback[0])
        self.assertIn("not visible", human.feedback[1] or "")
        self.assertTrue(
            any(event.kind == "rule_reject" for event in world.events)
        )

    async def test_engine_tick_logging_includes_events_and_faction_status(self) -> None:
        world = create_default_world(seed=50)
        engine = GameEngine(
            world,
            strategy_interval=999,
            leaders={},
            log_ticks=True,
        )

        with self.assertLogs("game-engine", level="INFO") as captured:
            await engine.tick()

        output = "\n".join(captured.output)
        self.assertIn("第 1 刻事件", output)
        self.assertIn("第 1 刻阵营 | human", output)
        self.assertIn("人口=", output)
        self.assertIn("资源=", output)

    async def test_engine_retries_idle_budget_feedback_then_accepts(self) -> None:
        world = create_default_world(seed=30)
        origin = world.faction_tiles("human")[0]
        targets = world.neighbors(origin.x, origin.y)[:2]
        for tile in targets:
            tile.terrain = "plain"
            tile.owner = None
            tile.population.clear()
            tile.professions.clear()
            tile.soldiers.clear()
        bad = LeaderDecision.from_mapping(
            {
                "turn_intent": "扩张两块领土，增加农民，修建房屋并训练新兵",
                "population_orders": [
                    {
                        "task": "farm",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 3,
                    },
                    {
                        "task": "build",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 3,
                    },
                    {
                        "task": "train",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 5,
                    },
                ],
                "territory_orders": [
                    {"action": "claim", "target": {"x": tile.x, "y": tile.y}}
                    for tile in targets
                ],
            }
        )
        good = LeaderDecision.from_mapping(
            {
                "turn_intent": "占领新土地并安排耕作建房",
                "population_orders": [
                    {
                        "task": "farm",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 3,
                    },
                    {
                        "task": "build",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 3,
                    },
                ],
                "territory_orders": [
                    {"action": "claim", "target": {"x": targets[0].x, "y": targets[0].y}}
                ],
            }
        )
        human = ScriptedLeader("human", [bad, good])
        engine = GameEngine(
            world,
            strategy_interval=1,
            leaders={
                "human": human,
                "elf": ScriptedLeader("elf", [hold()]),
                "orc": ScriptedLeader("orc", [hold()]),
            },
        )

        await engine.tick()

        self.assertFalse(world.paused)
        self.assertIn("current idle=10", human.feedback[1] or "")
        self.assertEqual(targets[0].owner, "human")

    async def test_engine_asks_leaders_in_parallel(self) -> None:
        world = create_default_world(seed=8)
        leaders = {
            faction_id: DelayedLeader(faction_id, delay=0.08)
            for faction_id in world.factions
        }
        engine = GameEngine(
            world,
            strategy_interval=1,
            leaders=leaders,
        )

        started = time.perf_counter()
        await engine.tick()
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.18)
        self.assertFalse(world.paused)
        for leader in leaders.values():
            self.assertEqual(leader.calls, 1)

    async def test_engine_pauses_after_repeated_illegal_llm_decisions(self) -> None:
        world = create_default_world(seed=6)
        bad = LeaderDecision.from_mapping(
            {
                "turn_intent": "break rules forever",
                "resource_orders": [
                    {"resource": "food", "action": "spend", "amount": 9999}
                ],
            }
        )
        engine = GameEngine(
            world,
            strategy_interval=1,
            retry_limit=1,
            leaders={
                "human": ScriptedLeader("human", [bad]),
                "elf": ScriptedLeader("elf", [hold()]),
                "orc": ScriptedLeader("orc", [hold()]),
            },
        )

        await engine.tick()

        self.assertTrue(world.paused)
        self.assertIn("exceeded invalid decision", world.pause_reason or "")


def _adjacent_empty_tile(world, faction_id: str) -> tuple[int, int]:
    for tile in world.faction_tiles(faction_id):
        for neighbor in world.neighbors(tile.x, tile.y):
            if (
                neighbor.owner is None
                and neighbor.is_passable()
                and world.is_visible(faction_id, neighbor.x, neighbor.y)
            ):
                return (neighbor.x, neighbor.y)
    raise AssertionError("no adjacent empty tile found")


def _non_adjacent_empty_tile(world, faction_id: str) -> tuple[int, int]:
    owned = {(tile.x, tile.y) for tile in world.faction_tiles(faction_id)}
    adjacent = {
        (neighbor.x, neighbor.y)
        for tile in world.faction_tiles(faction_id)
        for neighbor in world.neighbors(tile.x, tile.y)
    }
    for tile in world.tiles:
        if (
            tile.owner is None
            and tile.is_passable()
            and (tile.x, tile.y) not in owned
            and (tile.x, tile.y) not in adjacent
        ):
            return (tile.x, tile.y)
    raise AssertionError("no non-adjacent empty tile found")


def _start_positions(world) -> dict[str, list[tuple[int, int]]]:
    return {
        faction_id: [(tile.x, tile.y) for tile in world.faction_tiles(faction_id)]
        for faction_id in sorted(world.factions)
    }


if __name__ == "__main__":
    unittest.main()
