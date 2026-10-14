from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from agent_core import AgentCore
from assistant_plugins import PluginRegistry
from assistant_security import SecurityPolicy


def no_arguments():
    return {"type": "object", "properties": {}, "additionalProperties": False}


def target_arguments():
    return {
        "type": "object",
        "properties": {
            "target": {"type": "string", "minLength": 1},
        },
        "required": ["target"],
        "additionalProperties": False,
    }


def assistant_response(content="", tool_name=None, arguments=None):
    message = {"role": "assistant", "content": content}
    if tool_name:
        message["tool_calls"] = [
            {
                "id": f"call_{tool_name}",
                "function": {
                    "name": tool_name,
                    "arguments": {} if arguments is None else arguments,
                },
            }
        ]
    return {"message": message}


class ScriptedChat:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append({"messages": deepcopy(messages), "kwargs": deepcopy(kwargs)})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


class AgentCoreTests(unittest.TestCase):
    def setUp(self):
        self.registry = PluginRegistry()
        self.security = SecurityPolicy(
            settings={
                "require_confirmation_for_sensitive": True,
                "domain_permissions": {
                    "system": True,
                    "web": True,
                    "project": True,
                    "personal": True,
                    "iot": True,
                },
            }
        )

    def register(self, name, handler, *, domain="system", parameters=None):
        self.registry.register(
            name,
            domain,
            handler,
            description=f"Outil de test {name}",
            parameters=parameters or no_arguments(),
            ai_enabled=True,
        )

    def test_direct_response_without_tool(self):
        chat = ScriptedChat([assistant_response("Le ciel est bleu a cause de la diffusion de la lumiere.")])
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Pourquoi le ciel est bleu ?")

        self.assertEqual("final", result.status)
        self.assertIn("diffusion", result.response)
        self.assertEqual(1, len(chat.calls))

    def test_request_can_omit_all_tools_for_a_general_question(self):
        self.register("time", Mock(return_value="Il est 10:00."))
        chat = ScriptedChat([assistant_response("Le ciel diffuse davantage la lumiere bleue.")])
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Pourquoi le ciel est bleu ?", tool_names=())

        self.assertEqual("final", result.status)
        self.assertEqual([], chat.calls[0]["kwargs"]["tools"])

    def test_request_exposes_only_selected_tools(self):
        self.register("time", Mock(return_value="Il est 10:00."))
        self.register("battery", Mock(return_value="Batterie a 80 pour cent."))
        chat = ScriptedChat([assistant_response("Je peux consulter l'heure.")])
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        agent.run("Consulte l'heure.", tool_names={"time"})

        names = {
            tool["function"]["name"]
            for tool in chat.calls[0]["kwargs"]["tools"]
        }
        self.assertEqual({"time"}, names)

    def test_one_tool_call_and_result_is_reinjected(self):
        handler = Mock(return_value="Il est 14:30.")
        self.register("time", handler)
        chat = ScriptedChat(
            [
                assistant_response(tool_name="time", arguments={}),
                assistant_response("Il est actuellement 14:30."),
            ]
        )
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Quelle heure est-il ?")

        self.assertEqual("Il est actuellement 14:30.", result.response)
        handler.assert_called_once()
        second_request_messages = chat.calls[1]["messages"]
        tool_messages = [message for message in second_request_messages if message["role"] == "tool"]
        self.assertEqual(1, len(tool_messages))
        self.assertIn("Il est 14:30.", tool_messages[0]["content"])

    def test_two_successive_tool_calls(self):
        time_handler = Mock(return_value="Il est 15:00.")
        battery_handler = Mock(return_value="La batterie est a 80 pour cent.")
        self.register("time", time_handler)
        self.register("battery", battery_handler)
        chat = ScriptedChat(
            [
                assistant_response(tool_name="time", arguments={}),
                assistant_response(tool_name="battery", arguments={}),
                assistant_response("Il est 15:00 et la batterie est a 80 pour cent."),
            ]
        )
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Donne-moi l'heure puis le niveau de batterie.")

        self.assertEqual("final", result.status)
        time_handler.assert_called_once()
        battery_handler.assert_called_once()
        final_messages = chat.calls[2]["messages"]
        self.assertEqual(2, len([message for message in final_messages if message["role"] == "tool"]))

    def test_invalid_arguments_are_returned_to_model(self):
        handler = Mock(return_value="ne doit pas etre execute")
        self.register("open_app", handler, parameters=target_arguments())
        chat = ScriptedChat(
            [
                assistant_response(tool_name="open_app", arguments={}),
                assistant_response("Il manque le nom de l'application."),
            ]
        )
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Ouvre une application.")

        handler.assert_not_called()
        self.assertIn("manque", result.response)
        tool_message = [item for item in chat.calls[1]["messages"] if item["role"] == "tool"][0]
        self.assertEqual("invalid_arguments", json.loads(tool_message["content"])["status"])

    def test_unknown_tool_is_not_executed(self):
        chat = ScriptedChat(
            [
                assistant_response(tool_name="run_shell", arguments={"command": "whoami"}),
                assistant_response("Cet outil n'est pas autorise."),
            ]
        )
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Execute une commande.")

        self.assertIn("pas autorise", result.response)
        tool_message = [item for item in chat.calls[1]["messages"] if item["role"] == "tool"][0]
        self.assertEqual("unknown_tool", json.loads(tool_message["content"])["status"])

    def test_forbidden_domain_is_not_executed(self):
        handler = Mock(return_value="ne doit pas etre execute")
        self.register("open_site", handler, domain="web", parameters=target_arguments())
        denied_security = SecurityPolicy(
            settings={
                "require_confirmation_for_sensitive": True,
                "domain_permissions": {"web": False},
            }
        )
        chat = ScriptedChat(
            [
                assistant_response(tool_name="open_site", arguments={"target": "github"}),
                assistant_response("L'ouverture Web est interdite."),
            ]
        )
        agent = AgentCore(self.registry, denied_security, chat_client=chat)

        result = agent.run("Ouvre GitHub.")

        handler.assert_not_called()
        self.assertIn("interdite", result.response)
        tool_message = [item for item in chat.calls[1]["messages"] if item["role"] == "tool"][0]
        self.assertEqual("domain_denied", json.loads(tool_message["content"])["status"])

    def test_sensitive_action_requires_confirmation_then_resumes(self):
        handler = Mock(return_value="Application fermee.")
        self.register("close_app", handler, parameters=target_arguments())
        chat = ScriptedChat(
            [
                assistant_response(tool_name="close_app", arguments={"target": "edge"}),
                assistant_response("Edge a ete ferme."),
            ]
        )
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        pending = agent.run("Ferme Edge.")

        self.assertEqual("confirmation_required", pending.status)
        handler.assert_not_called()
        result = agent.resume(pending.pending_action, confirmed=True)
        self.assertEqual("Edge a ete ferme.", result.response)
        handler.assert_called_once()

    def test_refused_confirmation_never_executes_handler(self):
        handler = Mock(return_value="Application fermee.")
        self.register("close_app", handler, parameters=target_arguments())
        chat = ScriptedChat(
            [assistant_response(tool_name="close_app", arguments={"target": "edge"})]
        )
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        pending = agent.run("Ferme Edge.")
        result = agent.resume(pending.pending_action, confirmed=False)

        self.assertEqual("refused", result.status)
        self.assertEqual("Action annulee.", result.response)
        handler.assert_not_called()
        self.assertEqual(1, len(chat.calls))

    def test_agent_loop_has_a_strict_limit(self):
        handler = Mock(return_value="Il est 10:00.")
        self.register("time", handler)
        chat = ScriptedChat(
            [assistant_response(tool_name="time", arguments={}) for _ in range(3)]
        )
        agent = AgentCore(self.registry, self.security, chat_client=chat, max_steps=3)

        result = agent.run("Continue a demander l'heure.")

        self.assertEqual("max_steps", result.status)
        self.assertEqual(3, len(chat.calls))
        self.assertEqual(2, handler.call_count)

    def test_ollama_failure_uses_historical_fallback(self):
        chat = ScriptedChat([RuntimeError("ollama offline")])
        fallback = Mock(return_value="Reponse locale de secours.")
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Aide-moi.", fallback=fallback)

        self.assertEqual("fallback", result.status)
        self.assertTrue(result.used_fallback)
        self.assertEqual("Reponse locale de secours.", result.response)
        fallback.assert_called_once_with()

    def test_thinking_is_conditional(self):
        simple_chat = ScriptedChat([assistant_response("Chrome peut etre ouvert.")])
        complex_chat = ScriptedChat([assistant_response("Voici le plan.")])

        AgentCore(self.registry, self.security, chat_client=simple_chat).run("Ouvre Chrome")
        AgentCore(self.registry, self.security, chat_client=complex_chat).run(
            "Analyse mes taches, regarde mon projet actuel et dis-moi ce que je dois prioriser cet apres-midi."
        )

        self.assertFalse(simple_chat.calls[0]["kwargs"]["think"])
        self.assertTrue(complex_chat.calls[0]["kwargs"]["think"])


if __name__ == "__main__":
    unittest.main()
