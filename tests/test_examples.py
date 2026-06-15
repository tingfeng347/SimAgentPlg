import runpy
import unittest
from pathlib import Path

from simagentplg import MethodToolHandler, SkillManager

EXAMPLES_DIR = Path(__file__).parents[1] / "example"


class ExampleTests(unittest.IsolatedAsyncioTestCase):
    def test_examples_can_be_imported_without_running_main(self) -> None:
        for path in sorted(EXAMPLES_DIR.glob("*.py")):
            with self.subTest(example=path.name):
                namespace = runpy.run_path(path, run_name="example_test")
                self.assertIn("main", namespace)

    async def test_custom_tool_example_dispatches(self) -> None:
        namespace = runpy.run_path(
            EXAMPLES_DIR / "02_custom_tool.py",
            run_name="example_test",
        )
        handler = namespace["MathHandler"]()

        self.assertIsInstance(handler, MethodToolHandler)
        outcome = await handler.dispatch("add", {"left": 19.5, "right": 22.5})
        self.assertEqual(
            outcome.data,
            {"status": "success", "value": 42.0},
        )

    async def test_skill_example_discovers_local_skill(self) -> None:
        namespace = runpy.run_path(
            EXAMPLES_DIR / "06_skill.py",
            run_name="example_test",
        )
        manager = SkillManager(namespace["SKILLS_DIR"])

        await manager.discover()

        self.assertIn("release_notes", manager._skills)
        skill = manager._skills["release_notes"]
        self.assertIsNotNone(skill.template_md)
        self.assertIsNotNone(skill.sample_md)


if __name__ == "__main__":
    unittest.main()
