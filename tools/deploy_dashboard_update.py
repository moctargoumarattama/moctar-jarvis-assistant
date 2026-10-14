#!/usr/bin/env python3
"""Mise à jour NOC + alias sortant Baileys, sauvegardée et vérifiée sans éditeur.

Après git pull, lancer sudo python3.11 tools/deploy_dashboard_update.py --apply.
Ne modifie aucun User/Group systemd ni service applicatif supervisé.
Sans --apply : vérifier et préparer uniquement, sans envoyer de message.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings
from tools.repair_whatsapp_contract import (Change, checked_file, http, read_env,
                                           relay_ready, systemctl, update_env, write_atomic)

MARKER = "NOC_WHATSAPP_SEND_ALIAS_V1"
MODULE = Path("/usr/local/lib/noc-integrations/baileys-send-alias.cjs")
BRIDGE = Path("/usr/local/lib/noc-integrations/baileys-noc.cjs")
GATEWAY_ENV = Path("/etc/klasora-whatsapp-noc.env")
DROP_IN = Path("/etc/systemd/system/klasora-whatsapp.service.d/99-noc-whatsapp-relay.conf")


def code_mask(source: str) -> str:
    """Garder les positions du code, masquer commentaires et chaînes JavaScript."""
    masked = list(source)
    index = 0
    while index < len(source):
        start = index
        if source.startswith('//', index):
            end = source.find('\n', index)
            index = len(source) if end < 0 else end
        elif source.startswith('/*', index):
            end = source.find('*/', index + 2)
            if end < 0:
                raise ValueError("Commentaire JavaScript incomplet")
            index = end + 2
        elif source[index] in "'\"`":
            quote = source[index]
            index += 1
            while index < len(source):
                if source[index] == '\\':
                    index += 2
                elif source[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            else:
                raise ValueError("Chaîne JavaScript incomplète")
        else:
            index += 1
            continue
        for position in range(start, min(index, len(masked))):
            if masked[position] != '\n':
                masked[position] = ' '
    return ''.join(masked)


def patch_send_alias(source: str, *, esm: bool) -> str:
    """Insérer l'alias avant la route existante, dans le même scope Express."""
    if MARKER in source:
        if source.count(MARKER) != 1:
            raise ValueError("Plusieurs alias NOC : modification ambiguë refusée")
        return source
    mask = code_mask(source)
    pattern = re.compile(r'''\b(?P<app>[A-Za-z_$][\w$]*)\s*\.\s*post\s*\(\s*(?P<quote>["'])/session/:ecoleId/send(?P=quote)\s*,''')
    matches = [match for match in pattern.finditer(source) if mask[match.start():].startswith(match.group('app'))]
    if len(matches) != 1:
        raise ValueError("Route POST /session/:ecoleId/send absente ou ambiguë : aucun remplacement automatique")
    if not re.search(r'\b(?:const|let|var)\s+sessions\b', mask):
        raise ValueError("Le Map sessions n'est pas déclaré dans server.js : fournir sa structure avant modification")
    already = re.compile(r'''\b[A-Za-z_$][\w$]*\s*\.\s*post\s*\(\s*["']/message/send["']''')
    if any(mask[match.start():].strip().startswith(source[match.start():].split('.', 1)[0].strip()) for match in already.finditer(source)):
        raise ValueError("Un alias /message/send personnalisé existe déjà : il est conservé, modification automatique refusée")
    match = matches[0]
    opening = mask.find('(', match.start())
    depth, closing = 1, opening + 1
    while closing < len(mask) and depth:
        depth += (mask[closing] == '(') - (mask[closing] == ')')
        closing += 1
    handler = mask[opening + 1:closing - 1]
    if depth or not re.search(r'\bsessions\b', handler) or re.search(r'\b(?:const|let|var|function)\s+sessions\b', handler):
        raise ValueError("La route d'envoi ne référence pas un Map sessions extérieur : patch de scope refusé")
    attach = '__nocAttachMessageSendAlias'
    loader = '' if esm else 'const ' + attach + ' = require(' + json.dumps(str(MODULE)) + ').attachMessageSendAlias;\n'
    injection = f'''/* {MARKER}: route d'origine conservée. */
{{
{loader}{attach}({match.group('app')}, {{
  sessions: () => sessions,
  gatewaySecret: process.env.NOC_GATEWAY_SECRET,
  sessionId: process.env.NOC_WHATSAPP_SESSION_ID || "1",
  logger: console
}});
}}
'''
    updated = source[:match.start()] + injection + source[match.start():]
    if esm:
        statement = 'import { attachMessageSendAlias as ' + attach + ' } from ' + json.dumps(str(MODULE)) + ';\n'
        if updated.startswith('#!'):
            index = updated.find('\n') + 1
            updated = updated[:index] + statement + updated[index:]
        else:
            updated = statement + updated
    return updated


