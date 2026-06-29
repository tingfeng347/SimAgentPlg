import unittest

from simagentplg import FinishHandler


class FinishHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_finish_returns_summary_and_exits(self) -> None:
        handler = FinishHandler()

        outcome = await handler.dispatch(
            "run_finish",
            {"summary": " implemented "},
        )

        self.assertTrue(outcome.should_exit)
        self.assertEqual(outcome.data, {"summary": "implemented"})

    async def test_run_finish_requires_summary(self) -> None:
        handler = FinishHandler()

        outcome = await handler.dispatch("run_finish", {"summary": "  "})

        self.assertFalse(outcome.should_exit)
        self.assertEqual(outcome.data["status"], "error")


if __name__ == "__main__":
    unittest.main()
