"""Le patch alias préserve la route existante, son scope et l'ordre avant 404."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.deploy_dashboard_update import MARKER, code_mask, gateway_environment, patch_send_alias, restart_and_verify


class OutgoingDeployTests(unittest.TestCase):
    def source(self):
        return "const sessions = new Map();\napp.post('/session/:ecoleId/send', async (req,res)=>{sessions.get(req.params.ecoleId);res.json({ok:true});});\napp.use((req,res)=>res.status(404).end());\n"

    def test_existing_route_is_preserved_and_alias_precedes_it(self):
        source = self.source()
        result = patch_send_alias(source, esm=False)
        self.assertIn(source.splitlines()[1], result)
        self.assertLess(result.index(MARKER), result.index("app.post('/session"))
        self.assertLess(result.index(MARKER), result.index('app.use('))
        self.assertIn('sessions: () => sessions', result)
        self.assertEqual(patch_send_alias(result, esm=False), result)

    def test_comments_and_strings_do_not_create_fake_routes(self):
        false_route = "app.post('/session/:ecoleId/send', handler);"
        source = '/* ' + false_route + ' */\nconst example = ' + repr(false_route) + ';\n' + self.source()
        self.assertEqual(patch_send_alias(source, esm=False).count(MARKER), 1)
        with self.assertRaises(ValueError):
            patch_send_alias('const sessions = new Map();\n/* ' + false_route + ' */', esm=False)

    def test_duplicate_or_unknown_routes_are_refused(self):
        for source in (self.source() + self.source(), 'app.post("/other", handler);',
                       self.source().replace('const sessions', 'const different'),
                       self.source() + "app.post('/message/send', custom);", MARKER + MARKER):
            with self.subTest(source=source), self.assertRaises(ValueError):
                patch_send_alias(source, esm=False)

    def test_esm_import_respects_shebang(self):
        source = '#!/usr/bin/env node\n' + self.source()
        result = patch_send_alias(source, esm=True)
        self.assertTrue(result.startswith('#!/usr/bin/env node\nimport '))
        self.assertNotIn('require(', result)

    def test_sessions_in_a_different_scope_are_not_silently_used(self):
        for source in (
            "function unrelated(){const sessions=new Map();}\napp.post('/session/:ecoleId/send',(req,res)=>res.json({ok:true}));",
            "app.post('/session/:ecoleId/send',(req,res)=>{const sessions=new Map();res.json(sessions.get('1'));});",
        ):
            with self.assertRaises(ValueError):
                patch_send_alias(source, esm=False)

    def test_restart_checks_both_services_and_the_authenticated_alias(self):
        with patch('tools.deploy_dashboard_update.systemctl', return_value=SimpleNamespace(returncode=0)) as command, \
             patch('tools.deploy_dashboard_update.http', return_value={'status': 'ok'}), \
             patch('tools.deploy_dashboard_update.alias_available', return_value=True) as alias:
            restart_and_verify('private-mock-secret')
        self.assertIn((('restart', 'noc-dashboard.service', 'klasora-whatsapp.service'), {}), command.call_args_list)
        alias.assert_called_once_with('private-mock-secret')

    def test_gateway_config_is_complete_on_creation_and_preserves_unrelated_values(self):
        config = {'ADMIN_PHONE_NUMBER': '+212 770-010-264',
                  'NOC_WHATSAPP_WEBHOOK_TOKEN': 'private-incoming-test'}
        for original in ('', 'PORT=3001\nOTHER_SECRET="preserved-test"\nADMIN_PHONE_NUMBER=old\n'):
            with self.subTest(existing=bool(original)):
                updated = gateway_environment(original, config, 'private-outgoing-test')
                self.assertIn('ADMIN_PHONE_NUMBER="212770010264"', updated)
                self.assertIn('NOC_WHATSAPP_WEBHOOK_TOKEN="private-incoming-test"', updated)
                self.assertIn('NOC_GATEWAY_SECRET="private-outgoing-test"', updated)
                if original:
                    self.assertIn('PORT=3001\nOTHER_SECRET="preserved-test"', updated)
                self.assertEqual(gateway_environment(updated, config, 'private-outgoing-test'), updated)

    def test_explicitly_empty_admin_disables_relay_without_blocking_deployment(self):
        updated = gateway_environment('ADMIN_PHONE_NUMBER=212770010264\nPORT=3001\n',
                                      {'ADMIN_PHONE_NUMBER': ''}, 'private-outgoing-test')
        self.assertEqual(updated.count('ADMIN_PHONE_NUMBER='), 1)
        self.assertIn('ADMIN_PHONE_NUMBER=""', updated)
        self.assertIn('PORT=3001', updated)

    def test_configured_incoming_relay_is_checked_after_restart(self):
        with patch('tools.deploy_dashboard_update.systemctl', return_value=SimpleNamespace(returncode=0)), \
             patch('tools.deploy_dashboard_update.http', return_value={'status': 'ok'}), \
             patch('tools.deploy_dashboard_update.alias_available', return_value=True), \
             patch('tools.deploy_dashboard_update.relay_ready', side_effect=[False, True]) as relay, \
             patch('tools.deploy_dashboard_update.time.sleep'):
            restart_and_verify('private-mock-secret', require_relay=True)
        self.assertEqual(relay.call_count, 2)

    def test_generated_alias_runs_with_lazy_sessions_and_keeps_legacy_route(self):
        project = Path(__file__).resolve().parents[1]
        module = project / 'integrations' / 'baileys-send-alias.cjs'
        for esm in (False, True):
            with self.subTest(esm=esm), tempfile.TemporaryDirectory() as temporary:
                imports = ("import http from 'node:http'; import assert from 'node:assert/strict';" if esm
                           else "const http=require('node:http');const assert=require('node:assert/strict');")
                body = r'''
let calls=0;
const stack=[];
const app={post(path,handler){stack.push({path,handler});},use(handler){stack.push({path:null,handler});}};
process.env.NOC_GATEWAY_SECRET='mock-send-private';
app.post('/session/:ecoleId/send',async(req,res)=>{calls++;await sessions.get('1').sock.sendMessage('legacy@s.whatsapp.net',{text:'legacy'});res.json({ok:true});});
const sessions=new Map([['1',{sock:{user:{id:'bot@s.whatsapp.net'},sendMessage:async(to,content)=>({key:{id:'mock-id'}})},status:'connected'}]]);
app.use((req,res)=>res.status(404).json({error:'not-found'}));
const server=http.createServer(async(request,response)=>{
 let bytes='';for await(const chunk of request)bytes+=chunk;
 const req={body:JSON.parse(bytes||'{}'),headers:request.headers,get(name){return this.headers[name.toLowerCase()];}};
 const res={status(code){response.statusCode=code;return this;},json(value){response.setHeader('Content-Type','application/json');response.end(JSON.stringify(value));return this;}};
 const entry=stack.find(item=>item.path===request.url||item.path==='/session/:ecoleId/send'&&request.url==='/session/1/send'||item.path===null);
 try{await entry.handler(req,res);}catch{response.statusCode=500;response.end('{}');}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const options={method:'POST',headers:{'Content-Type':'application/json','X-Gateway-Secret':'mock-send-private'},body:JSON.stringify({to:'212770010264',text:'Réponse NOC'})};
assert.equal((await fetch(base+'/message/send',options)).status,200);
assert.equal((await fetch(base+'/session/1/send',options)).status,200);
assert.equal(calls,1);
assert.equal((await fetch(base+'/unknown',options)).status,404);
assert.equal((await fetch(base+'/message/send',{...options,body:'{}'})).status,400);
assert.equal((await fetch(base+'/message/send',{...options,headers:{...options.headers,'X-Gateway-Secret':'wrong'}})).status,403);
server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
'''
                source = imports + '\n(async()=>{\n' + body + '\n})().catch(error=>{console.error(error);process.exit(1);});'
                with patch('tools.deploy_dashboard_update.MODULE', module.as_uri() if esm else str(module)):
                    result = patch_send_alias(source, esm=esm)
                script = Path(temporary) / ('server.mjs' if esm else 'server.cjs')
                script.write_text(result, encoding='utf-8')
                tested = subprocess.run(['node', str(script)], capture_output=True, timeout=10)
                self.assertEqual(tested.returncode, 0, tested.stderr.decode())


if __name__ == '__main__':
    unittest.main()
