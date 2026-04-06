"""Typer CLI for codevet - AI Code Vet / Auto-Fixer."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from codevet import __version__
from codevet.config import example_config_yaml, load_config
from codevet.fixer import Fixer
from codevet.models import CodevetConfig, SandboxConfig
from codevet.preflight import FitLevel, check_model_fit
from codevet.sandbox import Sandbox
from codevet.scorer import score_fix
from codevet.utils import (
    output_json,
    read_code_file,
    read_from_stdin,
    render_full_output,
)
from codevet.vetter import Vetter

app = typer.Typer(
    name="codevet",
    help="AI Code Vet / Auto-Fixer — vet AI-generated code in a Docker sandbox.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def fix(
    file_path: Annotated[
        str | None,
        typer.Argument(help="Path to the Python file to vet and fix."),
    ] = None,
    diff: Annotated[
        str | None,
        typer.Option("--diff", "-d", help="Path to a PR diff file to vet."),
    ] = None,
    config_path: Annotated[
        str | None,
        typer.Option("--config", "-c", help="Path to a codevet.yaml config file."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Ollama model (overrides config)."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Output results as JSON."),
    ] = False,
    max_iterations: Annotated[
        int | None,
        typer.Option("--max-iterations", help="Max fix iterations (overrides config)."),
    ] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", "-t", help="Sandbox timeout (overrides config)."),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option("--image", help="Docker image (overrides config)."),
    ] = None,
    skip_preflight: Annotated[
        bool,
        typer.Option(
            "--skip-preflight",
            help="Skip the llmfit hardware-fit check for the chosen model.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Print phase-by-phase progress logs to stderr.",
        ),
    ] = False,
) -> None:
    """Vet and auto-fix a Python file using local AI.

    Configuration precedence (highest wins):
        1. CLI flags
        2. ./codevet.yaml (project root)
        3. ~/.config/codevet/config.yaml (user global)
        4. Built-in defaults
    """
    start_time = time.monotonic()

    # Enable info-level logging if --verbose was passed.
    if verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="[%(asctime)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    # Load config file (may be None -> defaults)
    loaded_config = load_config(config_path)

    # CLI flags override config file
    effective = _merge_cli_over_config(
        loaded_config,
        model=model,
        image=image,
        timeout=timeout,
        max_iterations=max_iterations,
    )

    # Preflight: verify the chosen model actually fits on this machine.
    if not skip_preflight and not json_output:
        _run_preflight(effective.model)

    # Resolve input source
    code = _resolve_input(file_path, diff)
    if not code:
        console.print(
            "[red]Error:[/red] No input provided. "
            "Pass a file path, --diff, or pipe via stdin."
        )
        raise typer.Exit(code=1)

    resolved_name = file_path or "stdin.py"

    # Build sandbox config from effective settings
    project_dir = Path(file_path).parent.resolve() if file_path else Path.cwd()
    config = SandboxConfig(
        project_dir=project_dir,
        timeout_seconds=effective.timeout_seconds,
        image=effective.image,
        mem_limit=effective.mem_limit,
        pids_limit=effective.pids_limit,
        security_opt=effective.security_opt,
        cap_drop=effective.cap_drop,
    )

    try:
        # Initialize components
        vetter = Vetter(model=effective.model)
        fixer = Fixer(model=effective.model, max_iterations=effective.max_iterations)

        with Sandbox(config) as sandbox:
            # Step 1: Vet the code
            if not json_output:
                console.print(f"\n[bold blue]Vetting[/bold blue] {resolved_name}...")
            vet_result = vetter.vet(code, resolved_name, sandbox)

            # Step 2: Fix if tests fail
            fix_result = None
            fixed_code: str | None = None
            if vet_result.failed > 0 or vet_result.errors > 0:
                if not json_output:
                    console.print(
                        f"[yellow]Found issues:[/yellow] {vet_result.failed} failed, "
                        f"{vet_result.errors} errors. Auto-fixing..."
                    )
                fix_result = fixer.fix(code, vet_result, sandbox, resolved_name)
                if fix_result.success:
                    fixed_code = fix_result.fixed_code

            # Step 3: Score confidence
            final_vet = (
                fix_result.attempts[-1].test_result
                if fix_result and fix_result.attempts
                else vet_result
            )
            critique_response = _get_critique(vetter, code, fixed_code or code, final_vet)
            confidence = score_fix(final_vet, critique_response)

            duration = time.monotonic() - start_time

            # Build output
            from codevet.models import CodevetOutput

            output = CodevetOutput(
                file_path=resolved_name,
                original_code=code,
                fixed_code=fixed_code,
                confidence=confidence,
                vet_result=vet_result,
                fix_result=fix_result,
                model_used=effective.model,
                duration_seconds=round(duration, 2),
            )

            # Render
            if json_output:
                typer.echo(output_json(output))
            else:
                render_full_output(console, output)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def version() -> None:
    """Show codevet version."""
    typer.echo(f"codevet {__version__}")


@app.command()
def preflight(
    model: Annotated[
        str,
        typer.Argument(help="Ollama model name, e.g. qwen2.5-coder:7b"),
    ],
    context: Annotated[
        int,
        typer.Option("--context", "-c", help="Context window size"),
    ] = 8192,
) -> None:
    """Check whether a model will fit on this machine (via llmfit)."""
    result = check_model_fit(model, context=context)

    if not result.llmfit_installed:
        console.print(
            "[yellow]llmfit is not installed.[/yellow]\n"
            "Install it to enable hardware-fit validation:\n"
            "  [cyan]https://github.com/AlexsJones/llmfit[/cyan]"
        )
        raise typer.Exit(code=0)

    colors = {
        FitLevel.PERFECT: "bold green",
        FitLevel.GOOD: "green",
        FitLevel.MARGINAL: "yellow",
        FitLevel.TOO_TIGHT: "bold red",
        FitLevel.UNKNOWN: "dim",
    }
    style = colors[result.fit]

    console.print(f"\n[bold]Model:[/bold] {model}")
    console.print(f"[bold]Context:[/bold] {context}")
    console.print(f"[bold]Fit:[/bold] [{style}]{result.fit.value.upper()}[/{style}]")
    console.print(f"[bold]Reason:[/bold] {result.reason}\n")

    if result.should_block:
        raise typer.Exit(code=2)


@app.command("init-config")
def init_config(
    path: Annotated[
        str,
        typer.Option(
            "--path",
            "-p",
            help="Where to write the config file.",
        ),
    ] = "codevet.yaml",
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite if exists."),
    ] = False,
) -> None:
    """Create a commented example ``codevet.yaml`` in the current directory."""
    target = Path(path).resolve()

    if target.exists() and not force:
        console.print(
            f"[yellow]File exists:[/yellow] {target}\n"
            "Use --force to overwrite."
        )
        raise typer.Exit(code=1)

    target.write_text(example_config_yaml(), encoding="utf-8")
    console.print(f"[green]Created[/green] {target}")
    console.print(
        "\nEdit this file to customize the model, Docker image, "
        "timeout, and other settings."
    )


def _merge_cli_over_config(
    cfg: CodevetConfig,
    *,
    model: str | None,
    image: str | None,
    timeout: int | None,
    max_iterations: int | None,
) -> CodevetConfig:
    """Return a new CodevetConfig with CLI flags overriding the file values.

    Any ``None`` CLI flag means 'not set on command line — use config value'.
    """
    overrides: dict[str, object] = {}
    if model is not None:
        overrides["model"] = model
    if image is not None:
        overrides["image"] = image
    if timeout is not None:
        overrides["timeout_seconds"] = timeout
    if max_iterations is not None:
        overrides["max_iterations"] = max_iterations

    if not overrides:
        return cfg

    return cfg.model_copy(update=overrides)


def _resolve_input(file_path: str | None, diff_path: str | None) -> str:
    """Resolve input from file path, diff path, or stdin."""
    if file_path:
        return read_code_file(file_path)
    if diff_path:
        return read_code_file(diff_path)
    return read_from_stdin()


def _run_preflight(model: str) -> None:
    """Run the llmfit hardware-fit check and react to the result.

    The llmfit binary is auto-installed on first use (downloaded from the
    upstream GitHub release). Behavior by fit level:
      - PERFECT / GOOD:  print green check, continue.
      - MARGINAL:        print yellow warning, continue.
      - TOO_TIGHT:       HARD ERROR with explanation, exit code 2.
      - UNKNOWN:         print dim note, continue (classification failed
                         but we don't want to block on llmfit hiccups).
    """
    console.print(f"[dim]Preflight: checking whether '{model}' fits...[/dim]")
    result = check_model_fit(model)

    if result.should_block:
        required = (
            f"\n[dim]Recommended: ~{result.required_ram_gb:.1f} GB RAM[/dim]"
            if result.required_ram_gb
            else ""
        )
        console.print(
            f"\n[bold red]X Preflight failed:[/bold red] "
            f"model '{model}' will not fit on this machine.\n"
            f"[red]{result.reason}[/red]{required}\n\n"
            f"[yellow]What to do:[/yellow]\n"
            f"  -Pick a smaller model "
            f"(e.g. qwen2.5-coder:7b instead of :14b)\n"
            f"  -Run 'codevet preflight <model>' to check alternatives\n"
            f"  -Or bypass this check with [cyan]--skip-preflight[/cyan] "
            f"(at your own risk - may OOM)\n"
        )
        raise typer.Exit(code=2)

    if result.should_warn:
        console.print(f"[yellow]!  Preflight warning:[/yellow] {result.reason}")
        return

    if result.fit == FitLevel.UNKNOWN:
        if not result.llmfit_installed:
            console.print(f"[dim]i  {result.reason}[/dim]")
        else:
            console.print(f"[yellow]!  Preflight:[/yellow] {result.reason}")
        return

    console.print(f"[green]OK Preflight:[/green] {result.reason}")


def _get_critique(vetter: Vetter, original: str, fixed: str, vet_result: object) -> str:
    """Get LLM critique response for confidence scoring."""
    from codevet.prompts import build_critique_prompt

    try:
        system_prompt, user_prompt = build_critique_prompt(
            original_code=original,
            fixed_code=fixed,
            test_output=str(vet_result),
        )
        client = vetter._get_client()
        response = client.chat(
            model=vetter.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={
                # Tuned for critique: very low temperature for deterministic,
                # focused self-evaluation.
                "temperature": 0.15,
                "top_p": 0.7,
                "top_k": 30,
            },
        )
        return str(response["message"]["content"])
    except Exception:
        return '{"score": 50, "reasoning": "Critique unavailable"}'


if __name__ == "__main__":
    app()
