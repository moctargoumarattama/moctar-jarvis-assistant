"""Le correctif terminal n'écrase aucun handler non reconnu ni autre configuration."""
import ast
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.repair_whatsapp_contract import (LEGACY, MARKER, gateway_replacement,
                                           patch_gateway, relay_ready, replace_functions, update_env)


class RepairTests(unittest.TestCase):
    def legacy(self):
        return LEGACY.replace('__NOC_TOKEN__', '"private-test-token"')

    def test_replaces_only_the_reported_handler_and_preserves_other_code(self):
        prefix = "const unrelated = 42;\nfunction buildSocket(sock) {\n"
        suffix = "\nreturn sock;\n}\n"
        result = patch_gateway(prefix + self.legacy() + suffix, "@whiskeysockets/baileys")
        self.assertTrue(result.startswith(prefix))
        self.assertTrue(result.endswith(suffix))
        self.assertIn(MARKER, result)
        self.assertNotIn("private-test-token", result)
        self.assertNotIn("audioMsg", result)
        self.assertIn("process.env.NOC_WHATSAPP_WEBHOOK_TOKEN", result)
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(temporary) / "server.cjs"
            staged.write_text(result, encoding="utf-8")
            check = subprocess.run(["node", "--check", str(staged)], capture_output=True)
            self.assertEqual(check.returncode, 0, check.stderr.decode())

    def test_spacing_single_quotes_and_env_token_are_supported(self):
        for legacy in (self.legacy().replace('"', "'"),
                       LEGACY.replace('__NOC_TOKEN__', 'process.env.NOC_WHATSAPP_WEBHOOK_TOKEN'),
                       self.legacy().replace("\n", " ").replace("  ", " ")):
            self.assertIn(MARKER, patch_gateway(legacy, "baileys"))

    def test_modified_or_ambiguous_handler_is_refused(self):
        for source in (self.legacy() + self.legacy(), "sock.ev.on('messages.upsert', () => {});",
                       self.legacy().replace('const res =', 'const response ='),
                       self.legacy().replace('if (text && isCommand)', 'if (text)')):
            with self.assertRaises(ValueError):
                patch_gateway(source, "baileys")

    def test_repeated_application_is_idempotent(self):
        first = patch_gateway(self.legacy(), "baileys")
        self.assertEqual(patch_gateway(first, "baileys"), first)
        with self.assertRaises(ValueError):
            patch_gateway(first + self.legacy(), "baileys")

    def test_ast_patch_preserves_unrelated_source_and_route_decorators(self):
        old = "import os\n@route('/old')\ndef handle():\n    return 'before'\n\ndef unrelated():\n    return 42\n"
        new = "@route('/new')\ndef handle():\n    return 'after'\n"
        changed = replace_functions(old, new, {"handle"})
        self.assertIn("@route('/old')", changed)
        self.assertNotIn("@route('/new')", changed)
        self.assertIn("return 'after'", changed)
        self.assertIn("def unrelated():\n    return 42", changed)
        ast.parse(changed)

    def test_missing_or_ambiguous_python_function_is_refused(self):
        source = "def handle():\n    pass\n"
        for old in ("def other():\n    pass\n", source + source):
            with self.assertRaises(ValueError):
                replace_functions(old, source, {"handle"})

    def test_env_patch_preserves_gateway_secret_and_other_settings(self):
        original = '# Gateway\nNOC_GATEWAY_SECRET="outgoing-secret"\nPORT=3001\nADMIN_PHONE_NUMBER=old\n'
        changed = update_env(original, {"ADMIN_PHONE_NUMBER": "212770010264", "NOC_WHATSAPP_WEBHOOK_TOKEN": "incoming-secret"})
        self.assertIn('NOC_GATEWAY_SECRET="outgoing-secret"', changed)
        self.assertIn("PORT=3001", changed)
        self.assertEqual(changed.count("ADMIN_PHONE_NUMBER="), 1)
        self.assertEqual(update_env(changed, {"ADMIN_PHONE_NUMBER": "212770010264", "NOC_WHATSAPP_WEBHOOK_TOKEN": "incoming-secret"}), changed)

    def test_config_and_import_injection_are_refused(self):
        for value in ("bad\nINJECT=yes", 'bad"quote', "bad\\escape"):
            with self.assertRaises(ValueError):
                update_env("", {"TOKEN": value})
        with self.assertRaises(ValueError):
            gateway_replacement("arbitrary-module")

    def test_relay_success_requires_ready_log_from_current_invocation(self):
        invocation = 'a' * 32
        for content, expected in (("NOC relay ready", True), ("Other gateway log", False),
                                  ("NOC relay ready\nNOC relay unavailable", False)):
            with patch('tools.repair_whatsapp_contract.systemctl', return_value=SimpleNamespace(stdout=invocation)), \
                 patch('tools.repair_whatsapp_contract.subprocess.run', return_value=SimpleNamespace(stdout=content)) as journal:
                self.assertEqual(relay_ready(), expected)
            self.assertIn('_SYSTEMD_INVOCATION_ID=' + invocation, journal.call_args.args[0])

    def test_missing_invocation_never_matches_old_ready_log(self):
        with patch('tools.repair_whatsapp_contract.systemctl', return_value=SimpleNamespace(stdout='')), \
             patch('tools.repair_whatsapp_contract.subprocess.run') as journal:
            self.assertFalse(relay_ready())
            journal.assert_not_called()

    def test_generated_relay_actually_forwards_in_commonjs_and_esm(self):
        # Exécuter le code injecté, pas seulement le bridge : aucun serveur réel.
        project = Path(__file__).resolve().parents[1]
        bridge_url = (project / 'integrations' / 'baileys-noc.cjs').as_uri()
        with patch('tools.repair_whatsapp_contract.BRIDGE', bridge_url):
            wrapper = gateway_replacement('baileys')
        wrapper = wrapper.replace('"http://127.0.0.1:5050"', 'baseUrl')
        core = r'''
const assert = (value) => { if(!value)throw new Error('Generated relay contract failed'); };
const received = [];
const server = http.createServer((request,response) => {
  const chunks=[]; request.on('data',chunk=>chunks.push(chunk));
  request.on('end',()=>{ received.push(JSON.parse(Buffer.concat(chunks).toString()));response.end('{"accepted":true}'); });
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const baseUrl=`http://127.0.0.1:${server.address().port}`;
const downloadMediaMessage=async()=>Readable.from([Buffer.from('OggS')]);
const sock={ev:new EventEmitter()};
process.env.ADMIN_PHONE_NUMBER='212770010264';
process.env.NOC_WHATSAPP_WEBHOOK_TOKEN='mock-private-token';
__WRAPPER__
for(let i=0;i<100&&!sock.ev.listenerCount('messages.upsert');i++)await new Promise(resolve=>setTimeout(resolve,10));
assert(sock.ev.listenerCount('messages.upsert')===1);
sock.ev.emit('messages.upsert',{type:'notify',messages:[{key:{id:'FIXTURE_1',remoteJid:'212770010264:38@s.whatsapp.net',fromMe:false},message:{conversation:'statut'}}]});
for(let i=0;i<100&&!received.length;i++)await new Promise(resolve=>setTimeout(resolve,10));
assert(received.length===1&&received[0].data.id==='FIXTURE_1'&&received[0].data.from==='212770010264@s.whatsapp.net');
sock.ev.emit('connection.update',{connection:'close'});
assert(sock.ev.listenerCount('messages.upsert')===0);
server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
'''.replace('__WRAPPER__', wrapper)
        for suffix, imports in (('.cjs', "const http=require('node:http'); const {EventEmitter}=require('node:events'); const {Readable}=require('node:stream');"),
                                 ('.mjs', "import http from 'node:http'; import {EventEmitter} from 'node:events'; import {Readable} from 'node:stream';")):
            with self.subTest(mode=suffix), tempfile.TemporaryDirectory() as temporary:
                script = Path(temporary) / ('relay' + suffix)
                script.write_text(imports + '\n(async()=>{\n' + core + '\n})().catch(()=>process.exit(1));', encoding='utf-8')
                result = subprocess.run(['node', str(script)], capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == "__main__":
    unittest.main()
