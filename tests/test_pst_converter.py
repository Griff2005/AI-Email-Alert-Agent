import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class PstConverterTests(unittest.TestCase):
    def test_module_import_does_not_exit_when_pypff_missing(self):
        module = importlib.import_module("pst_to_backlog_json")

        self.assertTrue(hasattr(module, "convert"))

    def test_missing_pypff_fails_only_when_loader_runs(self):
        module = importlib.import_module("pst_to_backlog_json")

        with patch.dict(sys.modules, {"pypff": None}):
            with self.assertRaisesRegex(RuntimeError, "libpff-python is not installed"):
                module._load_pypff()


if __name__ == "__main__":
    unittest.main()
