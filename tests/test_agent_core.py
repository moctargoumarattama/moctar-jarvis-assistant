from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent_core import AGENT_SYSTEM_PROMPT, AgentCore
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

    def test_legacy_registry_schema_alias_keeps_custom_tools_available_to_gemini(self):
        handler = Mock(return_value="12:00")
        self.register("test_clock", handler)
        legacy_registry = SimpleNamespace(
            ollama_tools=self.registry.tools_schema,
            get=self.registry.get,
            dispatch=self.registry.dispatch,
        )
        chat = ScriptedChat([
            assistant_response(tool_name="test_clock", arguments={}),
            assistant_response("Il est 12:00."),
        ])
        agent = AgentCore(legacy_registry, self.security, chat_client=chat)
        result = agent.run("Consulte l'horloge de test.", tool_names=("test_clock",))
        self.assertEqual("final", result.status)
        self.assertEqual("Il est 12:00.", result.response)
        handler.assert_called_once()
        self.assertEqual("test_clock", chat.calls[0]["kwargs"]["tools"][0]["function"]["name"])

    def test_prompt_is_compact_and_retains_operational_and_safety_rules(self):
        self.assertLess(len(AGENT_SYSTEM_PROMPT), 2000)
        for rule in (
            "l'OS personnel et copilote d'ingénierie de Moctar", "Dell Precision M4800",
            "DevOps, Linux, Solaire/Énergie, IoT, Projets", "directement dès le premier mot",
            "aucune politesse, phrase de transition ou conclusion inutile", "commandes Bash/Python",
            "2 à 4 phrases", "demande explicite de Moctar", "calcul déterministe",
            "résultat brut sans paraphrase",
            "Ne fais JAMAIS de promesse", "APPELLE DIRECTEMENT L'OUTIL au premier tour",
            "observations réelles déjà obtenues", "uniquement l'hôte mesuré",
            "confirmations requises",
        ):
            self.assertIn(rule, AGENT_SYSTEM_PROMPT)
        self.assertNotIn("[État Opérationnel]", AGENT_SYSTEM_PROMPT)
        self.assertNotIn("[Recommandations M.O.C.T.A.R]", AGENT_SYSTEM_PROMPT)

    def test_native_brain_receives_deterministic_agent_temperature(self):
        brain = Mock()
        brain.chat.return_value = {"content": "Réponse disponible.", "tool_calls": [], "finish_reason": "stop"}
        agent = AgentCore(self.registry, self.security, ai_brain_instance=brain)

        result = agent.run("Explique le fonctionnement du solaire")

        self.assertEqual("Réponse disponible.", result.response)
        self.assertEqual(0.2, brain.chat.call_args.kwargs["temperature"])

    def test_direct_response_without_tool(self):
        chat = ScriptedChat([assistant_response("Le ciel est bleu a cause de la diffusion de la lumiere.")])
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Pourquoi le ciel est bleu ?")

        self.assertEqual("final", result.status)
        self.assertIn("diffusion", result.response)
        self.assertEqual(1, len(chat.calls))

    def test_precollected_metrics_are_available_before_first_model_call(self):
        telemetry = {
            "system_telemetry": {"cpu_percent": 17.5, "memory_percent": 42.0, "disk_percent": 63.0},
            "collected_at": "2026-10-14T18:00:00+01:00",
        }
        expected = "CPU 17.5 %, RAM 42.0 %, disque 63.0 %."
        handler = Mock()
        self.register("get_system_telemetry", handler)
        chat = ScriptedChat([assistant_response(expected)])
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Quel est l'état global du système ?", context=telemetry, tool_names=())

        self.assertEqual(expected, result.response)
        self.assertEqual(1, len(chat.calls))
        system_content = chat.calls[0]["messages"][0]["content"]
        self.assertIn(json.dumps(telemetry, ensure_ascii=False), system_content)
        self.assertIn("observations réelles déjà obtenues", system_content)
        self.assertIn("uniquement l'hôte mesuré", system_content)
        self.assertIn("APPELLE DIRECTEMENT L'OUTIL au premier tour", system_content)
        self.assertIn("résultat brut sans paraphrase", system_content)
        handler.assert_not_called()

    def test_action_promise_is_retried_before_returning_a_result(self):
        handler = Mock(return_value="Il est 14:30.")
        self.register("time", handler)
        chat = ScriptedChat([
            assistant_response("Je vais vérifier l'heure."),
            assistant_response(tool_name="time", arguments={}),
            assistant_response("Il est 14:30."),
        ])
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Quelle heure est-il ?")

        self.assertEqual("final", result.status)
        self.assertEqual("Il est 14:30.", result.response)
        self.assertEqual(3, len(chat.calls))
        self.assertIn("Une promesse d'action seule", chat.calls[1]["messages"][-1]["content"])
        handler.assert_called_once()

    def test_repeated_action_promises_use_fallback_once(self):
        chat = ScriptedChat([
            assistant_response("Je commence par analyser le système."),
            assistant_response("Patientez, je consulte les métriques."),
        ])
        fallback = Mock(return_value="CPU mesuré : 17.5 %.")
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        result = agent.run("Quel est le statut du système ?", fallback=fallback)

        self.assertEqual("fallback", result.status)
        self.assertTrue(result.used_fallback)
        self.assertEqual("CPU mesuré : 17.5 %.", result.response)
        self.assertEqual(2, len(chat.calls))
        fallback.assert_called_once_with()

    def test_action_promise_does_not_exceed_the_step_limit(self):
        chat = ScriptedChat([assistant_response("Je vais vérifier les métriques.")])
        agent = AgentCore(self.registry, self.security, chat_client=chat, max_steps=1)

        result = agent.run("Quel est le statut du système ?")

        self.assertEqual("error", result.status)
        self.assertNotIn("Je vais", result.response)
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
        self.assertTrue(all(call["kwargs"]["temperature"] == 0.2 for call in chat.calls))

    def test_native_gemini_signed_parts_survive_the_tool_observation(self):
        handler = Mock(return_value="Il est 14:30.")
        self.register("time", handler)
        signed_parts = [
            {"thought": True, "text": "Internal tool selection"},
            {"thought_signature": "b3BhcXVlLXNpZ25hdHVyZQ==",
             "function_call": {"id": "signed-time", "name": "time", "args": {}}},
        ]
        brain = Mock()
        brain.chat.side_effect = [
            {"content": None, "tool_calls": [{"id": "signed-time", "name": "time", "arguments": {}}],
             "finish_reason": "tool_calls", "_gemini_parts": deepcopy(signed_parts)},
            {"content": "Il est 14:30.", "tool_calls": [], "finish_reason": "stop"},
        ]
        agent = AgentCore(self.registry, self.security, ai_brain_instance=brain)

        result = agent.run("Consulte l'heure.")

        self.assertEqual("final", result.status)
        self.assertEqual("Il est 14:30.", result.response)
        handler.assert_called_once_with({"intent": "time", "target": "", "slots": {}})
        self.assertEqual(2, brain.chat.call_count)
        second_messages = brain.chat.call_args_list[1].args[0]
        assistant = next(message for message in second_messages if message["role"] == "assistant")
        self.assertEqual(signed_parts, assistant["_gemini_parts"])
        self.assertEqual("signed-time", assistant["tool_calls"][0]["id"])
        observation = next(message for message in second_messages if message["role"] == "tool")
        self.assertEqual("signed-time", observation["tool_call_id"])
        self.assertIn("14:30", observation["content"])

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

    def test_gemini_failure_uses_historical_fallback(self):
        failure = RuntimeError("gemini offline")
        chat = ScriptedChat([failure])
        fallback = Mock(return_value="Reponse locale de secours.")
        agent = AgentCore(self.registry, self.security, chat_client=chat)

        with self.assertLogs("jarvis.agent", level="WARNING") as logs:
            result = agent.run("Aide-moi.", fallback=fallback)

        self.assertEqual("fallback", result.status)
        self.assertTrue(result.used_fallback)
        self.assertEqual("Reponse locale de secours.", result.response)
        fallback.assert_called_once_with()
        self.assertIs(failure, logs.records[-1].exc_info[1])
        self.assertIsNotNone(logs.records[-1].exc_info[2])
        self.assertIn("Traceback", logs.output[-1])

    def test_process_query_logs_model_exception_with_traceback(self):
        failure = RuntimeError("gemini SDK rejected the request")
        agent = AgentCore(self.registry, self.security, chat_client=ScriptedChat([failure]))

        with self.assertLogs("jarvis.agent", level="ERROR") as logs:
            response = agent.process_query("Explique le fonctionnement du solaire.")

        self.assertIn(str(failure), response)
        self.assertIs(failure, logs.records[-1].exc_info[1])
        self.assertIsNotNone(logs.records[-1].exc_info[2])

    def test_forced_synthesis_logs_failure_and_retains_tool_results(self):
        failure = RuntimeError("gemini synthesis failed")
        chat = ScriptedChat([
            assistant_response(tool_name="time", arguments={}),
            failure,
        ])
        agent = AgentCore(self.registry, self.security, chat_client=chat, max_steps=1)
        observation = {"ok": True, "message": "Il est 10:00."}

        with patch("agent_core.execute_tool", return_value=observation):
            with self.assertLogs("jarvis.agent", level="WARNING") as logs:
                response = agent.process_query("Quelle heure est-il ?")

        self.assertIn("Il est 10:00.", response)
        failure_logs = [record for record in logs.records if record.exc_info]
        self.assertEqual(1, len(failure_logs))
        self.assertIs(failure, failure_logs[0].exc_info[1])
        self.assertIsNotNone(failure_logs[0].exc_info[2])

    def test_resume_logs_model_failure_and_preserves_fallback(self):
        handler = Mock(return_value="Application fermee.")
        self.register("close_app", handler, parameters=target_arguments())
        failure = RuntimeError("gemini response failed after confirmation")
        chat = ScriptedChat([
            assistant_response(tool_name="close_app", arguments={"target": "edge"}),
            failure,
        ])
        fallback = Mock(return_value="Action executee, synthese indisponible.")
        agent = AgentCore(self.registry, self.security, chat_client=chat)
        pending = agent.run("Ferme Edge.")

        with self.assertLogs("jarvis.agent", level="WARNING") as logs:
            result = agent.resume(pending.pending_action, confirmed=True, fallback=fallback)

        self.assertEqual("fallback", result.status)
        self.assertTrue(result.used_fallback)
        self.assertEqual("Action executee, synthese indisponible.", result.response)
        handler.assert_called_once()
        fallback.assert_called_once_with()
        self.assertIs(failure, logs.records[-1].exc_info[1])
        self.assertIsNotNone(logs.records[-1].exc_info[2])

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
