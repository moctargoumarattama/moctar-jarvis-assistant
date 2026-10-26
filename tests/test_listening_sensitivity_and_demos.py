"""Tests de validation pour la sensibilité d'écoute, l'accueil démo sans latence et la robustesse vocale."""

import pytest
import main
import dynamic_attention_engine
import voice_transcribe
from actions import hud_actions


def test_hud_actions_optional_import():
    """Vérifie que les fonctions de hud_actions s'exécutent sans erreur d'import Optional."""
    res_mood = hud_actions.set_hud_mood("friendly")
    assert res_mood["success"] is True
    assert res_mood["data"]["mood"] == "friendly"

    res_view = hud_actions.switch_hud_view("orb", locate=False, mood="success")
    assert res_view["success"] is True
    assert res_view["data"]["view"] == "orb"


def test_is_direct_voice_command_for_demos_and_friends():
    """Vérifie que toutes les phrases types de démonstration devant amis sont reconnues directement."""
    demo_phrases = [
        "dis bonjour à mes amis",
        "salue tout le monde",
        "accueille nos invités",
        "je vous présente mon assistant",
        "voici mes amis",
        "fais-nous une démo",
        "montre ce que tu sais faire",
        "montre tes capacités",
        "tu nous entends ?",
        "tu peux parler un peu ?",
        "fais une blague",
        "raconte une blague",
        "tu sais faire quoi ?",
        "explique-moi ce projet",
        "donne-moi l'heure",
    ]
    for phrase in demo_phrases:
        assert main.is_direct_voice_command(phrase) is True, f"Phrase non reconnue: {phrase}"


def test_dynamic_attention_does_not_ignore_conversational_openers():
    """Vérifie que 'alors...', 'bon alors...', 'attends...' suivis d'un ordre ne sont JAMAIS classés en soliloque."""
    openers = [
        "alors présente-toi",
        "bon alors dis bonjour",
        "alors montre ce que tu sais faire",
        "attends donne l'heure",
        "alors fais une démo",
    ]
    for text in openers:
        att = dynamic_attention_engine.classify_utterance_attention(text, is_session_active=False)
        assert att["is_thinking_out_loud"] is False, f"Ne doit pas être classé en soliloque: {text}"
        assert att["is_directed"] is True, f"Doit être adressé au système: {text}"


def test_polite_merci_does_not_abruptly_disengage():
    """Un simple 'merci' ou 'merci mon ami' ne doit pas couper brutalement la session."""
    att = dynamic_attention_engine.classify_utterance_attention("merci", is_session_active=True)
    assert att.get("should_disengage") is not True

    att_friend = dynamic_attention_engine.classify_utterance_attention("merci mon ami", is_session_active=True)
    assert att_friend.get("should_disengage") is not True

    # Seule une clôture explicite coupe la session
    att_close = dynamic_attention_engine.classify_utterance_attention("merci c'est tout pour aujourd'hui", is_session_active=True)
    assert att_close.get("should_disengage") is True


def test_voice_session_duration_default_is_generous():
    """La durée de session par défaut doit être d'au moins 45s pour éviter d'ignorer les questions enchaînées."""
    assert main.VOICE_SESSION_DURATION_SECONDS >= 45.0


def test_voice_transcribe_candidates_include_opt_jarvis():
    """Vérifie que le répertoire APP_DIR est bien inclus dans les chemins de recherche Whisper."""
    candidates = [str(c[0]) for c in voice_transcribe._candidates("base")]
    app_dir_str = str(voice_transcribe.APP_DIR)
    assert any(app_dir_str in c for c in candidates)


def test_is_local_request_recognizes_lan_and_loopback():
    """Vérifie que les IPs privées (LAN/Wi-Fi) et les adresses de boucle locale sont considérées locales."""
    from starlette.requests import Request

    def make_mock_request(client_host: str) -> Request:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/voice/interact",
            "headers": [(b"host", b"localhost:5050")],
            "client": (client_host, 12345),
        }
        return Request(scope)

    assert main.is_local_request(make_mock_request("127.0.0.1")) is True
    assert main.is_local_request(make_mock_request("localhost")) is True
    assert main.is_local_request(make_mock_request("::1")) is True
    assert main.is_local_request(make_mock_request("192.168.1.50")) is True
    assert main.is_local_request(make_mock_request("10.0.0.15")) is True
    assert main.is_local_request(make_mock_request("172.20.10.5")) is True
    # Une IP publique WAN externe ne doit PAS être considérée locale
    assert main.is_local_request(make_mock_request("105.158.45.12")) is False