def alias_available(secret: str) -> bool:
    """Un corps invalide teste l'alias authentifié sans envoyer de message WhatsApp."""
    request = urllib.request.Request('http://127.0.0.1:3001/message/send', data=b'{}',
        headers={'Content-Type': 'application/json', 'X-Gateway-Secret': secret})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3):
            return False
    except urllib.error.HTTPError as error:
        return error.code in {400, 422}


def gateway_environment(original: str, config: dict[str, str], secret: str) -> str:
    """Synchroniser les valeurs NOC sans remplacer les autres réglages gateway."""
    phone = re.sub(r'\D', '', config.get('ADMIN_PHONE_NUMBER', '212770010264'))
    values = {'NOC_GATEWAY_SECRET': secret,
              'NOC_WHATSAPP_SESSION_ID': config.get('NOC_WHATSAPP_SESSION_ID', '1')}
    if phone:
        values['ADMIN_PHONE_NUMBER'] = phone
    token = config.get('NOC_WHATSAPP_WEBHOOK_TOKEN', '')
    if token:
        if not re.fullmatch(r'[!-~]{1,512}', token):
            raise ValueError('NOC_WHATSAPP_WEBHOOK_TOKEN invalide ; aucune modification')
        values['NOC_WHATSAPP_WEBHOOK_TOKEN'] = token
    updated = update_env(original, values)
    if not phone:
        # Une valeur vide désactive explicitement les commandes ; ne pas garder
        # un ancien numéro ni élargir update_env aux secrets vides.
        updated = '\n'.join(line for line in updated.splitlines()
                            if not re.match(r'\s*ADMIN_PHONE_NUMBER\s*=', line)) + '\nADMIN_PHONE_NUMBER=""\n'
    return updated


