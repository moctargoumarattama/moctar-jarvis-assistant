import ast
import pathlib
import subprocess
import sys
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class EntrypointTests(unittest.TestCase):
    def test_main_jarvis_has_explicit_main_and_no_top_level_loop(self):
        source = (PROJECT_ROOT / "main_jarvis.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        functions = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        top_level_loops = [
            node
            for node in tree.body
            if isinstance(node, (ast.For, ast.While))
        ]

        self.assertIn("main", functions)
        self.assertEqual([], top_level_loops)

    def test_start_script_uses_unified_entrypoint(self):
        script = (PROJECT_ROOT / "start_jarvis.bat").read_text(encoding="utf-8").lower()

        self.assertIn("python main_jarvis.py", script)
        self.assertNotIn("python main.py", script)
        self.assertNotIn("python jarvisgui\\jarvismain.py", script)

    def test_main_jarvis_import_has_no_network_side_effects(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import main_jarvis; print(callable(main_jarvis.main))",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(0, result.returncode, msg=result.stderr)
        self.assertIn("True", result.stdout)


if __name__ == "__main__":
    unittest.main()
