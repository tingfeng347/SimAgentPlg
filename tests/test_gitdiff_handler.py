import subprocess
import tempfile
import unittest
from pathlib import Path

from simagentplg import GitDiffHandler


def run_git(cwd: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class GitDiffHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_gitdiff_defaults_to_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_git(root, "init", "-q")
            (root / "new.txt").write_text("new\n", encoding="utf-8")

            handler = GitDiffHandler(cwd=str(root))
            outcome = await handler.dispatch("run_gitdiff", {})

            self.assertFalse(outcome.should_exit)
            self.assertEqual(outcome.data["status"], "success")
            self.assertEqual(outcome.data["mode"], "status")
            self.assertEqual(outcome.data["command"], "git status --short")
            self.assertIn("?? new.txt", outcome.data["output"])

    async def test_run_gitdiff_returns_stat_and_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_git(root, "init", "-q")
            run_git(root, "config", "user.name", "Test User")
            run_git(root, "config", "user.email", "test@example.com")

            path = root / "tracked.txt"
            path.write_text("initial\n", encoding="utf-8")
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "initial")
            path.write_text("changed\n", encoding="utf-8")

            handler = GitDiffHandler(cwd=str(root))
            stat = await handler.dispatch("run_gitdiff", {"mode": "stat"})
            diff = await handler.dispatch("run_gitdiff", {"mode": "diff"})

            self.assertEqual(stat.data["status"], "success")
            self.assertEqual(stat.data["command"], "git diff --stat")
            self.assertIn("tracked.txt", stat.data["output"])
            self.assertEqual(diff.data["status"], "success")
            self.assertEqual(diff.data["command"], "git diff")
            self.assertIn("-initial", diff.data["output"])
            self.assertIn("+changed", diff.data["output"])

    async def test_run_gitdiff_rejects_unknown_mode(self) -> None:
        handler = GitDiffHandler()

        outcome = await handler.dispatch("run_gitdiff", {"mode": "summary"})

        self.assertEqual(outcome.data["status"], "error")
        self.assertIn("mode must be", outcome.data["error"])

    async def test_run_gitdiff_reports_git_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            handler = GitDiffHandler(cwd=directory)

            outcome = await handler.dispatch("run_gitdiff", {"mode": "status"})

            self.assertEqual(outcome.data["status"], "error")
            self.assertEqual(outcome.data["mode"], "status")
            self.assertEqual(outcome.data["command"], "git status --short")
            self.assertTrue(outcome.data["error"])


if __name__ == "__main__":
    unittest.main()
