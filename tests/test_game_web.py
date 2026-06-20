import asyncio
import unittest

from fastapi.testclient import TestClient

from simagentplg.game import GameEngine, LeaderDecision, create_default_world
from simagentplg.game.web import create_game_app


class WebLeader:
    def __init__(self, faction_id: str) -> None:
        self.faction_id = faction_id
        self.calls = 0

    async def decide(
        self,
        world,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision:
        self.calls += 1
        return LeaderDecision(turn_intent="hold position")


class GameWebTests(unittest.TestCase):
    def make_client(self, *, strategy_interval: int = 5) -> TestClient:
        world = create_default_world(width=12, height=8, seed=9)
        leaders = {
            faction_id: WebLeader(faction_id)
            for faction_id in world.factions
        }
        engine = GameEngine(
            world,
            leaders=leaders,
            strategy_interval=strategy_interval,
        )
        return TestClient(create_game_app(engine))

    def test_index_page_is_localized_to_chinese(self) -> None:
        client = self.make_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("上帝模拟器", response.text)
        self.assertIn("推进 5 刻", response.text)
        self.assertIn("claimFactionSelect", response.text)
        self.assertIn("划给领土", response.text)
        self.assertIn("weatherDuration", response.text)
        self.assertIn("祈求", response.text)

    def test_state_endpoint_returns_renderable_world(self) -> None:
        client = self.make_client()

        response = client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["width"], 12)
        self.assertEqual(payload["height"], 8)
        self.assertEqual(payload["tick"], 0)
        self.assertEqual(len(payload["tiles"]), 96)
        self.assertIn("professions", payload["tiles"][0])
        self.assertIn("houses", payload["tiles"][0])
        self.assertIn("capacity", payload["tiles"][0])
        self.assertIn("weather_duration", payload["tiles"][0])
        self.assertEqual(
            {faction["faction_id"] for faction in payload["factions"]},
            {"human", "elf", "orc"},
        )
        human = next(
            faction
            for faction in payload["factions"]
            if faction["faction_id"] == "human"
        )
        self.assertIn("jobs", human)
        self.assertIn("houses", human)
        self.assertIn("population_capacity", human)
        self.assertIn("known_factions", human)
        self.assertIn("last_plan_snapshot", human)

    def test_god_mutation_endpoints_return_updated_state(self) -> None:
        client = self.make_client()
        engine = client.app.state.engine
        claim_target = _adjacent_empty_tile(engine.world, "human")

        give = client.post(
            "/api/god/give",
            json={"faction_id": "human", "resource": "food", "amount": 7},
        )
        weather = client.post(
            "/api/god/weather",
            json={"x": 0, "y": 0, "weather": "storm", "duration": 4},
        )
        claim = client.post(
            "/api/god/claim",
            json={
                "faction_id": "human",
                "x": claim_target[0],
                "y": claim_target[1],
            },
        )

        self.assertEqual(give.status_code, 200)
        self.assertEqual(weather.status_code, 200)
        self.assertEqual(claim.status_code, 200)
        payload = claim.json()
        human = next(
            faction
            for faction in payload["factions"]
            if faction["faction_id"] == "human"
        )
        tile = payload["tiles"][0]
        claimed = payload["tiles"][claim_target[1] * 12 + claim_target[0]]
        self.assertEqual(human["resources"]["food"], 127)
        self.assertEqual(tile["weather"], "storm")
        self.assertEqual(tile["weather_duration"], 4)
        self.assertEqual(claimed["owner"], "human")

    def test_god_claim_endpoint_rejects_non_adjacent_tile(self) -> None:
        client = self.make_client()
        engine = client.app.state.engine
        target = _non_adjacent_empty_tile(engine.world, "human")

        response = client.post(
            "/api/god/claim",
            json={"faction_id": "human", "x": target[0], "y": target[1]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("must border owned territory", response.json()["error"])

    def test_answer_petition_endpoint_updates_inbox(self) -> None:
        client = self.make_client()
        engine = client.app.state.engine
        engine.world.add_petition(
            faction_id="human",
            kind="resources",
            request={"resource": "wood", "amount": 5},
            reason="build homes",
        )

        response = client.post(
            "/api/god/answer",
            json={"petition_id": 1, "approve": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["petitions"], [])
        self.assertEqual(engine.world.petitions[0].status, "approved")

    def test_tick_endpoint_advances_world_and_fake_leaders(self) -> None:
        client = self.make_client(strategy_interval=1)

        response = client.post("/api/tick", json={"count": 1})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tick"], 1)
        self.assertFalse(payload["paused"])
        for leader in client.app.state.engine.leaders.values():
            self.assertEqual(leader.calls, 1)

    def test_tick_endpoint_rejects_concurrent_request(self) -> None:
        client = self.make_client()
        asyncio.run(client.app.state.tick_lock.acquire())
        try:
            response = client.post("/api/tick", json={"count": 1})
        finally:
            client.app.state.tick_lock.release()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "world is already advancing")

    def test_mutation_errors_are_json(self) -> None:
        client = self.make_client()

        response = client.post(
            "/api/god/give",
            json={"faction_id": "missing", "resource": "food", "amount": 1},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unknown faction", response.json()["error"])


def _adjacent_empty_tile(world, faction_id: str) -> tuple[int, int]:
    for tile in world.faction_tiles(faction_id):
        for neighbor in world.neighbors(tile.x, tile.y):
            if neighbor.owner is None and neighbor.is_passable():
                return (neighbor.x, neighbor.y)
    raise AssertionError("no adjacent empty tile found")


def _non_adjacent_empty_tile(world, faction_id: str) -> tuple[int, int]:
    adjacent = {
        (neighbor.x, neighbor.y)
        for tile in world.faction_tiles(faction_id)
        for neighbor in world.neighbors(tile.x, tile.y)
    }
    for tile in world.tiles:
        if (
            tile.owner is None
            and tile.is_passable()
            and (tile.x, tile.y) not in adjacent
        ):
            return (tile.x, tile.y)
    raise AssertionError("no non-adjacent empty tile found")


if __name__ == "__main__":
    unittest.main()
