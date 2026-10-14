"""Startup dependency failures must report the original error, not mask it."""
import ast
import builtins
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class AgentStartupTest(unittest.TestCase):
    def test_import_failure_uses_existing_logger_and_preserves_original_error(self):
        tree = ast.parse((ROOT / 'agent/main.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'agent')
        logger = Mock()
        namespace = {'logger': logger}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<agent-startup>', 'exec'), namespace)
        missing = ModuleNotFoundError("No module named 'custom.missing_dependency'")
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == 'custom':
                raise missing
            return original(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=guarded):
            with self.assertRaises(ModuleNotFoundError) as raised:
                namespace['agent']()
        self.assertIs(missing, raised.exception)
        logger.exception.assert_called_once_with('Agent 运行过程中发生异常')

    def test_battle_only_dependency_is_scoped_to_its_implementation(self):
        tree = ast.parse((ROOT / 'agent/custom/action/produce.py').read_text(encoding='utf-8'))
        self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.module == 'hand_identity'
                             for n in tree.body))
        battle = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ProduceCardsAuto')
        run = next(n for n in battle.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == 'hand_identity'
                            for n in ast.walk(run)))


if __name__ == '__main__':
    unittest.main()
