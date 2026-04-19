"""Conductor CLI — the primary user-facing entrypoint."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from conductor import __version__
from conductor.backends import Message, MessageRole, registry
from conductor.config import (
    AgentConfig,
    APIConfig,
    BackendConfig,
    ConductorConfig,
    FleetConfig,
    load_config,
    save_config,
)
from conductor.config.loader import default_config_path
from conductor.core import BackendRouter

app = typer.Typer(
    name="conductor",
    help="Multi-backend autonomous code agent orchestrator.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
console = Console()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V"),
) -> None:
    if version:
        console.print(f"conductor {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@app.command()
def init(
    path: Annotated[Path | None, typer.Option("--path", "-p", help="Config path")] = None,
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
) -> None:
    """Create a starter conductor.yaml."""
    target = path or default_config_path()
    if target.exists() and not force:
        console.print(f"[yellow]Config already exists at {target}[/yellow]")
        console.print("Pass --force to overwrite.")
        raise typer.Exit(1)

    cfg = ConductorConfig(
        backends={
            "claude": BackendConfig(
                type="claude",
                model="claude-sonnet-4-6",
                api_key="${ANTHROPIC_API_KEY}",
            ),
            "ollama": BackendConfig(
                type="ollama",
                model="llama3.1",
                base_url="http://localhost:11434",
            ),
        },
        agent=AgentConfig(default_backend="claude"),
        fleet=FleetConfig(),
        api=APIConfig(enabled=True, host="127.0.0.1", port=8080),
    )
    written = save_config(cfg, target)
    console.print(
        Panel.fit(
            f"[green]✓[/green] Wrote starter config to [bold]{written}[/bold]\n"
            f"Edit it, then run [bold]conductor status[/bold] to verify.",
            title="Conductor initialized",
            border_style="green",
        )
    )


@app.command()
def status(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Show configured backends, repos, and fleet members."""
    try:
        cfg = load_config(config)
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1) from None

    table = Table(title="Backends", show_header=True, header_style="bold cyan")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Model")
    table.add_column("Enabled")
    for name, b in cfg.backends.items():
        mark = "[green]✓[/green]" if b.enabled else "[red]✗[/red]"
        table.add_row(name, b.type, b.model, mark)
    console.print(table)

    if cfg.repos:
        rt = Table(title="Repos", show_header=True, header_style="bold cyan")
        rt.add_column("Name")
        rt.add_column("Path")
        rt.add_column("Slots")
        rt.add_column("Backend")
        for r in cfg.repos:
            rt.add_row(r.name, str(r.path), str(r.slots), r.backend or "(default)")
        console.print(rt)

    console.print(f"\nDefault backend: [bold]{cfg.agent.default_backend}[/bold]")
    console.print(f"Available adapters: {', '.join(registry.available())}")


@app.command()
def backends() -> None:
    """List all registered backend adapters."""
    table = Table(title="Registered Backend Adapters", header_style="bold cyan")
    table.add_column("Adapter")
    table.add_column("Module")
    for name in registry.available():
        table.add_row(name, f"conductor.backends.{name}")
    console.print(table)


@app.command()
def health(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Probe each configured backend for reachability."""
    cfg = load_config(config)
    router = BackendRouter(cfg)

    async def _run() -> int:
        failures = 0
        table = Table(title="Backend Health", header_style="bold cyan")
        table.add_column("Name")
        table.add_column("Status")
        for name in router.available_backends():
            try:
                decision = router.route(backend_override=name)
                ok = await decision.backend.health_check()
                table.add_row(name, "[green]healthy[/green]" if ok else "[red]unreachable[/red]")
                if not ok:
                    failures += 1
            except Exception as e:
                table.add_row(name, f"[red]error: {e}[/red]")
                failures += 1
        console.print(table)
        return failures

    failures = asyncio.run(_run())
    if failures:
        raise typer.Exit(1)


@app.command()
def ask(
    prompt: Annotated[str, typer.Argument(help="Prompt to send to the backend")],
    backend: Annotated[str | None, typer.Option("--backend", "-b")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    stream: Annotated[bool, typer.Option("--stream/--no-stream")] = True,
) -> None:
    """Send a one-shot prompt to the configured backend (for smoke-testing)."""
    cfg = load_config(config)
    router = BackendRouter(cfg)
    decision = router.route(backend_override=backend, model_override=model)

    console.print(
        f"[dim]→ routing to [bold]{decision.backend_name}[/bold] "
        f"({decision.model}) — {decision.reason}[/dim]"
    )

    async def _run() -> None:
        msgs = [Message(role=MessageRole.USER, content=prompt)]
        if stream:
            async for chunk in decision.backend.stream(msgs, model=decision.model):
                console.print(chunk, end="", soft_wrap=True)
            console.print()
        else:
            resp = await decision.backend.complete(msgs, model=decision.model)
            console.print(resp.content)
            cost = decision.backend.estimate_cost(resp.usage, decision.model)
            console.print(f"\n[dim]tokens: {resp.usage.total_tokens}  cost: ${cost:.4f}[/dim]")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(130)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
