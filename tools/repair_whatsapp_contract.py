#!/usr/bin/env python3
"""Réparer le relais décrit dans le diagnostic, sans éditeur ni changement de compte.

Usage sur le Dell, depuis les sources actualisées :
  sudo python3.11 tools/repair_whatsapp_contract.py --apply --probe

Sans --apply : préparation/contrôles uniquement. Aucun jeton n'est affiché.
Le remplacement Node exige exactement le bloc communiqué ; un autre code est
refusé. Les fichiers remplacés sont sauvegardés et restaurés si le redémarrage
ou le contrôle de disponibilité échoue. Le probe envoie uniquement « statut ».
"""
from __future__ import annotations

import argparse
import ast
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
import uuid
from dataclasses import dataclass
from pathlib import Path


MARKER = "NOC_GATEWAY_RELAY_V2"
BRIDGE = Path("/usr/local/lib/noc-integrations/baileys-noc.cjs")
GATEWAY_ENV = Path("/etc/klasora-whatsapp-noc.env")
DROP_IN = Path("/etc/systemd/system/klasora-whatsapp.service.d/99-noc-whatsapp-relay.conf")
LEGACY = r'''sock.ev.on("messages.upsert", async (m) => {
  if (m.type !== "notify") return;
  for (const msg of m.messages) {
    const rawText = msg.message?.conversation || msg.message?.extendedTextMessage?.text;
    const text = (rawText || "").trim();
    const isCommand = /^(statut|status|restart|aide|help)/i.test(text);
    if (msg.key.fromMe && !isCommand && !audioMsg) continue;
    const sender = (msg.key.remoteJid || "").split("@")[0].replace(/[^0-9]/g, "");
    if (text && isCommand) {
      const res = await fetch("http://127.0.0.1:5050/api/whatsapp/webhook", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-gateway-secret": __NOC_TOKEN__
        },
        body: JSON.stringify({ sender: "212770010264", message: text })
      });
    }
  }
});'''


def legacy_pattern() -> re.Pattern:
    """Comparer les tokens du bloc connu, avec espaces/quotes interchangeables."""
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[A-Za-z_$][\w$]*|[^\s]', LEGACY)
    parts = []
    for token in tokens:
        if token == "__NOC_TOKEN__":
            parts.append(r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|process\s*\.\s*env\s*\.\s*NOC_WHATSAPP_WEBHOOK_TOKEN)''')
        elif token.startswith('"'):
            content = re.escape(token[1:-1])
            parts.append('(?:"' + content + '"|\'' + content + '\')')
        else:
            parts.append(re.escape(token))
    return re.compile(r"\s*".join(parts))


def gateway_replacement(package: str) -> str:
    if package not in {"@whiskeysockets/baileys", "baileys"}:
        raise ValueError("Paquet Baileys non reconnu : aucun remplacement automatique")
    return f'''/* {MARKER}: un seul relais NOC, pas de réponse depuis l'accusé HTTP. */
