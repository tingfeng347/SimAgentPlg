import subprocess
import tempfile
import unittest
from pathlib import Path

from simagentplg import FinishHandler


def run_git(cwd: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class FinishHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_finish_reports_task_git_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_git(root, "init", "-q")
            run_git(root, "config", "user.name", "Test User")
            run_git(root, "config", "user.email", "test@example.com")

            for name in ("clean.txt", "delete.txt", "dirty.txt"):
                (root / name).write_text("initial\n", encoding="utf-8")
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "initial")

            (root / "dirty.txt").write_text("before\n", encoding="utf-8")
            (root / "old-untracked.txt").write_text(
                "unchanged\n",
                encoding="utf-8",
            )

            handler = FinishHandler(cwd=str(root))
            await handler.on_task_start()

            (root / "clean.txt").write_text("changed\n", encoding="utf-8")
            (root / "delete.txt").unlink()
            (root / "dirty.txt").write_text(
                "changed again\n",
                encoding="utf-8",
            )
            (root / "new.txt").write_text("new\n", encoding="utf-8")

            outcome = await handler.dispatch(
                "run_finish",
                {"summary": "implemented"},
            )

            self.assertTrue(outcome.should_exit)
            self.assertEqual(outcome.data["summary"], "implemented")
            self.assertEqual(
                outcome.data["changes"]["added"],
                ["new.txt"],
            )
            self.assertEqual(
                outcome.data["changes"]["modified"],
                ["clean.txt", "dirty.txt"],
            )
            self.assertEqual(
                outcome.data["changes"]["deleted"],
                ["delete.txt"],
            )
            self.assertNotIn(
                "old-untracked.txt",
                outcome.data["changes"]["added"],
            )

    async def test_run_finish_detects_changes_to_preexisting_dirty_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_git(root, "init", "-q")
            run_git(root, "config", "user.name", "Test User")
            run_git(root, "config", "user.email", "test@example.com")
            path = root / "tracked.txt"
            path.write_text("initial\n", encoding="utf-8")
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "initial")

            path.write_text("dirty before task\n", encoding="utf-8")
            handler = FinishHandler(cwd=str(root))
            await handler.on_task_start()
            path.write_text("dirty during task\n", encoding="utf-8")

            outcome = await handler.dispatch(
                "run_finish",
                {"summary": "updated"},
            )

            self.assertEqual(
                outcome.data["changes"]["modified"],
                ["tracked.txt"],
            )

    async def test_run_finish_works_outside_git_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            handler = FinishHandler(cwd=directory)
            await handler.on_task_start()

            outcome = await handler.dispatch(
                "run_finish",
                {"summary": "done"},
            )

            self.assertTrue(outcome.should_exit)
            self.assertFalse(outcome.data["changes"]["available"])
            self.assertIn("reason", outcome.data["changes"])
            self.assertEqual(outcome.data["changes"]["added"], [])

    async def test_run_finish_requires_summary(self) -> None:
        handler = FinishHandler()

        outcome = await handler.dispatch("run_finish", {"summary": "  "})

        self.assertFalse(outcome.should_exit)
        self.assertEqual(outcome.data["status"], "error")


if __name__ == "__main__":
    unittest.main()
