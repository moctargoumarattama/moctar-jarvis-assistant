import config


def _build_url(base_url, endpoint):
    return f"{base_url.rstrip('/')}{endpoint}"


def _safe_get_json(url, timeout):
    try:
        import requests
    except Exception as exc:
        return None, f"Module HTTP indisponible : {exc}"

    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json(), None
    except Exception as exc:
        return None, str(exc)


def get_iot_status():
    base_url = config.IOT_SETTINGS["base_url"]
    if not base_url:
        return "API IoT non configuree."

    payload, error = _safe_get_json(
        _build_url(base_url, config.IOT_SETTINGS["status_endpoint"]),
        config.IOT_SETTINGS["request_timeout"],
    )
    if error:
        return f"Etat capteurs indisponible : {error}"
    return f"Dernier etat capteurs : {payload}"


def get_iot_temperature():
    base_url = config.IOT_SETTINGS["base_url"]
    if not base_url:
        return "API IoT non configuree."

    payload, error = _safe_get_json(
        _build_url(base_url, config.IOT_SETTINGS["temperature_endpoint"]),
        config.IOT_SETTINGS["request_timeout"],
    )
    if error:
        return f"Temperature indisponible : {error}"
    return f"Temperature actuelle : {payload}"


def set_relay_state(relay_id, enabled):
    base_url = config.IOT_SETTINGS["base_url"]
    if not base_url:
        return "API IoT non configuree."

    try:
        import requests
    except Exception as exc:
        return f"Module HTTP indisponible : {exc}"

    try:
        response = requests.post(
            _build_url(base_url, config.IOT_SETTINGS["relay_endpoint"]),
            json={"relay_id": relay_id, "enabled": enabled},
            timeout=config.IOT_SETTINGS["request_timeout"],
        )
        response.raise_for_status()
        return f"Commande relais {relay_id} envoyee."
    except Exception as exc:
        return f"Commande relais impossible : {exc}"
