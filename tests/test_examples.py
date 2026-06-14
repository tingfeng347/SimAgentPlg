import runpy
import unittest
from pathlib import Path

from simagentplg import MethodToolHandler

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


if __name__ == "__main__":
    unittest.main()
