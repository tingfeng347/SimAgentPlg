"""Route a release-note request through a local skill."""

import asyncio
import json
from pathlib import Path

from simagentplg import BaseAgent, FinishHandler, ModelConfig

SKILLS_DIR = Path(__file__).with_name("skills")


async def main() -> None:
    agent = BaseAgent(
        config=ModelConfig.from_env(),
        agent_id="release-writer",
        system_prompt=(
            "Use the selected local skill to complete the task. "
            "Return the final deliverable in the run_finish summary."
        ),
        handlers=[FinishHandler()],
        skills_dir=SKILLS_DIR,
        enable_tools=True,
    )

    try:
        result = await agent.runtime(
            task=(
                "Write release notes for SimAgentPlg 0.2.3. Changes: "
                "added FinishHandler and run_finish, added Git change "
                "reporting, and added repeated tool-call protection."
            )
        )
        report = json.loads(result or "{}")
        print(report.get("summary", result))
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
