"""Visual Architecture Companion CLI."""

import ast
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from maxwell_daemon.config import load_config
from maxwell_daemon.config.loader import default_config_path

app = typer.Typer(
    help="Visual Architecture Companion: Generate Mermaid diagrams of the system architecture."
)
console = Console()


@app.command()
def fleet(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Generate a Mermaid graph of the fleet topology dynamically from config."""
    cfg = load_config(config or default_config_path())

    lines = ["graph TD", "    subgraph Control Tower", "        Daemon[Maxwell Daemon]", "    end"]

    if cfg.fleet_machines:
        lines.append("    subgraph Fleet Workers")
        for m in cfg.fleet_machines:
            lines.append(f"        {m.name}[{m.host}:{m.port}]")
        lines.append("    end")
        for m in cfg.fleet_machines:
            lines.append(f"    Daemon -->|dispatch| {m.name}")
    else:
        lines.append("    %% No fleet workers configured.")

    if cfg.repos:
        lines.append("    subgraph Repositories")
        for r in cfg.repos:
            lines.append(f"        repo_{r.name}[{r.path}]")
        lines.append("    end")
        for r in cfg.repos:
            lines.append(f"    Daemon -->|manage| repo_{r.name}")

    console.print("\n".join(lines))


@app.command()
def pipeline() -> None:
    """Generate a Mermaid sequence diagram for the Next-Gen Cognitive Pipeline."""
    # Since the pipeline is currently hardcoded in cognitive_phases.py, we render its intended flow.
    # Future iterations can introspect the CognitivePipeline class dynamically.
    lines = [
        "sequenceDiagram",
        "    participant Orchestrator",
        "    participant Strategist",
        "    participant Implementer",
        "    participant Validator",
        "",
        "    Orchestrator->>Strategist: Execute Job",
        "    Strategist-->>Orchestrator: Architectural Plan",
        "",
        "    loop Phase-Gate Validation (max_retries)",
        "        Orchestrator->>Implementer: Execute Job (Plan & Context)",
        "        Implementer-->>Orchestrator: Code Implementation",
        "        Orchestrator->>Validator: Verify Implementation",
        "        Validator-->>Orchestrator: PASS or FAIL + Feedback",
        "    end",
    ]
    console.print("\n".join(lines))


@app.command()
def classes(
    module: Annotated[str, typer.Option("--module", "-m")] = "maxwell_daemon.core",
) -> None:
    """Dynamically generate a Mermaid class diagram from the target module's AST."""
    import maxwell_daemon

    base_path = Path(maxwell_daemon.__file__).parent.parent
    target_path = base_path / module.replace(".", "/")

    if not target_path.exists():
        console.print(f"Path {target_path} does not exist.")
        raise typer.Exit(1)

    lines = ["classDiagram"]

    for py_file in target_path.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue

        try:
            with open(py_file, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except Exception:
            continue

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                lines.append(f"    class {node.name}")
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        lines.append(f"    {base.id} <|-- {node.name}")
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        # Simple method extraction
                        args = [a.arg for a in item.args.args if a.arg != "self"]
                        lines.append(f"    {node.name} : +{item.name}({', '.join(args)})")

    console.print("\n".join(lines))
