"""Run the LLM-led god sandbox MVP in a browser."""

from __future__ import annotations

import argparse

import uvicorn

from simagentplg.game.web import create_engine, create_game_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--width", type=int, default=32)
    parser.add_argument("--height", type=int, default=20)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    engine = create_engine(
        seed=args.seed,
        width=args.width,
        height=args.height,
    )
    app = create_game_app(engine)
    print(f"Open http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