def restart_and_verify(secret: str, *, require_relay: bool = False) -> None:
    # Nécessaire aussi si git pull a déjà remplacé les sources sous /opt.
    systemctl('daemon-reload')
    systemctl('restart', 'noc-dashboard.service', 'klasora-whatsapp.service')
    for _ in range(30):
        try:
            if http('/api/health').get('status') == 'ok' and alias_available(secret) and all(
                systemctl('is-active', '--quiet', unit, check=False).returncode == 0
                for unit in ('noc-dashboard.service', 'klasora-whatsapp.service')) and (
                    not require_relay or relay_ready()):
                return
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        time.sleep(0.5)
    raise ValueError('NOC, alias sortant ou relais entrant requis indisponible après redémarrage')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Lancer avec sudo python3.11 sur le Dell Ubuntu')
    source = Path(__file__).resolve().parents[1]
    app = Path('/opt/noc-dashboard')
    server = Path('/opt/klasora/app/whatsapp-server/server.js')
    env_path = Path('/etc/noc-dashboard.env')
    env_text = checked_file(env_path)
    info = env_path.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('La configuration NOC doit appartenir à root et ne pas être modifiable par groupe/autres')
    config = read_env(env_path)
    admin_phone = re.sub(r'\D', '', config.get('ADMIN_PHONE_NUMBER', '212770010264'))
    secret = config.get('NOC_GATEWAY_SECRET', Settings.__dataclass_fields__['gateway_secret'].default)
    if not re.fullmatch(r'[!-~]{1,512}', secret):
        raise ValueError('NOC_GATEWAY_SECRET vide ou invalide ; aucune modification')
    package_json = server.parent / 'package.json'
    esm = json.loads(checked_file(package_json)).get('type') == 'module' if package_json.exists() else False
    changed_server = patch_send_alias(checked_file(server), esm=esm)
    require_relay = ('NOC_GATEWAY_RELAY_V2' in changed_server and
                     bool(admin_phone and config.get('NOC_WHATSAPP_WEBHOOK_TOKEN')))
    changes = [Change(app / relative, checked_file(source / relative)) for relative in (
        'config.py', 'collector.py', 'main.py', 'cyberdefense.py', 'whatsapp.py', 'static/index.html',
        'integrations/baileys-send-alias.cjs', 'integrations/baileys-noc.cjs',
        'vps_agent.py', 'vps-agent.service', 'tools/deploy_dashboard_update.py', 'tools/repair_whatsapp_contract.py')]
    changes += [Change(server, changed_server), Change(MODULE, checked_file(source / 'integrations/baileys-send-alias.cjs')),
                Change(BRIDGE, checked_file(source / 'integrations/baileys-noc.cjs')),
                Change(env_path, update_env(env_text, {
                    'NOC_WHATSAPP_SEND_URL': 'http://127.0.0.1:3001/message/send',
                    'NOC_WHATSAPP_SEND_TO_FIELD': 'to', 'NOC_WHATSAPP_SEND_TEXT_FIELD': 'text',
                    'NOC_WHATSAPP_SEND_TIMEOUT': '10'})),
                Change(GATEWAY_ENV, gateway_environment(
                    checked_file(GATEWAY_ENV) if GATEWAY_ENV.exists() else '', config, secret), private=True),
                Change(DROP_IN, '# NOC_GATEWAY_RELAY_V2\n[Service]\nEnvironmentFile=/etc/klasora-whatsapp-noc.env\n')]
    if DROP_IN.exists() and 'NOC_GATEWAY_RELAY_V2' not in checked_file(DROP_IN):
        raise ValueError('Override gateway personnalisé conservé ; modification automatique refusée')
    for change in changes:
        if change.path.resolve() != change.path.absolute():
            raise ValueError('Destination avec lien symbolique : aucune modification')
        if change.path.exists():
            checked_file(change.path)
    with tempfile.TemporaryDirectory(prefix='noc-outgoing-check-') as temporary:
        for change in changes:
            if change.path.suffix == '.py':
                import ast
                ast.parse(change.content, filename=str(change.path))
            elif change.path.suffix in {'.js', '.cjs'}:
                suffix = '.mjs' if change.path == server and esm else '.cjs'
                staged = Path(temporary) / (change.path.stem + suffix)
                staged.write_text(change.content, encoding='utf-8')
                subprocess.run(['node', '--check', str(staged)], check=True, capture_output=True, timeout=10)
    changed = [change for change in changes if not change.path.exists() or checked_file(change.path) != change.content
               or change.private and stat.S_IMODE(change.path.stat().st_mode) != 0o600]
    print('Préparation validée :', len(changed), 'fichiers. Comptes systemd et route /session/:ecoleId/send conservés.')
    if not args.apply:
        print('Aucun fichier modifié. Utiliser --apply pour installer puis redémarrer les deux services.')
        return 0
    if changed:
        backup_root = Path('/var/backups/noc-dashboard')
        if backup_root.resolve() != backup_root:
            raise ValueError('Répertoire de sauvegarde contenant un lien symbolique')
        backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='outgoing-actions-', dir=backup_root))
        backup.chmod(0o700)
        originals = {}
        for change in changed:
            if change.path.exists():
                saved = backup / change.path.relative_to('/')
                saved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                shutil.copy2(change.path, saved)
                info = change.path.stat()
                originals[change.path] = (saved, info.st_uid, info.st_gid)
            else:
                originals[change.path] = None
        active = {unit: systemctl('is-active', '--quiet', unit, check=False).returncode == 0
                  for unit in ('noc-dashboard.service', 'klasora-whatsapp.service')}
        written = []
        try:
            for change in changed:
                write_atomic(change)
                written.append(change.path)
            gateway_user = systemctl('show', 'klasora-whatsapp.service', '--property=User', '--value').stdout.strip() or 'root'
            gateway_group = systemctl('show', 'klasora-whatsapp.service', '--property=Group', '--value').stdout.strip()
            account = ['runuser', '-u', gateway_user]
            if gateway_group:
                account += ['-g', gateway_group]
            subprocess.run(account + ['--', 'node', '--input-type=module', '-'],
                input='\n'.join('await import(' + json.dumps(str(module)) + ');' for module in (MODULE, BRIDGE)),
                text=True, capture_output=True, check=True, timeout=10)
            restart_and_verify(secret, require_relay=require_relay)
        except BaseException:
            for path in reversed(written):
                if originals[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    saved, uid, gid = originals[path]
                    shutil.copy2(saved, path)
                    os.chown(path, uid, gid)
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                systemctl('daemon-reload', check=False)
            for unit, was_active in active.items():
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    systemctl('restart' if was_active else 'stop', unit, check=False)
            raise ValueError('Mise à jour annulée : fichiers restaurés. Sauvegarde : ' + str(backup)) from None
        print('Mise à jour appliquée. Sauvegarde :', backup)
    else:
        restart_and_verify(secret, require_relay=require_relay)
    if not alias_available(secret):
        raise ValueError('Alias non disponible : vérifier le journal de la passerelle')
    print('NOC disponible, alias /message/send actif, actions web directes sans saisie de jeton.')
    print('Contrôle effectué sans envoyer de message WhatsApp. Tester ensuite « statut » depuis le téléphone admin.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        exception = sys.exc_info()[1]
        print(str(exception) if type(exception) is ValueError else
              'Contrôle ou commande système en échec ; consulter journalctl.', file=sys.stderr)
        raise SystemExit(1)
