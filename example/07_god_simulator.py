"""Run the LLM-led god sandbox MVP in a simple CLI."""

from __future__ import annotations

import argparse
import asyncio
import shlex

from simagentplg import ModelConfig
from simagentplg.game import (
    DEFAULT_FACTIONS,
    GameEngine,
    LLMLeaderController,
    create_default_world,
    render_inbox,
    render_log,
    render_map,
    render_status,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--width", type=int, default=32)
    parser.add_argument("--height", type=int, default=20)
    args = parser.parse_args()

    world = create_default_world(
        width=args.width,
        height=args.height,
        seed=args.seed,
    )
    config = ModelConfig.from_env()
    leaders = {
        faction_id: LLMLeaderController.create(
            config=config,
            faction_id=faction_id,
            world_provider=lambda world=world: world,
        )
        for faction_id in DEFAULT_FACTIONS
    }
    engine = GameEngine(world, leaders=leaders, log_ticks=True)

    print(render_map(world))
    print("Commands: map, status, tick [n], give, weather, claim, inbox, answer, log, quit")
    while True:
        try:
            raw = input("god> ").strip()
        except EOFError:
            break
        if not raw:
            continue
        try:
            should_quit = await _handle_command(engine, raw)
        except Exception as exc:
            print(f"error: {exc}")
            continue
        if should_quit:
            break


async def _handle_command(engine: GameEngine, raw: str) -> bool:
    parts = shlex.split(raw)
    command = parts[0]

    if command == "quit":
        return True
    if command == "map":
        print(render_map(engine.world))
    elif command == "status":
        print(render_status(engine.world))
    elif command == "tick":
        count = int(parts[1]) if len(parts) > 1 else 1
        await engine.tick(count)
        print(render_log(engine.world, limit=6))
        if engine.world.paused:
            print(render_status(engine.world))
    elif command == "give":
        faction_id, resource, amount = parts[1], parts[2], int(parts[3])
        engine.god.give_resource(faction_id, resource, amount)
    elif command == "weather":
        x, y, weather = int(parts[1]), int(parts[2]), parts[3]
        engine.god.set_weather(x, y, weather)
    elif command == "claim":
        faction_id, x, y = parts[1], int(parts[2]), int(parts[3])
        engine.god.claim_tile(faction_id, x, y)
    elif command == "inbox":
        print(render_inbox(engine.world))
    elif command == "answer":
        petition_id = int(parts[1])
        approve = parts[2] == "approve"
        engine.god.answer_petition(petition_id, approve)
    elif command == "log":
        limit = int(parts[1]) if len(parts) > 1 else 12
        print(render_log(engine.world, limit=limit))
    else:
        print(f"unknown command: {command}")
    return False


if __name__ == "__main__":
    asyncio.run(main())
