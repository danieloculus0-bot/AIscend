import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.bean_core_bridge import core_identity, observe_with_current_bean


class BeanCoreBridgeTests(unittest.TestCase):
    def test_missing_core_is_not_silently_replaced_with_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                core_identity(folder)

    def test_real_core_identity_is_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            location = root / "bean/integration/reasoning_layer.py"
            location.parent.mkdir(parents=True)
            location.write_text("# test")
            class Command:
                stdout = "a" * 40
            with patch("src.bean_core_bridge.subprocess.run", return_value=Command()):
                self.assertEqual(core_identity(root)["sha"], "a" * 40)

    def test_bridge_never_authorizes_execution(self):
        class Command:
            stdout = '{"proposal":{"status":"observed"}}'
        with patch("src.bean_core_bridge.core_identity",
                   return_value={"sha": "a" * 40}), patch(
                       "src.bean_core_bridge.subprocess.run", return_value=Command()):
            result = observe_with_current_bean(".", "test.db", {"foo": 1})
        self.assertIs(result["trade_authorized"], False)
        self.assertEqual(result["result"]["proposal"]["status"], "observed")


if __name__ == "__main__":
    unittest.main()
