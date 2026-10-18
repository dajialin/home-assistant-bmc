"""Regression coverage for Core-managed Bluetooth dependencies (no radio use)."""
import json
from pathlib import Path
import unittest

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "bmc_e5" / "manifest.json"


class DependencyTests(unittest.TestCase):
    def test_core_bluetooth_connector_version_is_not_overridden(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf8"))
        self.assertIn("bluetooth", manifest["dependencies"])
        requirements = [Requirement(value) for value in manifest.get("requirements", [])]
        connector = [r for r in requirements if canonicalize_name(r.name) == "bleak-retry-connector"]
        self.assertEqual(
            connector,
            [],
            "Use the declared HA Bluetooth dependency's connector instead of pinning a conflicting second version",
        )


if __name__ == "__main__":
    unittest.main()
