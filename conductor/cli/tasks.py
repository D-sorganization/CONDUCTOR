"""`conductor tasks ...` subcommands — list, show, cancel.

LoD: the CLI never reaches into the daemon's in-memory state. It speaks only
HTTP, so it works against a local daemon (``conductor serve``) or a remote one.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

tasks_app = typer.Typer(name="tasks", help="List, show, cancel tasks on a running daemon.")
console = Console()


def _headers(token: str | None) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"} if token else {}


def _fail(message: str) -> None:
    console.print(f"[red]✗[/red] {message}")
    raise typer.Exit(1)


@tasks_app.command("list")
def list_tasks(
    status: Annotated[
        str | None,
        typer.Option("--status", help="queued | running | completed | failed | cancelled"),
    ] = None,
    kind: Annotated[str | None, typer.Option("--kind", help="prompt | issue")] = None,
    repo: Annotated[str | None, typer.Option("--repo")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 25,
    daemon_url: Annotated[
        str, typer.Option("--daemon-url", envvar="CONDUCTOR_DAEMON_URL")
    ] = "http://127.0.0.1:8080",
    auth_token: Annotated[
        str | None, typer.Option("--auth-token", envvar="CONDUCTOR_API_TOKEN")
    ] = None,
) -> None:
    """Show tasks, newest first."""
    params: list[str] = []
    if status:
        params.append(f"status={status}")
    if kind:
        params.append(f"kind={kind}")
    if repo:
        params.append(f"repo={repo}")
    params.append(f"limit={limit}")
    url = f"{daemon_url}/api/v1/tasks?{'&'.join(params)}"

    try:
        r = httpx.get(url, headers=_headers(auth_token), timeout=10.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        _fail(f"request failed: {e}")

    tasks = r.json()
    if not tasks:
        console.print("[dim]No tasks.[/dim]")
        return

    t = Table(header_style="bold cyan")
    t.add_column("ID", style="bold")
    t.add_column("Kind")
    t.add_column("Status")
    t.add_column("Target")
    t.add_column("Cost (USD)", justify="right")
    t.add_column("Created")
    for task in tasks:
        target = (
            f"{task['issue_repo']}#{task['issue_number']}"
            if task.get("issue_repo")
            else task.get("prompt", "")[:40]
        )
        t.add_row(
            task["id"],
            task["kind"],
            _colourise(task["status"]),
            target,
            f"${task.get('cost_usd', 0):.4f}",
            task["created_at"][:19],
        )
    console.print(t)


@tasks_app.command("show")
def show_task(
    task_id: Annotated[str, typer.Argument()],
    daemon_url: Annotated[
        str, typer.Option("--daemon-url", envvar="CONDUCTOR_DAEMON_URL")
    ] = "http://127.0.0.1:8080",
    auth_token: Annotated[
        str | None, typer.Option("--auth-token", envvar="CONDUCTOR_API_TOKEN")
    ] = None,
) -> None:
    """Print every field of a task."""
    try:
        r = httpx.get(
            f"{daemon_url}/api/v1/tasks/{task_id}",
            headers=_headers(auth_token),
            timeout=10.0,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        _fail(f"request failed: {e}")

    task = r.json()
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold cyan", justify="right")
    t.add_column()
    for key, value in task.items():
        if value is None:
            continue
        if isinstance(value, str) and len(value) > 200:
            value = value[:200] + "…"
        t.add_row(f"{key}:", str(value))
    console.print(t)


@tasks_app.command("cancel")
def cancel_task(
    task_id: Annotated[str, typer.Argument()],
    daemon_url: Annotated[
        str, typer.Option("--daemon-url", envvar="CONDUCTOR_DAEMON_URL")
    ] = "http://127.0.0.1:8080",
    auth_token: Annotated[
        str | None, typer.Option("--auth-token", envvar="CONDUCTOR_API_TOKEN")
    ] = None,
) -> None:
    """Cancel a queued task. Running / completed / failed tasks are unchanged."""
    try:
        r = httpx.post(
            f"{daemon_url}/api/v1/tasks/{task_id}/cancel",
            headers=_headers(auth_token),
            timeout=10.0,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        _fail(f"cancel failed: {e}")

    console.print(f"[green]✓[/green] Task {task_id} cancelled")


def _colourise(status: str) -> str:
    return {
        "queued": "[yellow]queued[/yellow]",
        "running": "[cyan]running[/cyan]",
        "completed": "[green]completed[/green]",
        "failed": "[red]failed[/red]",
        "cancelled": "[dim]cancelled[/dim]",
    }.get(status, status)
