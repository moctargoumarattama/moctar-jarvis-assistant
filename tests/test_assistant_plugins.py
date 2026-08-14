import unittest

from assistant_plugins import PluginRegistry


class PluginRegistryTests(unittest.TestCase):
    def test_register_and_dispatch(self):
        registry = PluginRegistry()

        registry.register("demo", "system", lambda payload: f"ok:{payload['intent']}")
        response = registry.dispatch({"intent": "demo"})

        self.assertEqual("ok:demo", response)
        self.assertEqual("system", registry.get("demo").domain)


if __name__ == "__main__":
    unittest.main()
