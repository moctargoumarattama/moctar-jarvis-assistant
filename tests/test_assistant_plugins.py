import unittest

from unittest.mock import Mock

from assistant_plugins import PluginRegistry, build_default_registry


class PluginRegistryTests(unittest.TestCase):
    def test_register_and_dispatch(self):
        registry = PluginRegistry()

        registry.register("demo", "system", lambda payload: f"ok:{payload['intent']}")
        response = registry.dispatch({"intent": "demo"})

        self.assertEqual("ok:demo", response)
        self.assertEqual("system", registry.get("demo").domain)

    def test_default_registry_exposes_only_the_safe_agent_tools(self):
        registry = build_default_registry(Mock())

        self.assertEqual(
            {
                "time",
                "date",
                "battery",
                "open_app",
                "open_folder",
                "open_site",
                "search_google",
                "search_youtube",
                "git_status",
                "git_log",
                "list_todos",
                "search_personal",
                "summarize_file",
                "iot_status",
                "iot_temperature",
            },
            {route.name for route in registry.ai_routes()},
        )
        open_app = registry.get("open_app")
        schema = open_app.as_ollama_tool()
        self.assertTrue(open_app.ai_enabled)
        self.assertEqual("function", schema["type"])
        self.assertEqual("open_app", schema["function"]["name"])
        self.assertIn("target", schema["function"]["parameters"]["required"])
        open_folder_choices = registry.get("open_folder").parameters["properties"]["target"]["enum"]
        self.assertIn("noor_express", open_folder_choices)


if __name__ == "__main__":
    unittest.main()
