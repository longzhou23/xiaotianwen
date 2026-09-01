from __future__ import annotations

import json
import unittest
from pathlib import Path


class ConfigSchemaCompatibilityTests(unittest.TestCase):
    def test_object_fields_define_items_for_astrbot_427(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))

        object_fields = {
            name: value
            for name, value in schema.items()
            if isinstance(value, dict) and value.get("type") == "object"
        }
        self.assertTrue(object_fields)
        for name, value in object_fields.items():
            self.assertIn("items", value, name)
            self.assertIsInstance(value["items"], dict, name)

        self.assertEqual(object_fields["route_reasoning_effort"]["items"], {})
        self.assertEqual(object_fields["route_max_output_tokens"]["items"], {})


if __name__ == "__main__":
    unittest.main()
