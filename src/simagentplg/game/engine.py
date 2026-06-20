from __future__ import annotations

import asyncio
from typing import Protocol

from simagentplg.logger import get_logger
from simagentplg.game.god import GodSystem
from simagentplg.game.leader import LeaderDecision
from simagentplg.game.npc import NPCExecutor
from simagentplg.game.rules import RuleEngine
from simagentplg.game.world import WorldState, create_default_world


class LeaderControllerProtocol(Protocol):
    faction_id: str

    async def decide(
        self,
        world: WorldState,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision: ...


class GameEngine:
    """Coordinate god powers, LLM leaders, rules, NPCs, and world ticks."""

    def __init__(
        self,
        world: WorldState | None = None,
        *,
        leaders: dict[str, LeaderControllerProtocol] | None = None,
        strategy_interval: int = 5,
        retry_limit: int = 9,
        rules: RuleEngine | None = None,
        npc: NPCExecutor | None = None,
        log_ticks: bool = False,
    ) -> None:
        if strategy_interval <= 0:
            raise ValueError("strategy_interval must be positive")
        if retry_limit < 0:
            raise ValueError("retry_limit must not be negative")

        self.world = world or create_default_world()
        self.leaders = leaders or {}
        self.strategy_interval = strategy_interval
        self.retry_limit = retry_limit
        self.rules = rules or RuleEngine()
        self.npc = npc or NPCExecutor()
        self.god = GodSystem(self.world)
        self.log_ticks = log_ticks
        self.logger = get_logger("game-engine")

    async def tick(self, count: int = 1) -> WorldState:
        if count <= 0:
            raise ValueError("count must be positive")

        for _ in range(count):
            if self.world.paused:
                break
            event_start = len(self.world.events)
            self.world.tick += 1
            self._advance_weather()
            self.npc.apply_passive_tick(self.world)
            self.world.enforce_population_ownership()
            self._record_discoveries()

            if self.world.tick % self.strategy_interval == 0:
                await self._run_strategic_turns()
                if self.world.paused:
                    break
                for faction_id in sorted(self.world.factions):
                    self.npc.execute_active_orders(self.world, faction_id)
                self.world.enforce_population_ownership()
                self._record_discoveries()

            self.world.add_event("tick", f"Tick {self.world.tick} completed")
            if self.log_ticks:
                self._log_tick(event_start)
        return self.world

    async def _run_strategic_turns(self) -> None:
        faction_ids = sorted(
            faction_id
            for faction_id, faction in self.world.factions.items()
            if not faction.eliminated
        )
        for faction_id in faction_ids:
            controller = self.leaders.get(faction_id)
            if controller is None:
                self.world.pause(
                    f"Missing LLM leader controller for faction {faction_id}"
                )
                return

        pending = set(faction_ids)
        feedback_by_faction: dict[str, str | None] = {
            faction_id: None for faction_id in faction_ids
        }

        for attempt in range(self.retry_limit + 1):
            results = await asyncio.gather(
                *(
                    self._ask_leader(
                        faction_id,
                        feedback_by_faction[faction_id],
                    )
                    for faction_id in sorted(pending)
                ),
                return_exceptions=True,
            )

            for result in results:
                if isinstance(result, Exception):
                    self.world.pause(
                        f"Leader failed to decide: {result}"
                    )
                    return

                faction_id, decision = result
                check = self.rules.apply_decision(
                    self.world,
                    faction_id,
                    decision,
                )
                if check.accepted:
                    pending.discard(faction_id)
                    continue

                feedback = "; ".join(check.errors)
                feedback_by_faction[faction_id] = feedback
                self.world.add_event(
                    "rule_reject",
                    (
                        f"{faction_id} submitted illegal plan on attempt "
                        f"{attempt + 1}: {feedback}"
                    ),
                    faction_id=faction_id,
                )

            if not pending:
                break

        if pending:
            failed = ", ".join(sorted(pending))
            self.world.pause(
                f"Leader(s) {failed} exceeded invalid decision retry limit"
            )
            return

    async def _ask_leader(
        self,
        faction_id: str,
        feedback: str | None,
    ) -> tuple[str, LeaderDecision]:
        controller = self.leaders[faction_id]
        try:
            decision = await controller.decide(
                self.world,
                feedback=feedback,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Leader {faction_id} failed to decide: {exc}"
            ) from exc
        return faction_id, decision

    def _advance_weather(self) -> None:
        for tile in self.world.tiles:
            if tile.weather == "clear":
                tile.weather_duration = 0
                continue
            if tile.weather_duration > 0:
                tile.weather_duration -= 1
            if tile.weather_duration <= 0:
                tile.weather = "clear"
                tile.weather_duration = 0

    def _record_discoveries(self) -> None:
        for faction_id, other_id in self.world.discover_factions():
            self.world.add_event(
                "discovery",
                f"{faction_id} discovered {other_id}",
                faction_id=faction_id,
            )

    def _log_tick(self, event_start: int) -> None:
        events = self.world.events[event_start:]
        self.logger.info(
            "第 %d 刻结束：events=%d paused=%s",
            self.world.tick,
            len(events),
            self.world.paused,
        )
        for event in events:
            self.logger.info(
                "第 %d 刻事件 | %s | %s | %s",
                event.tick,
                event.kind,
                event.faction_id or "world",
                event.message,
            )
        for faction_id, faction in sorted(self.world.factions.items()):
            jobs = self.world.total_jobs(faction_id)
            self.logger.info(
                (
                    "第 %d 刻阵营 | %s | 人口=%d/%d 士兵=%d 领土=%d 房屋=%d "
                    "资源=%s 职业=%s 已发现=%s 外交=%s"
                ),
                self.world.tick,
                faction_id,
                self.world.total_population(faction_id),
                self.world.population_capacity(faction_id),
                self.world.total_soldiers(faction_id),
                len(self.world.faction_tiles(faction_id)),
                self.world.total_houses(faction_id),
                faction.resources.as_dict(),
                jobs,
                sorted(faction.known_factions),
                dict(sorted(faction.diplomacy.items())),
            )
