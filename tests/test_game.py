import json
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


def hold() -> LeaderDecision:
    return LeaderDecision(turn_intent="hold position")


class GameTests(unittest.IsolatedAsyncioTestCase):
    def test_default_world_is_seeded_and_renderable(self) -> None:
        first = create_default_world(seed=123)
        second = create_default_world(seed=123)

        self.assertEqual(
            [tile.terrain for tile in first.tiles],
            [tile.terrain for tile in second.tiles],
        )
        self.assertIn("Tick 0", render_map(first))
        self.assertIn("Human", render_status(first))

    def test_god_commands_change_world_and_inbox(self) -> None:
        world = create_default_world(seed=1)
        engine = GameEngine(world)

        engine.god.give_resource("human", "food", 25)
        engine.god.set_weather(0, 0, "storm")
        engine.god.claim_tile("human", 0, 0)
        world.add_petition(
            faction_id="human",
            kind="resources",
            request={"resource": "wood", "amount": 5},
            reason="build a shrine",
        )

        self.assertEqual(world.factions["human"].resources.food, 145)
        self.assertEqual(world.tile_at(0, 0).weather, "storm")
        self.assertEqual(world.tile_at(0, 0).owner, "human")
        self.assertIn("build a shrine", render_inbox(world))

        engine.god.answer_petition(1, True)
        self.assertEqual(world.petitions[0].status, "approved")
        self.assertEqual(world.factions["human"].resources.wood, 85)

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
        before_food = world.factions["human"].resources.food
        decision = LeaderDecision.from_mapping(
            {
                "turn_intent": "farm and settle",
                "population_orders": [
                    {
                        "task": "farm",
                        "target": {"x": target[0], "y": target[1]},
                        "workers": 12,
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

        self.assertGreater(world.factions["human"].resources.food, before_food)
        self.assertEqual(world.tile_at(*target).owner, "human")

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
                "inspect_realm",
                "inspect_tiles",
                "inspect_faction",
                "submit_leader_turn",
            ],
        )

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


if __name__ == "__main__":
    unittest.main()
