"""External-process model providers using the official subscription CLIs.

No tokens are imported into Hermes; the shared provider seam routes main and
auxiliary requests through the same CLI client. Login stays native: ``hermes auth
add|status|logout <engine>-cli`` runs the CLI's own commands in the user's terminal.
"""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

# Native account commands per action; None = the CLI has no non-interactive form for it.
_NATIVE_AUTH = {
    "codex": {"add": ("login", "--device-auth"), "status": ("login", "status"), "logout": ("logout",)},
    "claude": {"add": ("auth", "login", "--claudeai"), "status": ("auth", "status"), "logout": ("auth", "logout")},
    # Gemini signs in from its own TUI (choose "Sign in with Google", then /quit).
    "gemini": {"add": (), "status": None, "logout": None},
}
_GEMINI_HELP = ("Gemini CLI signs in from its own screen: choose \"Sign in with Google\" (or run /auth), "
                "finish the browser step, then /quit. API-key and Vertex modes are refused by Hermes.")


def _gemini_status(env: dict) -> int:
    from agent.cli_subscription_process import validate_gemini_auth

    try:
        validate_gemini_auth(env)
    except ValueError as exc:
        print(f"gemini-cli: {exc}")
        return 1
    print("gemini-cli: Sign in with Google is selected (credentials stay inside Gemini CLI).")
    return 0


def _auth_handler(name: str, engine: str):
    def handle(action: str, _args: Any) -> bool:
        import subprocess

        from agent.cli_subscription_process import subscription_env
        from hermes_cli.auth import resolve_external_process_provider_credentials

        if action not in _NATIVE_AUTH[engine]:
            print(f"{name}: credentials are owned by the {engine} CLI; there is nothing for Hermes to {action}.")
            return True
        env = subscription_env()
        if engine == "gemini" and action == "status":
            raise SystemExit(_gemini_status(env))
        native = _NATIVE_AUTH[engine][action]
        if native is None:
            print(_GEMINI_HELP)
            return True
        if engine == "gemini":
            print(_GEMINI_HELP)
        command = resolve_external_process_provider_credentials(name)["command"]
        # Inherit the terminal: device codes and browser prompts belong to the user, not Hermes logs.
        code = subprocess.call([command, *native], env=env)  # health: allow HX006 -- interactive native login runs until the user finishes it
        if code:
            raise SystemExit(code)
        return True

    return handle


class SubscriptionCLIProfile(ProviderProfile):
    def create_client(self, **kwargs):
        from agent.cli_subscription_client import SubscriptionCLIClient

        return SubscriptionCLIClient(engine=self.name.removesuffix("-cli"), **kwargs)


for engine, label in (("codex", "Codex CLI"), ("gemini", "Gemini CLI"), ("claude", "Claude Code CLI")):
    register_provider(SubscriptionCLIProfile(
        name=engine + "-cli", display_name=label + " (subscription)",
        description="Hermes tools and history; inference through the signed-in official CLI",
        auth_type="external_process", process_command=engine, base_url="cli://" + engine,
        supports_model_listing=False, supports_health_check=False, supports_vision=False,
        fallback_models=("default",), default_aux_model="default",
        auth_handler=_auth_handler(engine + "-cli", engine),
    ))
