"""``zentral idp``: people and AI agents in the bundled Keycloak realm.

On the single-server deployment, ``./zentralctl user …`` / ``agent …`` run
these in a one-off container that holds only Keycloak's admin credentials.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated

import httpx
import typer

from zentral.config import get_settings
from zentral.idp.keycloak import (
    AGENT_ROLES,
    ROLE_ALIASES,
    AgentCredentials,
    IdpError,
    KeycloakAdmin,
    parse_role,
    role_alias,
)
from zentral.security.rbac import Role

idp_app = typer.Typer(
    no_args_is_help=True,
    help="People and AI agents in the bundled identity provider (Keycloak)",
)
user_app = typer.Typer(no_args_is_help=True, help="People who sign in")
agent_app = typer.Typer(no_args_is_help=True, help="AI agents (client-credentials identities)")
idp_app.add_typer(user_app, name="user")
idp_app.add_typer(agent_app, name="agent")

ROLE_HELP = "Roles: " + ", ".join(ROLE_ALIASES)


# Set by the group option, for every command: `zentral idp --wait 60 user list`.
_options = {"wait": 0.0}


@idp_app.callback()
def _group(
    wait: Annotated[
        float, typer.Option(help="Seconds to keep retrying while Keycloak is starting.")
    ] = 0,
) -> None:
    _options["wait"] = wait


@contextmanager
def _admin(wait: float) -> Iterator[KeycloakAdmin]:
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as http:
            yield KeycloakAdmin(get_settings(), http, wait_seconds=wait)
    except IdpError as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(1) from None


def _run[T](action: Callable[[KeycloakAdmin], T]) -> T:
    with _admin(_options["wait"]) as admin:
        try:
            return action(admin)
        except IdpError as exc:
            typer.echo(f"✗ {exc}", err=True)
            raise typer.Exit(1) from None


def _roles(names: list[str]) -> list[Role]:
    try:
        return list(dict.fromkeys(parse_role(n) for n in names))
    except IdpError as exc:
        raise typer.BadParameter(str(exc)) from None


def _aliases(roles: tuple[Role, ...] | list[Role]) -> str:
    return " ".join(role_alias(r) for r in roles) or "(none)"


RolesArg = Annotated[list[str], typer.Argument(help=ROLE_HELP, show_default=False)]


# --- people ------------------------------------------------------------------------
@user_app.command("add")
def user_add(email: str, name: str, roles: RolesArg) -> None:
    """Create a person. Prints a one-time password; at first sign-in they choose
    their own and register an authenticator app."""
    wanted = _roles(roles)
    password = _run(lambda kc: kc.add_user(email, name, wanted))
    typer.echo(f"Created {email} ({_aliases(wanted)}).")
    typer.echo(f"One-time password: {password}")
    typer.echo("Give it to them over a separate channel from the address.")


@user_app.command("list")
def user_list() -> None:
    """Everyone and their roles."""
    people = _run(lambda kc: kc.people())
    typer.echo(f"{'EMAIL':<40} {'ENABLED':<8} ROLES")
    for p in people:
        typer.echo(f"{p.email:<40} {'yes' if p.enabled else 'no':<8} {_aliases(p.roles)}")


@user_app.command("roles")
def user_roles(email: str, roles: RolesArg) -> None:
    """Replace someone's roles. Takes effect within five minutes (token lifetime)."""
    wanted = _roles(roles)
    _run(lambda kc: kc.set_roles(email, wanted))
    typer.echo(f"{email} now has: {_aliases(wanted)}.")


@user_app.command("disable")
def user_disable(email: str) -> None:
    """Block sign-in and end their sessions (a token already issued lasts ≤ 5 minutes)."""
    _run(lambda kc: kc.set_enabled(email, False))
    typer.echo(f"Disabled {email} and ended their sessions.")


@user_app.command("enable")
def user_enable(email: str) -> None:
    """Allow sign-in again."""
    _run(lambda kc: kc.set_enabled(email, True))
    typer.echo(f"Enabled {email}.")


@user_app.command("reset")
def user_reset(email: str) -> None:
    """Lost phone or forgotten password: end sessions, remove the authenticator
    app, and issue a new one-time password."""
    password = _run(lambda kc: kc.reset(email))
    typer.echo(f"Reset {email}: sessions ended, authenticator app removed.")
    typer.echo(f"One-time password: {password}")


# --- AI agents ---------------------------------------------------------------------
def _print_agent(creds: AgentCredentials) -> None:
    base = get_settings().public_base_url.rstrip("/")
    typer.echo(
        f"  Client ID       {creds.client_id}\n"
        f"  Client secret   {creds.secret}\n"
        f"  Token endpoint  {creds.token_endpoint}\n"
        f"  MCP endpoint    {base}/mcp\n\n"
        "  Keep the secret in the agent's secret store; it is not shown again\n"
        f"  (`agent rotate {creds.client_id.removeprefix('agent-')}` issues a new one). "
        "The agent uses the client-credentials grant."
    )


@agent_app.command("add")
def agent_add(
    name: str,
    role: Annotated[
        str,
        typer.Argument(help="One of: " + ", ".join(sorted(role_alias(r) for r in AGENT_ROLES))),
    ] = role_alias(Role.AUTOMATION),
) -> None:
    """Create an AI agent identity (default role: automation — masked, cannot reply)."""
    wanted = _roles([role])[0]
    creds = _run(lambda kc: kc.add_agent(name, wanted))
    typer.echo(f"Created agent {name} with role {wanted.value}.")
    _print_agent(creds)


@agent_app.command("list")
def agent_list() -> None:
    """All AI agents."""
    agents = _run(lambda kc: kc.agents())
    if not agents:
        typer.echo("(no agents)")
    for name, enabled in agents:
        typer.echo(f"{name:<32} {'enabled' if enabled else 'disabled'}")


@agent_app.command("rotate")
def agent_rotate(name: str) -> None:
    """Issue a new secret; the old one stops working immediately."""
    creds = _run(lambda kc: kc.rotate_agent(name))
    typer.echo(f"New secret for agent {name}:")
    _print_agent(creds)


@agent_app.command("remove")
def agent_remove(name: str) -> None:
    """Delete an agent (a token already issued lasts ≤ 5 minutes)."""
    _run(lambda kc: kc.remove_agent(name))
    typer.echo(f"Removed agent {name}.")
