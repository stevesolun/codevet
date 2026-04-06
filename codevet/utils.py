"""Rich output formatting and file utilities."""
from __future__ import annotations

import difflib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import Console

    from codevet.models import CodevetOutput, ConfidenceScore


def read_code_file(path: str | Path) -> str:
    """Read a file and return its contents as a string.

    Args:
        path: Filesystem path to the code file.

    Returns:
        The full text content of the file.

    Raises:
        FileNotFoundError: If the file does not exist at *path*.
    """
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Code file not found: {resolved}")
    return resolved.read_text(encoding="utf-8")


def read_from_stdin() -> str:
    """Read piped input from stdin.

    Returns:
        The stdin content as a string, or an empty string when stdin is
        a TTY (no piped input).
    """
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def format_diff(original: str, fixed: str, file_name: str = "code.py") -> str:
    """Generate a unified diff between *original* and *fixed*.

    Args:
        original: The original source text.
        fixed: The modified source text.
        file_name: Label used in the diff header lines.

    Returns:
        A unified-diff string (empty when the texts are identical).
    """
    original_lines = original.splitlines(keepends=True)
    fixed_lines = fixed.splitlines(keepends=True)

    diff_lines = difflib.unified_diff(
        original_lines,
        fixed_lines,
        fromfile=f"a/{file_name}",
        tofile=f"b/{file_name}",
    )
    return "".join(diff_lines)


def render_diff(
    console: Console,
    original: str,
    fixed: str,
    file_name: str,
) -> None:
    """Render a colored unified diff to a Rich console.

    Additions are printed in green, deletions in red, and diff header
    lines in bold.
    """
    diff_text = format_diff(original, fixed, file_name)
    if not diff_text:
        console.print("[dim]No changes.[/dim]")
        return

    output = Text()
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("+++") or line.startswith("---"):
            output.append(line, style="bold")
        elif line.startswith("@@"):
            output.append(line, style="cyan")
        elif line.startswith("+"):
            output.append(line, style="green")
        elif line.startswith("-"):
            output.append(line, style="red")
        else:
            output.append(line)

    console.print(
        Panel(output, title=f"Diff: {file_name}", border_style="dim")
    )


_GRADE_STYLES: dict[str, str] = {
    "A": "bold green",
    "B": "bold blue",
    "C": "bold yellow",
    "D": "bold dark_orange",
    "F": "bold red",
}


def render_confidence_badge(console: Console, score: ConfidenceScore) -> None:
    """Render a confidence badge as a Rich Panel.

    Displays ``[grade] score/100`` with color coding:
    A = green, B = blue, C = yellow, D = orange, F = red.
    """
    style = _GRADE_STYLES.get(score.grade, "bold white")

    badge = Text()
    badge.append(f"[{score.grade}] ", style=style)
    badge.append(f"{score.score}/100", style=style)

    console.print(
        Panel(badge, title="Confidence", border_style=style, expand=False)
    )


def render_explanation(console: Console, explanation: str) -> None:
    """Render an explanation string as Rich Markdown."""
    if not explanation:
        return
    console.print(
        Panel(Markdown(explanation), title="Explanation", border_style="dim")
    )


def render_full_output(console: Console, output: CodevetOutput) -> None:
    """Render the complete codevet pipeline output.

    Displays, in order:
    1. Header with file path and model info
    2. Diff (if fixed code differs from original)
    3. Confidence badge
    4. Explanation
    """
    # -- header ---------------------------------------------------------
    console.rule(f"[bold]codevet: {output.file_path}[/bold]")
    console.print(f"[dim]Model: {output.model_used}[/dim]")
    console.print()

    # -- diff -----------------------------------------------------------
    if output.fixed_code is not None and output.fixed_code != output.original_code:
        render_diff(console, output.original_code, output.fixed_code, output.file_path)
        console.print()

    # -- confidence badge -----------------------------------------------
    render_confidence_badge(console, output.confidence)
    console.print()

    # -- explanation ----------------------------------------------------
    render_explanation(console, output.confidence.explanation)


def output_json(output: CodevetOutput) -> str:
    """Serialize a ``CodevetOutput`` to a JSON string.

    Uses Pydantic's ``model_dump_json`` for correct serialization of
    all nested models.
    """
    return output.model_dump_json(indent=2)
