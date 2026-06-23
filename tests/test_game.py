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
    LEADER_CHAT_SYSTEM_PROMPT,
    LEADER_RECENT_CONTEXT_TURNS,
    LEADER_SYSTEM_PROMPT,
    SUBMIT_LEADER_TURN_TOOL,
    _build_leader_task,
    _expansion_candidates,
    _faction_doctrine,
    record_leader_rule_error,
)
from simagentplg.game.npc import _food_output

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
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=self.responses.pop(0))]
        )


class FakeClient:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


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


class ContextTrackingLeader:
    def __init__(self, faction_id: str) -> None:
        self.faction_id = faction_id
        self.last_task: str | None = None

    async def decide(
        self,
        world,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision:
        self.last_task = f"task for {self.faction_id} at {world.tick}"
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
            self.assertEqual(first.total_population(faction_id), 15)
            self.assertEqual(first.total_soldiers(faction_id), 5)
            self.assertEqual(first.total_houses(faction_id), 2)
            jobs = first.total_jobs(faction_id)
            self.assertEqual(jobs["farmer"], 10)
            self.assertEqual(jobs["lumberjack"], 0)
            self.assertEqual(jobs["miner"], 0)
            self.assertEqual(jobs["builder"], 0)
            self.assertEqual(jobs["idle"], 5)
            self.assertIsNotNone(first.factions[faction_id].home_tile)
            self.assertEqual(first.home_owner(faction_id), faction_id)
            home = first.factions[faction_id].home_tile
            self.assertEqual(first.home_of_tile(*home), faction_id)
            self.assertEqual(
                first.factions[faction_id].leader_memory,
                {"god_dialogue": [], "rule_errors": []},
            )
            self.assertEqual(first.factions[faction_id].leader_context_window, [])
        self.assertIn("Tick 0", render_map(first))
        self.assertIn("Human", render_status(first))

    def test_starting_home_tiles_are_not_too_close(self) -> None:
        default_world = create_default_world(width=32, height=20, seed=123)
        web_sized_world = create_default_world(width=12, height=8, seed=9)

        self.assertGreaterEqual(_minimum_home_distance(default_world), 8)
        self.assertGreaterEqual(_minimum_home_distance(web_sized_world), 6)

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

    def test_god_claiming_home_tile_eliminates_previous_owner(self) -> None:
        world = create_default_world(seed=1)
        engine = GameEngine(world)
        human = world.faction_tiles("human")[0]
        target = world.neighbors(human.x, human.y)[0]
        target.terrain = "plain"
        target.owner = "elf"
        target.set_population("elf", 6)
        world.factions["elf"].home_tile = (target.x, target.y)
        world.factions["elf"].resources.food = 33
        world.factions["elf"].resources.wood = 22
        world.factions["elf"].resources.stone = 11
        before = world.factions["human"].resources.as_dict()

        engine.god.claim_tile("human", target.x, target.y)

        self.assertTrue(world.factions["elf"].eliminated)
        self.assertEqual(world.total_population("elf"), 0)
        self.assertEqual(world.total_soldiers("elf"), 0)
        self.assertEqual(world.factions["human"].resources.food, before["food"] + 33)
        self.assertEqual(world.factions["human"].resources.wood, before["wood"] + 22)
        self.assertEqual(world.factions["human"].resources.stone, before["stone"] + 11)

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

    def test_world_records_private_god_chat_by_faction(self) -> None:
        world = create_default_world(seed=20)

        first = world.add_god_chat_message(
            faction_id="human",
            speaker="god",
            content="攻打兽人，我会赐予粮食。",
        )
        world.add_god_chat_message(
            faction_id="elf",
            speaker="god",
            content="守住森林。",
        )
        third = world.add_god_chat_message(
            faction_id="human",
            speaker="leader",
            content="我们会考虑边境战机。",
        )

        human_messages = world.recent_god_chat("human")
        self.assertEqual(first.message_id, 1)
        self.assertEqual(third.message_id, 3)
        self.assertEqual([message.message_id for message in human_messages], [1, 3])
        self.assertEqual(human_messages[0].speaker, "god")
        self.assertTrue(any(event.kind == "god_chat" for event in world.events))

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

        self.assertGreater(worker_tile.professions_of("human")["farmer"], 10)
        self.assertEqual(world.tile_at(*target).owner, "human")
        self.assertEqual(world.tile_at(*target).population_of("human"), 2)

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
                        "workers": 2,
                    },
                    {
                        "task": "build",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 2,
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
        self.assertEqual(target.population_of("human"), 2)
        jobs = world.total_jobs("human")
        self.assertEqual(jobs["farmer"], 12)
        self.assertEqual(jobs["builder"], 2)

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
        self.assertEqual(targets[0].population_of("human"), 2)
        self.assertEqual(targets[1].population_of("human"), 2)
        self.assertEqual(len(world.faction_tiles("human")), 3)

    def test_settle_moves_specified_profession_to_unowned_tile(self) -> None:
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
                "turn_intent": "派农夫迁入新平原",
                "territory_orders": [
                    {
                        "action": "settle",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target.x, "y": target.y},
                        "profession": "farmer",
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "human")
        self.assertEqual(origin.professions_of("human")["farmer"], 8)
        self.assertEqual(target.professions_of("human")["farmer"], 2)
        self.assertEqual(target.population_of("human"), 2)

    def test_settle_uses_requested_migration_amount(self) -> None:
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
                "turn_intent": "迁更多农夫开发平原",
                "territory_orders": [
                    {
                        "action": "settle",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target.x, "y": target.y},
                        "profession": "farmer",
                        "amount": 4,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "human")
        self.assertEqual(origin.professions_of("human")["farmer"], 6)
        self.assertEqual(target.professions_of("human")["farmer"], 4)
        self.assertEqual(target.population_of("human"), 4)

    def test_settle_moves_civilian_into_owned_tile(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "human"
        target.population.clear()
        target.professions.clear()
        target.soldiers["human"] = 3

        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "把农夫迁入士兵守住的新领地",
                "territory_orders": [
                    {
                        "action": "settle",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target.x, "y": target.y},
                        "profession": "farmer",
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "human")
        self.assertEqual(target.soldiers_of("human"), 3)
        self.assertEqual(target.professions_of("human")["farmer"], 2)

    def test_claim_does_not_move_into_owned_tile(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "human"
        target.set_population("human", 1)

        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "错误使用claim迁入已有领地",
                "territory_orders": [
                    {"action": "claim", "target": {"x": target.x, "y": target.y}}
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("claim target must be unowned" in error for error in check.errors))

    def test_settle_auto_selects_next_movable_civilian_profession(self) -> None:
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
        origin.professions["human"] = {
            "farmer": 1,
            "lumberjack": 0,
            "miner": 0,
            "builder": 2,
            "idle": 0,
        }
        origin.set_population("human", 3)

        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "自动选择可迁平民扩张",
                "territory_orders": [
                    {"action": "settle", "target": {"x": target.x, "y": target.y}}
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "human")
        self.assertEqual(target.professions_of("human")["builder"], 2)

    def test_settlement_does_not_drain_source_tile_population(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        origin = world.faction_tiles("human")[0]
        target = _adjacent_empty_tile(world, "human")
        origin.soldiers["human"] = 0
        origin.set_population("human", 2)
        origin.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": 2,
        }
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "训练一名士兵并派一人扩张",
                "population_orders": [
                    {
                        "task": "train",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 5,
                    }
                ],
                "territory_orders": [
                    {"action": "claim", "target": {"x": target[0], "y": target[1]}}
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(
            any("no movable civilian" in error for error in check.errors),
            check.errors,
        )

    def test_npc_refuses_settlement_that_would_empty_source_tile(self) -> None:
        world = create_default_world(seed=30)
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = _adjacent_empty_tile(world, "human")
        target_tile = world.tile_at(*target)
        origin.set_population("human", 2)
        origin.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": 2,
        }
        origin.soldiers["human"] = 0
        world.factions["human"].active_orders = {
            "territory_orders": [
                {"action": "claim", "target": {"x": target[0], "y": target[1]}}
            ]
        }

        npc.execute_active_orders(world, "human")

        self.assertEqual(origin.owner, "human")
        self.assertIsNone(target_tile.owner)
        self.assertTrue(any("failed to settle" in event.message for event in world.events))

    def test_settlement_can_move_last_civilian_from_soldier_held_tile(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = _adjacent_empty_tile(world, "human")
        target_tile = world.tile_at(*target)
        origin.set_population("human", 2)
        origin.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": 2,
        }
        origin.soldiers["human"] = 2
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "士兵守住原地，平民迁出",
                "territory_orders": [
                    {"action": "claim", "target": {"x": target[0], "y": target[1]}}
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(origin.owner, "human")
        self.assertEqual(origin.population_of("human"), 0)
        self.assertEqual(origin.soldiers_of("human"), 2)
        self.assertEqual(target_tile.owner, "human")
        self.assertEqual(target_tile.population_of("human"), 2)

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
        self.assertTrue(any("assigns 10 workers" in error for error in check.errors))

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
                        "workers": 5,
                    },
                    {
                        "task": "build",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 4,
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
        self.assertTrue(any("idle population budget is overcommitted" in error for error in check.errors))

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

    def test_negated_plan_keywords_do_not_require_matching_orders(self) -> None:
        world = create_default_world(seed=30)
        rules = RuleEngine()
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "天气晴好，无需祈求天气；暂不发动进攻，也不做任何领土扩张。",
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertTrue(check.accepted, check.errors)

    def test_npc_population_growth_adds_one_idle_each_tick_on_owned_tiles(self) -> None:
        world = create_default_world(seed=31)
        npc = NPCExecutor()
        home = world.tile_at(*world.factions["human"].home_tile)
        target = _adjacent_empty_tile(world, "human")
        frontier = world.tile_at(*target)
        frontier.owner = "human"
        frontier.terrain = "plain"
        frontier.set_population("human", 1)
        frontier.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": 1,
        }
        before_population = home.population_of("human")
        before_home_idle = home.professions_of("human")["idle"]
        before_frontier = frontier.population_of("human")
        before_frontier_idle = frontier.professions_of("human")["idle"]

        npc.apply_passive_tick(world)

        self.assertEqual(home.population_of("human"), before_population + 1)
        self.assertEqual(home.professions_of("human")["idle"], before_home_idle + 1)
        self.assertEqual(frontier.population_of("human"), before_frontier + 1)
        self.assertEqual(
            frontier.professions_of("human")["idle"],
            before_frontier_idle + 1,
        )
        self.assertTrue(
            any("population grew by" in event.message for event in world.events)
        )

    def test_population_growth_respects_food_weather_and_capacity(self) -> None:
        world = create_default_world(seed=31)
        npc = NPCExecutor()
        home = world.tile_at(*world.factions["human"].home_tile)
        world.factions["human"].resources.food = 19
        before = home.population_of("human")

        npc.apply_passive_tick(world)

        self.assertEqual(home.population_of("human"), before)

        world.factions["human"].resources.food = 120
        home.weather = "drought"
        world.tick = 1

        npc.apply_passive_tick(world)

        self.assertEqual(home.population_of("human"), before)

        home.weather = "rain"
        home.set_population("human", home.capacity() - 1)
        home.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": home.capacity() - 1,
        }

        npc.apply_passive_tick(world)

        self.assertEqual(home.population_of("human"), home.capacity())
        self.assertEqual(home.professions_of("human")["idle"], home.capacity())

    def test_farmer_food_output_is_higher_on_good_farmland(self) -> None:
        farmers = 10

        self.assertEqual(
            _food_output(SimpleNamespace(terrain="plain", weather="clear"), farmers),
            3,
        )
        self.assertEqual(
            _food_output(SimpleNamespace(terrain="plain", weather="rain"), farmers),
            4,
        )
        self.assertEqual(
            _food_output(SimpleNamespace(terrain="forest", weather="clear"), farmers),
            1,
        )
        self.assertEqual(
            _food_output(SimpleNamespace(terrain="hill", weather="clear"), farmers),
            1,
        )
        self.assertEqual(
            _food_output(SimpleNamespace(terrain="water", weather="clear"), farmers),
            0,
        )

    def test_weather_damages_population_on_owned_tiles(self) -> None:
        world = create_default_world(seed=32)
        npc = NPCExecutor()
        tile = world.faction_tiles("human")[0]
        tile.houses = 0
        tile.set_population("human", tile.capacity())
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
        self.assertEqual(target.population_of("human"), 0)
        self.assertGreater(target.soldiers_of("human"), 0)
        self.assertEqual(world.factions["human"].relation_to("elf"), "war")
        self.assertEqual(world.factions["elf"].relation_to("human"), "war")
        self.assertIn("elf", world.factions["human"].known_factions)
        self.assertIn("human", world.factions["elf"].known_factions)
        self.assertTrue(any(event.kind == "battle" for event in world.events))
        self.assertTrue(
            any(
                event.kind == "battle"
                and event.faction_id == "elf"
                and "captured" in event.message
                for event in world.events
            )
        )

    def test_military_move_transfers_soldiers_between_adjacent_owned_tiles(self) -> None:
        world = create_default_world(seed=36)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = _adjacent_empty_tile(world, "human")
        target_tile = world.tile_at(*target)
        target_tile.terrain = "plain"
        target_tile.owner = "human"
        target_tile.set_population("human", 3)
        target_tile.soldiers["human"] = 0
        origin.soldiers["human"] = 20
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "调兵防守边境",
                "military_orders": [
                    {
                        "action": "move",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target_tile.x, "y": target_tile.y},
                        "force_ratio": 0.5,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        npc.execute_active_orders(world, "human")

        self.assertTrue(check.accepted, check.errors)
        self.assertEqual(origin.soldiers_of("human"), 10)
        self.assertEqual(target_tile.soldiers_of("human"), 10)
        self.assertTrue(any("moved 10 soldiers" in event.message for event in world.events))

    def test_failed_attack_notifies_defender(self) -> None:
        world = create_default_world(seed=35)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "hill"
        target.owner = "elf"
        target.soldiers["elf"] = 20
        origin.soldiers["human"] = 5
        world.factions["human"].known_factions.add("elf")
        world.factions["elf"].known_factions.add("human")
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "试探精灵山地防线",
                "diplomacy_orders": [
                    {"target_faction": "elf", "proposal": "war"}
                ],
                "military_orders": [
                    {
                        "action": "attack",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target.x, "y": target.y},
                        "force_ratio": 1,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")

        self.assertEqual(target.owner, "elf")
        self.assertTrue(
            any(
                event.kind == "battle"
                and event.faction_id == "elf"
                and "failed" in event.message
                for event in world.events
            )
        )

    def test_military_move_requires_adjacent_owned_target(self) -> None:
        world = create_default_world(seed=36)
        rules = RuleEngine()
        origin = world.faction_tiles("human")[0]
        target = _non_adjacent_empty_tile(world, "human")
        target_tile = world.tile_at(*target)
        target_tile.terrain = "plain"
        target_tile.owner = "human"
        target_tile.set_population("human", 3)
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "远距离调兵",
                "military_orders": [
                    {
                        "action": "move",
                        "origin": {"x": origin.x, "y": origin.y},
                        "target": {"x": target_tile.x, "y": target_tile.y},
                        "force_ratio": 1,
                    }
                ],
            }
        )

        check = rules.validate_decision(world, "human", decision)

        self.assertFalse(check.accepted)
        self.assertTrue(any("adjacent" in error for error in check.errors))

    def test_capturing_home_tile_eliminates_defender_and_transfers_resources(self) -> None:
        world = create_default_world(seed=37)
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "elf"
        target.set_population("elf", 5)
        target.soldiers["elf"] = 1
        world.factions["elf"].home_tile = (target.x, target.y)
        world.factions["elf"].resources.food = 60
        world.factions["elf"].resources.wood = 30
        world.factions["elf"].resources.stone = 12
        world.factions["human"].known_factions.add("elf")
        origin.set_population("human", 10)
        origin.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": 10,
        }
        origin.soldiers["human"] = 30
        before = world.factions["human"].resources.as_dict()
        world.factions["human"].active_orders = {
            "military_orders": [
                {
                    "action": "attack",
                    "origin": {"x": origin.x, "y": origin.y},
                    "target": {"x": target.x, "y": target.y},
                    "force_ratio": 1,
                }
            ]
        }

        npc.execute_active_orders(world, "human")

        self.assertTrue(world.factions["elf"].eliminated)
        self.assertEqual(world.total_population("elf"), 0)
        self.assertEqual(world.total_soldiers("elf"), 0)
        self.assertEqual(
            world.factions["elf"].resources.as_dict(),
            {"food": 0, "wood": 0, "stone": 0},
        )
        self.assertEqual(world.factions["human"].resources.food, before["food"] + 60)
        self.assertEqual(world.factions["human"].resources.wood, before["wood"] + 30)
        self.assertEqual(world.factions["human"].resources.stone, before["stone"] + 12)
        self.assertEqual(target.owner, "human")
        self.assertTrue(any(event.kind == "elimination" for event in world.events))

    def test_capturing_non_home_tile_does_not_eliminate_defender(self) -> None:
        world = create_default_world(seed=37)
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "elf"
        target.set_population("elf", 5)
        target.soldiers["elf"] = 1
        world.factions["human"].known_factions.add("elf")
        origin.set_population("human", 10)
        origin.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": 10,
        }
        origin.soldiers["human"] = 30
        world.factions["human"].active_orders = {
            "military_orders": [
                {
                    "action": "attack",
                    "origin": {"x": origin.x, "y": origin.y},
                    "target": {"x": target.x, "y": target.y},
                    "force_ratio": 1,
                }
            ]
        }

        npc.execute_active_orders(world, "human")

        self.assertFalse(world.factions["elf"].eliminated)

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

    def test_attack_without_movable_population_captures_with_soldiers(self) -> None:
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

        self.assertEqual(target.owner, "human")
        self.assertEqual(target.population_of("human"), 0)
        self.assertGreater(target.soldiers_of("human"), 0)
        self.assertTrue(any("captured" in event.message for event in world.events))

    def test_soldier_only_tile_keeps_owner_until_soldiers_leave(self) -> None:
        world = create_default_world(seed=38)
        rules = RuleEngine()
        npc = NPCExecutor()
        origin = world.faction_tiles("human")[0]
        captured = world.neighbors(origin.x, origin.y)[0]
        captured.terrain = "plain"
        captured.owner = "elf"
        captured.set_population("elf", 10)
        captured.soldiers = {"elf": 1}
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
                        "target": {"x": captured.x, "y": captured.y},
                        "force_ratio": 0.8,
                    }
                ],
            }
        )

        check = rules.apply_decision(world, "human", decision)
        self.assertTrue(check.accepted, check.errors)
        npc.execute_active_orders(world, "human")
        self.assertEqual(captured.owner, "human")
        self.assertEqual(captured.population_of("human"), 0)

        move_target = next(
            tile
            for tile in world.neighbors(captured.x, captured.y)
            if tile.owner == "human" and (tile.x, tile.y) != (captured.x, captured.y)
        )
        captured.soldiers["human"] = 4
        world.factions["human"].active_orders = {
            "military_orders": [
                {
                    "action": "move",
                    "origin": {"x": captured.x, "y": captured.y},
                    "target": {"x": move_target.x, "y": move_target.y},
                    "force_ratio": 1,
                }
            ]
        }

        npc.execute_active_orders(world, "human")

        self.assertIsNone(captured.owner)
        self.assertEqual(captured.soldiers_of("human"), 0)

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
        self.assertTrue(
            any(
                event.kind == "battle"
                and event.faction_id == "elf"
                and "raided" in event.message
                for event in world.events
            )
        )

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
                        "workers": 5,
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
        self.assertIn("Tool usage:", LEADER_SYSTEM_PROMPT)
        self.assertIn("submit_leader_turn is the only function that ends", LEADER_SYSTEM_PROMPT)
        self.assertNotIn('{"mode": "realm"}', LEADER_SYSTEM_PROMPT)
        self.assertNotIn("Call inspect", LEADER_SYSTEM_PROMPT)
        self.assertIn("human-facing narrative text", LEADER_SYSTEM_PROMPT)
        self.assertIn("Leaders cannot ask the god for people", LEADER_SYSTEM_PROMPT)
        self.assertIn("There is no dismiss-worker or assign-back-to-idle order", task)
        self.assertNotIn("assign them back to idle", LEADER_SYSTEM_PROMPT)

    def test_leader_task_emphasizes_victory_civilian_migration_and_soldier_occupation(self) -> None:
        world = create_default_world(seed=49)
        world.factions["human"].leader_memory["legacy_history"] = [
            {
                "tick": 35,
                "kind": "war_history",
                "note": "曾决定优先压制兽人。",
                "status": "historical",
            }
        ]
        world.factions["human"].leader_memory["stale_rule_memory"] = [
            {
                "first_tick": 30,
                "last_tick": 30,
                "count": 1,
                "pattern": "军事命令 origin 必须有士兵",
                "lesson": "军事命令必须从当前有己方士兵的地块发起。",
            }
        ]
        world.factions["human"].leader_context_window.append(
            {
                "tick": 45,
                "accepted": True,
                "strategy_summary": "训练士兵",
                "feedback_attempts": [],
                "after_execution": {"soldiers": 8},
                "events": [{"kind": "battle", "message": "演示事件"}],
            }
        )

        task = _build_leader_task(world, "human", None)
        schema = SUBMIT_LEADER_TURN_TOOL["function"]["parameters"]["properties"]
        territory_schema = schema["territory_orders"]["items"]["properties"]
        petition_schema = schema["petitions"]["items"]["properties"]

        for expected in (
            "final objective",
            "defeat the other races",
            "Current battle conditions",
            "Historical notes never decide legality",
            "old sources",
            "old plans",
            "Soldiers can move only between adjacent owned",
            "currently has at least 1 soldier",
            "from a tile with 0 soldiers",
            "Protect your home tile",
            "outer territory is also vital",
            "frontier tiles can grow naturally",
            "shift surplus idle people toward training",
            "border reinforcement",
            "matching concrete order",
            "workers means the number of idle people",
            "Build orders automatically spend wood",
            "claimed, settled, or captured later this turn",
            "Choose claim/settle amount",
            "settle.origin is set",
            "specified profession must have",
            "distant same-faction",
            "too few movable civilians and 0 soldiers",
            "attacks that win",
            "surviving attackers move",
            "Resource petitions must use request",
            '"resource":"food|wood|stone"',
            "positive amount",
            "use population task gather_wood",
            "Do not use the profession name lumberjack",
            "safe_civilian_migrations, target capacity",
        ):
            self.assertIn(expected, LEADER_SYSTEM_PROMPT)
        for expected in (
            "Compact submit_leader_turn examples",
            "Economy:",
            "Claim:",
            "Settle owned land:",
            "Attack:",
            "Resource petition:",
            '"workers":2',
            '"action":"settle"',
            '"profession":"farmer"',
            '"action":"attack"',
            '"type":"resources"',
            '"amount":80',
        ):
            self.assertIn(expected, LEADER_SYSTEM_PROMPT)
        self.assertNotIn("preserve at least 1 idle person on the attacking", LEADER_SYSTEM_PROMPT)
        self.assertIn("origin", territory_schema)
        self.assertIn("profession", territory_schema)
        self.assertIn("amount", territory_schema)
        self.assertEqual(territory_schema["amount"]["minimum"], 1)
        self.assertEqual(
            territory_schema["profession"]["enum"],
            ["idle", "farmer", "lumberjack", "miner", "builder"],
        )
        self.assertIn("positive_integer", petition_schema["request"]["description"])
        self.assertIn("Long-term objective", task)
        self.assertIn("defeat rival civilizations", task)
        self.assertIn("Decision priority: current battle conditions", task)
        self.assertIn("Historical memory is not used for strategic legality", task)
        self.assertIn("current resources, owned tiles, visible enemies", task)
        self.assertIn("shift surplus idle people toward training", task)
        self.assertIn("Home tile status:", task)
        self.assertIn("outer territory is also important", task)
        self.assertIn("Safe owned frontier tiles can grow naturally", task)
        self.assertIn("move", task)
        self.assertIn("Civilian movement focus tiles:", task)
        self.assertIn("Expansion recommendation:", task)
        self.assertIn("claim_now", task)
        self.assertIn("Prefer at least one claim", task)
        self.assertIn("recommended_idle_uses", task)
        self.assertIn("safe_civilian_migrations", task)
        self.assertIn("settlement_sources", task)
        self.assertIn("Recent strategic turn (continuity only", task)
        self.assertIn("Current task facts (highest priority)", task)
        self.assertIn("Pre-submit legality checklist", task)
        self.assertNotIn("曾决定优先压制兽人", task)
        self.assertNotIn("军事命令必须从当前有己方士兵", task)
        self.assertIn("训练士兵", task)
        self.assertIn("settle can include origin and profession", task)
        self.assertIn("claim/settle can choose amount", task)
        self.assertIn("Migration benefits", task)
        self.assertIn("Moving farmers onto plains", task)
        self.assertIn("source tile must be directly adjacent", task)
        self.assertIn("safe_civilian_migrations", task)
        self.assertIn("movable_jobs", task)
        self.assertIn("maximum number of civilians", task)
        self.assertIn("surviving soldiers directly occupy", task)
        self.assertIn("already own before this turn executes", task)
        self.assertIn("include the matching concrete order", task)
        self.assertIn("current soldiers > 0", task)
        self.assertIn('"resource":"food","amount":50', task)
        self.assertIn("expansion wording needs claim/settle", task)
        self.assertNotIn("reserve at least 1 idle person on that exact attacking origin tile", task)

    def test_leader_task_includes_only_own_recent_god_dialogue(self) -> None:
        world = create_default_world(seed=50)
        world.add_god_chat_message(
            faction_id="human",
            speaker="god",
            content="攻打兽人，我会赐予粮食。",
        )
        world.add_god_chat_message(
            faction_id="elf",
            speaker="god",
            content="守住森林，不要相信人类。",
        )

        task = _build_leader_task(world, "human", None)

        self.assertIn("Direct god dialogue (highest narrative priority", task)
        self.assertIn("Recent relevant events", task)
        self.assertIn("god_chat", task)
        self.assertIn("攻打兽人", task)
        self.assertNotIn("不要相信人类", task)
        self.assertEqual(
            world.factions["human"].leader_memory["god_dialogue"][-1]["content"],
            "攻打兽人，我会赐予粮食。",
        )
        self.assertIn("private political conversation", LEADER_CHAT_SYSTEM_PROMPT)
        self.assertIn("Actual game actions happen only during strategic turns", LEADER_CHAT_SYSTEM_PROMPT)
        self.assertIn("must not claim that orders", LEADER_CHAT_SYSTEM_PROMPT)

    def test_faction_doctrines_express_distinct_priorities(self) -> None:
        orc = _faction_doctrine("orc")
        elf = _faction_doctrine("elf")
        human = _faction_doctrine("human")

        self.assertIn("Aggressive conquerors", orc)
        self.assertIn("build soldier advantage", orc)
        self.assertIn("capturing enemy home tiles", orc)
        self.assertIn("Diplomacy is temporary and tactical", orc)
        self.assertIn("Resource surplus should quickly become soldiers", orc)

        self.assertIn("Defensive forest stewards", elf)
        self.assertIn("forest heartland", elf)
        self.assertIn("alliances, peace, and non-aggression", elf)
        self.assertIn("punish nearby threats", elf)
        self.assertIn("Resource surplus should become defensive depth", elf)

        self.assertIn("Pragmatic settler-builders", human)
        self.assertIn("secure food, housing", human)
        self.assertIn("safe open land", human)
        self.assertIn("switch to deterrence, raids, or war", human)
        self.assertIn("After economic stability, convert surplus into soldiers", human)

        for doctrine in (orc, elf, human):
            self.assertIn("Personality changes priorities, not legality", doctrine)
            self.assertIn("ignore idle people", doctrine)
            self.assertIn("protect the home tile", doctrine)
            self.assertIn("decisive attack", doctrine)

    def test_leader_task_marks_border_soldier_occupation(self) -> None:
        world = create_default_world(seed=52)
        origin = world.faction_tiles("human")[0]
        target = world.neighbors(origin.x, origin.y)[0]
        target.terrain = "plain"
        target.owner = "orc"
        target.set_population("orc", 5)
        target.soldiers["orc"] = 0
        world.factions["human"].known_factions.add("orc")
        origin.set_population("human", 5)
        origin.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": 5,
        }
        origin.soldiers["human"] = 20

        task = _build_leader_task(world, "human", None)

        self.assertIn("'origin_movable_jobs': {'idle': 5", task)
        self.assertIn("'winning_attackers_can_occupy': True", task)
        self.assertIn("'target_is_enemy_home': False", task)

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

        self.assertIn("Previous strategic result", task)
        self.assertIn("planned_tick", task)
        self.assertIn("after_execution", task)
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
        agent.messages.append({"role": "user", "content": "stale turn"})

        decision = await controller.decide(world)

        self.assertEqual(decision.turn_intent, "hold")
        self.assertFalse(
            any(message.get("content") == "stale turn" for message in agent.messages)
        )
        self.assertIn("You lead faction", controller.last_task or "")
        self.assertEqual(
            [tool["function"]["name"] for tool in agent.tools],
            [
                "submit_leader_turn",
            ],
        )
        self.assertFalse(any(hasattr(handler, "do_inspect") for handler in agent.handlers))

    async def test_llm_leader_controller_syncs_memory_without_llm(self) -> None:
        world = create_default_world(seed=4)
        faction = world.factions["human"]
        world.add_god_chat_message(
            faction_id="human",
            speaker="god",
            content="上帝要求攻打兽人。",
        )
        faction.leader_memory["legacy_history"] = [{"note": "不应保留"}]
        faction.leader_memory["rule_errors"] = [
            {"tick": 5, "error": "旧错误1"},
            {"tick": 10, "error": "旧错误2"},
        ]
        faction.leader_context_window = [
            {"tick": 5, "task": "task-5", "accepted": True},
            {"tick": 10, "task": "task-10", "accepted": True},
        ]
        strategic_agent = BaseAgent(
            TEST_CONFIG,
            agent_id="leader-human",
            enable_tools=True,
            client=FakeClient([]),
        )
        memory_client = FakeClient([FakeMessage(content='{"should":"not be called"}')])
        memory_agent = BaseAgent(
            TEST_CONFIG,
            agent_id="leader-human-memory",
            enable_tools=False,
            client=memory_client,
        )
        controller = LLMLeaderController(
            faction_id="human",
            agent=strategic_agent,
            memory_agent=memory_agent,
        )

        refreshed = await controller.compress_memory_if_needed(world)

        self.assertTrue(refreshed)
        self.assertEqual(
            faction.leader_context_window,
            [{"tick": 10, "task": "task-10", "accepted": True}],
        )
        self.assertEqual(list(faction.leader_memory), ["god_dialogue", "rule_errors"])
        self.assertEqual(faction.leader_memory["god_dialogue"][-1]["content"], "上帝要求攻打兽人。")
        self.assertEqual(
            faction.leader_memory["rule_errors"],
            [
                {"tick": 5, "error": "旧错误1"},
                {"tick": 10, "error": "旧错误2"},
            ],
        )
        self.assertEqual(memory_client.completions.calls, [])

    def test_rule_error_memory_keeps_latest_three_entries(self) -> None:
        world = create_default_world(seed=4)
        faction = world.factions["human"]

        for index in range(4):
            record_leader_rule_error(
                faction.leader_memory,
                f"错误{index}",
                tick=index,
            )

        self.assertEqual(
            [item["error"] for item in faction.leader_memory["rule_errors"]],
            ["错误1", "错误2", "错误3"],
        )
        self.assertEqual(faction.leader_memory["god_dialogue"], [])

    def test_rule_error_memory_merges_same_tick_retries(self) -> None:
        world = create_default_world(seed=4)
        faction = world.factions["human"]

        record_leader_rule_error(
            faction.leader_memory,
            "territory_order 1: faction has no movable civilian to settle target while leaving the source tile held",
            tick=15,
        )
        record_leader_rule_error(
            faction.leader_memory,
            "population_order 1: unknown task 'lumberjack'; territory_order 1: faction has no movable civilian to settle target while leaving the source tile held",
            tick=15,
        )

        self.assertEqual(len(faction.leader_memory["rule_errors"]), 1)
        error = faction.leader_memory["rule_errors"][0]
        self.assertEqual(error["count"], 2)
        self.assertIn("unknown task 'lumberjack'", error["error"])
        self.assertIn("settlement_source_hold", error["categories"])
        self.assertIn("plan_order_mismatch", error["categories"])

    def test_expansion_candidates_exclude_targets_without_safe_migrant_source(self) -> None:
        world = create_default_world(seed=54)
        home = world.tile_at(*world.factions["human"].home_tile)
        home.set_population("human", 1)
        home.professions["human"] = {
            "farmer": 0,
            "lumberjack": 0,
            "miner": 0,
            "builder": 0,
            "idle": 1,
        }
        home.soldiers["human"] = 0
        for tile in world.faction_tiles("human"):
            if tile is not home:
                tile.owner = None
                tile.set_population("human", 0)
                tile.soldiers["human"] = 0

        candidates = _expansion_candidates(world, "human")

        self.assertEqual(candidates, [])

    async def test_llm_leader_controller_answers_god_chat_without_tools(self) -> None:
        world = create_default_world(seed=4)
        world.add_god_chat_message(
            faction_id="human",
            speaker="god",
            content="若你攻打兽人，我会赐予粮食。",
        )
        strategic_agent = BaseAgent(
            TEST_CONFIG,
            agent_id="leader-human",
            enable_tools=True,
            client=FakeClient([]),
        )
        chat_agent = BaseAgent(
            TEST_CONFIG,
            agent_id="leader-human-chat",
            system_prompt=LEADER_CHAT_SYSTEM_PROMPT,
            enable_tools=False,
            max_steps=1,
            client=FakeClient([FakeMessage(content="我会整军观察兽人的边境。")]),
        )
        controller = LLMLeaderController(
            faction_id="human",
            agent=strategic_agent,
            chat_agent=chat_agent,
        )

        reply = await controller.chat_with_god(world)

        self.assertEqual(reply, "我会整军观察兽人的边境。")
        self.assertEqual(chat_agent.tools, [])

    async def test_leader_task_includes_missing_observation_fields(self) -> None:
        world = create_default_world(seed=4)
        human_tile = world.faction_tiles("human")[0]
        elf_tile = world.neighbors(human_tile.x, human_tile.y)[0]
        elf_tile.terrain = "plain"
        elf_tile.owner = "elf"
        elf_tile.set_population("elf", 8)
        world.factions["human"].known_factions.add("elf")

        task = _build_leader_task(world, "human", None)

        self.assertIn("Visible tile count:", task)
        self.assertIn("Recent relevant events:", task)
        self.assertIn("Known faction visible summaries:", task)
        self.assertIn("'faction_id': 'elf'", task)
        self.assertIn("'visible_population': 8", task)
        self.assertIn("'visible_territory_count': 1", task)
        self.assertNotIn("Current observation snapshot", task)

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
                        "workers": 5,
                    },
                    {
                        "task": "build",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 4,
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
                        "workers": 2,
                    },
                    {
                        "task": "build",
                        "target": {"x": origin.x, "y": origin.y},
                        "workers": 2,
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
        self.assertIn("idle population budget is overcommitted", human.feedback[1] or "")
        self.assertEqual(targets[0].owner, "human")

    async def test_engine_default_allows_three_decision_attempts(self) -> None:
        world = create_default_world(seed=51)
        bad = LeaderDecision.from_mapping(
            {
                "turn_intent": "break rules until final retry",
                "resource_orders": [
                    {"resource": "food", "action": "spend", "amount": 9999}
                ],
            }
        )
        human = ScriptedLeader("human", [bad] * 2 + [hold()])
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
        self.assertEqual(len(human.feedback), 3)
        self.assertIsNone(human.feedback[0])
        self.assertTrue(all(feedback for feedback in human.feedback[1:]))

    async def test_engine_skips_eliminated_leader(self) -> None:
        world = create_default_world(seed=52)
        world.factions["elf"].eliminated = True
        world.factions["elf"].active_orders = {"population_orders": []}
        engine = GameEngine(
            world,
            strategy_interval=1,
            leaders={
                "human": ScriptedLeader("human", [hold()]),
                "orc": ScriptedLeader("orc", [hold()]),
            },
        )

        await engine.tick()

        self.assertFalse(world.paused)
        self.assertEqual(world.factions["elf"].active_orders, {})

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

    async def test_engine_publishes_accepted_plan_before_all_leaders_finish(self) -> None:
        world = create_default_world(seed=53)
        engine = GameEngine(
            world,
            strategy_interval=1,
            leaders={
                "human": DelayedLeader("human", delay=0.01),
                "elf": DelayedLeader("elf", delay=0.2),
                "orc": DelayedLeader("orc", delay=0.2),
            },
        )

        tick_task = asyncio.create_task(engine.tick())
        await asyncio.sleep(0.05)

        self.assertFalse(tick_task.done())
        self.assertEqual(world.factions["human"].last_plan_snapshot["tick"], 1)
        self.assertEqual(
            world.factions["human"].last_plan_snapshot["strategy_summary"],
            "守住当前领地",
        )
        self.assertEqual(world.factions["elf"].last_plan_snapshot, {})
        self.assertEqual(world.factions["orc"].last_plan_snapshot, {})

        await tick_task
        self.assertFalse(world.paused)

    async def test_engine_keeps_only_latest_strategy_context(self) -> None:
        world = create_default_world(seed=8)
        leaders = {
            faction_id: ContextTrackingLeader(faction_id)
            for faction_id in world.factions
        }
        engine = GameEngine(
            world,
            strategy_interval=5,
            leaders=leaders,
        )

        await engine.tick(25)

        self.assertFalse(world.paused)
        for faction_id in leaders:
            window = world.factions[faction_id].leader_context_window
            self.assertEqual(len(window), LEADER_RECENT_CONTEXT_TURNS)
            self.assertEqual(window[0]["tick"], 25)
            self.assertEqual(
                world.factions[faction_id].leader_memory,
                {"god_dialogue": [], "rule_errors": []},
            )

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
        self.assertEqual(len(world.factions["human"].leader_memory["rule_errors"]), 1)
        self.assertEqual(world.factions["human"].leader_memory["rule_errors"][0]["count"], 2)
        self.assertIn(
            "not enough food",
            world.factions["human"].leader_memory["rule_errors"][-1]["error"],
        )


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


def _minimum_home_distance(world) -> int:
    homes = [
        faction.home_tile
        for faction in world.factions.values()
        if faction.home_tile is not None
    ]
    distances = [
        abs(first[0] - second[0]) + abs(first[1] - second[1])
        for index, first in enumerate(homes)
        for second in homes[index + 1:]
    ]
    return min(distances)


if __name__ == "__main__":
    unittest.main()