def test_voice_interact_on_lan_returns_200_without_password():
    """Vérifie qu'un terminal sur le réseau local LAN peut envoyer une commande comme 'cpu' sans être bloqué."""
    from fastapi.testclient import TestClient

    with TestClient(main.app, base_url="http://192.168.1.50:5050", client=("192.168.1.50", 50000)) as client_lan:
        resp = client_lan.post(
            "/api/voice/interact",
            json={"text": "cpu"},
            headers={
                "Host": "192.168.1.50:5050",
                "Origin": "http://192.168.1.50:5050",
                "Sec-Fetch-Site": "same-origin",
                "X-Voice-Session-Active": "true",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert "processeur" in data.get("reply_text", "").lower() or "système" in data.get("reply_text", "").lower()


def test_voice_interact_on_wan_requires_external_password():
    """Vérifie qu'une IP publique WAN externe exige le mot de passe Alkaline0702."""
    from fastapi.testclient import TestClient

    with TestClient(main.app, base_url="http://105.158.45.12:5050", client=("105.158.45.12", 50000)) as client_wan:
        # Sans mot de passe -> 403
        resp_locked = client_wan.post(
            "/api/voice/interact",
            json={"text": "cpu"},
            headers={
                "Host": "105.158.45.12:5050",
                "Origin": "http://105.158.45.12:5050",
                "Sec-Fetch-Site": "same-origin",
                "X-Voice-Session-Active": "true",
            },
        )
        assert resp_locked.status_code == 403

        # Avec mot de passe Alkaline0702 -> 200
        resp_unlocked = client_wan.post(
            "/api/voice/interact",
            json={"text": "cpu"},
            headers={
                "Host": "105.158.45.12:5050",
                "Origin": "http://105.158.45.12:5050",
                "Sec-Fetch-Site": "same-origin",
                "X-Voice-Session-Active": "true",
                "X-Moctar-Password": "Alkaline0702",
            },
        )
        assert resp_unlocked.status_code == 200


def test_comprehensive_direct_voice_commands_audit():
    """Audit exhaustif : vérifie que 31 commandes naturelles de la vie réelle sont reconnues 5/5 sans mot-clé."""
    test_phrases = [
        "cpu", "ram", "statut", "statut systeme", "meteo", "la meteo", "l heure",
        "batterie", "disque", "memoire", "pause", "stop", "blague", "musique",
        "la terre", "globe", "orbe", "hud", "mode noc", "aide-moi", "fais voir",
        "qui est la", "salut a tous", "bonsoir tout le monde", "tu m entends",
        "affiche la terre", "lance la musique", "ouvre le bureau",
        "c est quoi le programme", "actualites", "qu est ce que tu fais",
    ]
    for p in test_phrases:
        assert main.is_direct_voice_command(p) is True, f"Doit être direct : {p}"
        att = dynamic_attention_engine.classify_utterance_attention(p, is_session_active=False)
        assert att.get("is_directed") is True, f"Doit être adressé au système : {p}"


def test_tailscale_and_client_device_awareness():
    """Vérifie la prise en charge native des IPs Tailscale et la personnalisation selon l'appareil client."""
    from types import SimpleNamespace
    from fastapi.testclient import TestClient

    # 1. Tailscale IP reconnue comme locale
    mock_tailscale_req = SimpleNamespace(client=SimpleNamespace(host="100.68.163.14"), headers={})
    assert main.is_local_request(mock_tailscale_req) is True

    # 2. Adaptation des réponses instantanées selon le terminal client
    reply_android = main.handle_instant_local_command("tu sers à quoi", client_device="Smartphone Android")
    assert reply_android is not None
    assert "Smartphone Android" in reply_android
    assert "Dell Precision M4800" in reply_android

    reply_pc = main.handle_instant_local_command("ça va", client_device="PC Windows")
    assert reply_pc is not None
    assert "PC Windows" in reply_pc
    assert "Dell Precision M4800" in reply_pc

    # 3. Requête HTTP interact avec headers d'appareil client
    with TestClient(main.app, base_url="http://100.68.163.14:5050", client=("100.68.163.14", 50000)) as client_ts:
        resp = client_ts.post(
            "/api/voice/interact",
            json={"text": "comment tu vas"},
            headers={
                "Host": "100.68.163.14:5050",
                "Origin": "http://100.68.163.14:5050",
                "Sec-Fetch-Site": "same-origin",
                "X-Voice-Session-Active": "true",
                "X-Client-Device": "iPad Pro",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert "iPad Pro" in data.get("reply_text", "") or "Dell" in data.get("reply_text", "")