(() => {{
  let detachNoc = () => {{}};
  let closed = false;
  sock.ev.on("connection.update", update => {{
    if (update.connection === "close") {{ closed = true; detachNoc(); }}
  }});
  (async () => {{
    const bridge = await import({json.dumps(str(BRIDGE))});
    const media = typeof downloadMediaMessage === "function"
      ? downloadMediaMessage : (await import({json.dumps(package)})).downloadMediaMessage;
    if (closed) return;
    detachNoc = (bridge.attachNocWebhook || bridge.default.attachNocWebhook)(sock, {{
      downloadMediaMessage: media,
      adminPhone: process.env.ADMIN_PHONE_NUMBER,
      sessionId: process.env.NOC_WHATSAPP_SESSION_ID || "1",
      webhookToken: process.env.NOC_WHATSAPP_WEBHOOK_TOKEN,
      baseUrl: "http://127.0.0.1:5050",
      logger: console
    }});
    console.info("NOC relay ready");
  }})().catch(() => console.warn("NOC relay unavailable: check configuration and Baileys export"));
}})();'''


def patch_gateway(source: str, package: str) -> str:
    if MARKER in source:
        if source.count(MARKER) != 1 or legacy_pattern().search(source):
            raise ValueError("Plusieurs relais NOC : remplacement ambigu refusé")
        return source
    matches = list(legacy_pattern().finditer(source))
    if len(matches) != 1:
        raise ValueError("Le bloc server.js diffère du diagnostic ou existe plusieurs fois. Aucun fichier modifié ; fournir le bloc réel")
    match = matches[0]
    return source[:match.start()] + gateway_replacement(package) + source[match.end():]


def replace_functions(target: str, source: str, names: set[str]) -> str:
    """Remplacer seulement les fonctions visées, conserver le reste du projet."""
    def find(text: str):
        found = {}
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                if node.name in found:
                    raise ValueError("Fonctions homonymes : patch ambigu refusé")
                found[node.name] = node
        if set(found) != names:
            raise ValueError("Version Python différente : fonctions requises absentes")
        return found
    old, new = find(target), find(source)
    lines, current = target.splitlines(keepends=True), source.splitlines(keepends=True)
    for name, node in sorted(old.items(), key=lambda item: item[1].lineno, reverse=True):
        replacement = new[name]
        lines[node.lineno - 1:node.end_lineno] = current[replacement.lineno - 1:replacement.end_lineno]
    result = "".join(lines)
    ast.parse(result)
    return result


def read_env(path: Path) -> dict[str, str]:
    import shlex
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        words = shlex.split(value, comments=True)
        if len(words) > 1:
            raise ValueError("Configuration : citer les valeurs contenant des espaces")
        result[key.strip()] = words[0] if words else ""
    return result


def update_env(original: str, values: dict[str, str]) -> str:
    lines = original.splitlines()
    for key, value in values.items():
        if not value or any(char in value for char in '\r\n\0"\\'):
            raise ValueError("Valeur de configuration absente ou non prise en charge")
        lines = [line for line in lines if not re.match(r"\s*" + re.escape(key) + r"\s*=", line)]
        lines.append(key + '=' + json.dumps(value))
    return "\n".join(lines) + "\n"


def checked_file(path: Path) -> str:
    if path.resolve() != path.absolute() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Fichier absent, lien symbolique ou taille inattendue : " + str(path))
    return path.read_text(encoding="utf-8")


@dataclass
class Change:
    path: Path
    content: str
    mode: int = 0o644
    private: bool = False


def write_atomic(change: Change) -> None:
    path = change.path
    if path.resolve() != path.absolute():
        raise ValueError("Destination contenant un lien symbolique : " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    attributes = path.stat() if path.exists() else None
    descriptor, temporary = tempfile.mkstemp(prefix=".noc-relay-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(change.content)
            file.flush()
            os.fsync(file.fileno())
            os.fchmod(file.fileno(), 0o600 if change.private else stat.S_IMODE(attributes.st_mode) if attributes else change.mode)
            os.fchown(file.fileno(), 0 if change.private else attributes.st_uid if attributes else 0,
                      0 if change.private else attributes.st_gid if attributes else 0)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def systemctl(*arguments: str, check: bool = True):
    return subprocess.run(["systemctl", *arguments], check=check, capture_output=True, text=True, timeout=30)


def relay_ready() -> bool:
    """Lire seulement les logs de la NOUVELLE invocation, y compris ses enfants."""
    invocation = systemctl("show", "klasora-whatsapp.service", "--property=InvocationID", "--value").stdout.strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", invocation):
        return False
    journal = subprocess.run(["journalctl", "--quiet", "--no-pager", "--output=cat", "--lines=80",
                              "_SYSTEMD_INVOCATION_ID=" + invocation],
                             capture_output=True, text=True, timeout=5, check=True)
    return "NOC relay ready" in journal.stdout and "NOC relay unavailable" not in journal.stdout


def http(path: str, payload=None, token: str = ""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Gateway-Secret"] = token
    request = urllib.request.Request("http://127.0.0.1:5050" + path,
        data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        return json.loads(response.read(65536))


def probe(token: str, phone: str, session: str) -> None:
    identifier = "diagnostic-" + uuid.uuid4().hex
    accepted = http("/api/whatsapp/webhook", {"id": identifier, "sender": phone,
                     "message": "statut", "sessionId": session}, token)
    print("Réception :", "acceptée" if accepted.get("accepted") else "refusée")
    for _ in range(50):
        state = http("/api/voice/status")
        command = state.get("last_command") or {}
        if command.get("id") == identifier and command.get("status") in {"completed", "failed"} and not state.get("busy"):
            print("Traitement :", command["status"], "· action :", command.get("action"))
            print("Livraison WhatsApp :", "confirmée HTTP" if command.get("reply_sent") else "échec : vérifier /message/send et NOC_GATEWAY_SECRET")
            return
        time.sleep(0.2)
    print("Traitement encore en file ; consulter /api/voice/status (aucun second envoi)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--probe", action="store_true", help="envoyer uniquement statut au numéro admin")
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("Lancer avec sudo python3.11 sur le Dell Ubuntu")
    app = Path("/opt/noc-dashboard")
    server = Path("/opt/klasora/app/whatsapp-server/server.js")
    config_path = Path("/etc/noc-dashboard.env")
    checked_file(config_path)
    attributes = config_path.stat()
    if attributes.st_uid != 0 or attributes.st_mode & 0o022:
        raise ValueError("La configuration NOC doit appartenir à root et ne pas être modifiable par le groupe/autres")
    config = read_env(config_path)
    token = config.get("NOC_WHATSAPP_WEBHOOK_TOKEN", "")
    phone = re.sub(r"[+ ()-]", "", config.get("ADMIN_PHONE_NUMBER", ""))
    session = config.get("NOC_WHATSAPP_SESSION_ID", "1")
    if phone != "212770010264" or not re.fullmatch(r"[!-~]{1,512}", token):
        raise ValueError("Vérifier ADMIN_PHONE_NUMBER et NOC_WHATSAPP_WEBHOOK_TOKEN dans /etc/noc-dashboard.env")
    original_server = checked_file(server)
    packages = re.findall(r'''["'](@whiskeysockets/baileys|baileys)["']''', original_server)
    if not packages:
        dependencies = json.loads(checked_file(server.parent / "package.json")).get("dependencies", {})
        packages = [name for name in ("@whiskeysockets/baileys", "baileys") if name in dependencies]
    if not packages:
        raise ValueError("Import Baileys introuvable : aucun changement automatique")
    package = packages[0]
    changes = []
    for name, functions in (("whatsapp.py", {"normalize_phone", "parse_message", "parse_intent", "_execute"}),
                            ("main.py", {"whatsapp_webhook", "voice_process"})):
        target, current = app / name, args.source_dir / name
        old_text = checked_file(target)
        required = {"AUDIO_MIME_TYPES", "RESTART_ALLOWLIST"} if name == "whatsapp.py" else {
            "AUDIO_MIME_TYPES", "parse_message", "tempfile", "require_gateway", "limited_json"}
        tree = ast.parse(old_text)
        defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        defined.update(node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                defined.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        if not required <= defined:
            raise ValueError("Version NOC antérieure ou personnalisée : installer la version complète avant ce correctif ; aucun fichier modifié")
        changes.append(Change(target, replace_functions(old_text, checked_file(current), functions)))
    changes.extend((Change(server, patch_gateway(original_server, package)),
                    Change(BRIDGE, checked_file(args.source_dir / "integrations" / "baileys-noc.cjs")),
                    Change(GATEWAY_ENV, update_env(checked_file(GATEWAY_ENV) if GATEWAY_ENV.exists() else "",
                           {"ADMIN_PHONE_NUMBER": phone, "NOC_WHATSAPP_WEBHOOK_TOKEN": token,
                            "NOC_WHATSAPP_SESSION_ID": session}), private=True),
                    Change(DROP_IN, "# NOC_GATEWAY_RELAY_V2\n[Service]\nEnvironmentFile=/etc/klasora-whatsapp-noc.env\n")))
    for change in changes:
        if change.path.exists():
            checked_file(change.path)
    if DROP_IN.exists() and MARKER not in checked_file(DROP_IN):
        raise ValueError("Override existant personnalisé : remplacement refusé")
    with tempfile.TemporaryDirectory(prefix="noc-relay-check-") as temporary:
        package_json = server.parent / "package.json"
        module_type = json.loads(checked_file(package_json)).get("type") if package_json.exists() else None
        for change in changes:
            if change.path.suffix in {".js", ".cjs"}:
                suffix = ".mjs" if change.path == server and module_type == "module" else ".cjs"
                staged = Path(temporary) / (change.path.stem + suffix)
                staged.write_text(change.content, encoding="utf-8")
                subprocess.run(["node", "--check", str(staged)], check=True, capture_output=True, timeout=10)
        checked = subprocess.run(["node", "--input-type=module", "-"], cwd=server.parent,
            input="const m=await import(" + json.dumps(package) + ");if(typeof m.downloadMediaMessage!=='function')process.exit(2);",
            text=True, capture_output=True, timeout=20)
        if checked.returncode:
            raise ValueError("La version Baileys installée n'exporte pas downloadMediaMessage ; aucune modification")
    changed = [change for change in changes if not change.path.exists() or checked_file(change.path) != change.content
               or change.private and stat.S_IMODE(change.path.stat().st_mode) != 0o600]
    print("Préparation validée :", len(changed), "fichiers à mettre à jour ; secrets et comptes systemd conservés")
    print("Le POST retourne accepted/duplicate ; la réponse finale passe par /message/send.")
    if not args.apply:
        print("Aucun fichier modifié. Relancer avec --apply, puis --probe pour le test statut.")
        return 0
    if changed:
        backup_root = Path("/var/backups/noc-dashboard")
        if backup_root.resolve() != backup_root or backup_root.is_symlink():
            raise ValueError("Répertoire de sauvegarde contenant un lien symbolique")
        backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="whatsapp-relay-", dir=backup_root))
        backup.chmod(0o700)
        original = {}
        for change in changed:
            if change.path.exists():
                saved = backup / change.path.relative_to("/")
                saved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                shutil.copy2(change.path, saved)
                info = change.path.stat()
                original[change.path] = (saved, info.st_uid, info.st_gid)
            else:
                original[change.path] = None
        active = {unit: systemctl("is-active", "--quiet", unit, check=False).returncode == 0
                  for unit in ("noc-dashboard.service", "klasora-whatsapp.service")}
        written = []
        try:
            for change in changed:
                write_atomic(change)
                written.append(change.path)
            systemctl("daemon-reload")
            systemctl("restart", "noc-dashboard.service", "klasora-whatsapp.service")
            healthy = False
            for _ in range(40):
                try:
                    healthy = http("/api/health").get("status") == "ok" and all(
                        systemctl("is-active", "--quiet", unit, check=False).returncode == 0 for unit in active) and relay_ready()
                except (OSError, ValueError, urllib.error.URLError, subprocess.SubprocessError):
                    healthy = False
                if healthy:
                    break
                time.sleep(0.5)
            if not healthy:
                raise ValueError("Service ou relais entrant non disponible après mise à jour")
        except BaseException:
            for path in reversed(written):
                if original[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    saved, uid, gid = original[path]
                    shutil.copy2(saved, path)
                    os.chown(path, uid, gid)
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                systemctl("daemon-reload", check=False)
            for unit, was_active in active.items():
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    systemctl("restart" if was_active else "stop", unit, check=False)
            raise ValueError("Mise à jour annulée : fichiers restaurés. Sauvegarde : " + str(backup)) from None
        print("Mise à jour appliquée. Sauvegarde :", backup)
        print("Relais entrant Baileys attaché (nouvelle invocation systemd confirmée)")
    state = http("/api/voice/status")
    print("Whisper :", state.get("status"), "·", state.get("reason"))
    if args.probe:
        probe(token, phone, session)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        # Les erreurs de processus et les fichiers env peuvent contenir des secrets.
        # Afficher seulement les messages contrôlés des ValueError de ce module.
        exception = sys.exc_info()[1]
        print(str(exception) if type(exception) is ValueError else
              "Contrôle ou commande système en échec ; aucun secret affiché. Consulter journalctl.", file=sys.stderr)
        raise SystemExit(1)
