import config


SENSITIVE_INTENTS = {
    "close_app",
    "launch_project_server",
    "git_pull",
    "git_push_prepare",
    "iot_relay_on",
    "iot_relay_off",
}


class SecurityPolicy:
    def __init__(self, settings=None):
        self.settings = settings or config.SECURITY_SETTINGS

    def is_domain_allowed(self, domain):
        return bool(self.settings.get("domain_permissions", {}).get(domain, True))

    def requires_confirmation(self, intent):
        return self.settings.get("require_confirmation_for_sensitive", True) and intent in SENSITIVE_INTENTS

    @staticmethod
    def confirmation_prompt(intent):
        return f"Confirme cette action sensible: {intent}. Dis 'oui' pour confirmer ou 'non' pour annuler."
