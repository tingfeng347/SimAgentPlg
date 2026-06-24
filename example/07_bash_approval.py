"""Trigger BashApprovalMiddleware with a deterministic bash_run call."""

import asyncio
import json

from simagentplg import (
    BaseAgent,
    BashApprovalMiddleware,
    BashHandler,
    ModelConfig,
)


async def main() -> None:
    agent = BaseAgent(
        config=ModelConfig.from_env(),
        agent_id="bash-approval-demo",
        handlers=[BashHandler()],
        middlewares=[BashApprovalMiddleware()],
        enable_tools=True,
    )

    try:
        outcome = await agent.dispatch(
            "bash_run",
            {
                "code": "printf 'approved by human\\n' > /dev/null",
                "timeout": 5,
            },
        )
        print(json.dumps(outcome.data, ensure_ascii=False, indent=2))
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
