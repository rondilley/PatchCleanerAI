"""Rich terminal UI and argparse CLI for PatchClean AI."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from patchclean.actions import delete_files, is_admin, move_files
from patchclean.analyzer import analyze
from patchclean.config import load_keys
from patchclean.models import Classification, InstallerFile, ScanResult
from patchclean.scanner import INSTALLER_DIR, scan_installer_dir

console = Console()

CLASSIFICATION_COLORS = {
    Classification.KNOWN: "green",
    Classification.ORPHANED: "red",
    Classification.UNKNOWN: "yellow",
}


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _build_table(files: list[InstallerFile]) -> Table:
    table = Table(title="Windows Installer Files", show_lines=False)
    table.add_column("File", style="cyan", max_width=40)
    table.add_column("Type", justify="center")
    table.add_column("Size", justify="right")
    table.add_column("Classification", justify="center")
    table.add_column("Product", max_width=45)

    for f in sorted(files, key=lambda x: x.classification.value):
        color = CLASSIFICATION_COLORS.get(f.classification, "white")
        cls_text = f"[{color}]{f.classification.value}[/{color}]"
        product = escape(f.product_name or "")
        if f.ai_confidence is not None:
            product += f" [dim](AI {f.ai_confidence:.0%})[/dim]"
        table.add_row(
            escape(f.path.name),
            f.file_type.value.upper(),
            _human_size(f.size_bytes),
            cls_text,
            product,
        )
    return table


def _print_summary(result: ScanResult) -> None:
    total = len(result.files)
    known = sum(1 for f in result.files if f.classification == Classification.KNOWN)
    orphaned = sum(1 for f in result.files if f.classification == Classification.ORPHANED)
    unknown = sum(1 for f in result.files if f.classification == Classification.UNKNOWN)

    summary = (
        f"[bold]Total files:[/bold] {total}\n"
        f"[green]Known:[/green] {known} ({_human_size(result.known_size)})\n"
        f"[red]Orphaned:[/red] {orphaned} ({_human_size(result.orphaned_size)})\n"
        f"[yellow]Unknown:[/yellow] {unknown} ({_human_size(result.unknown_size)})\n"
        f"\n[bold]Potential savings:[/bold] {_human_size(result.orphaned_size)}"
    )
    console.print(Panel(summary, title="Summary", border_style="blue"))


def _result_to_json(result: ScanResult) -> str:
    data = {
        "files": [
            {
                "path": str(f.path),
                "type": f.file_type.value,
                "size_bytes": f.size_bytes,
                "classification": f.classification.value,
                "product_name": f.product_name,
                "product_guid": f.product_guid,
                "patch_guid": f.patch_guid,
                "ai_confidence": f.ai_confidence,
                "ai_reasoning": f.ai_reasoning,
            }
            for f in result.files
        ],
        "known_size": result.known_size,
        "orphaned_size": result.orphaned_size,
        "unknown_size": result.unknown_size,
        "errors": result.errors,
    }
    return json.dumps(data, indent=2)


def _do_scan(args: argparse.Namespace) -> ScanResult:
    """Core scan + classify logic shared by all subcommands."""
    console.print(f"[bold]Scanning {INSTALLER_DIR} ...[/bold]")
    files, scan_errors = scan_installer_dir()
    console.print(f"Found {len(files)} installer files.")

    result = analyze(files)
    result.errors.extend(scan_errors)

    # AI analysis for UNKNOWN files
    if args.ai:
        keys = load_keys()
        api_key = keys.get("ANTHROPIC_API_KEY")
        if api_key:
            from patchclean.ai_advisor import analyze_unknown_files

            unknowns = [f for f in result.files if f.classification == Classification.UNKNOWN]
            if unknowns:
                console.print(f"[yellow]Running AI analysis on {len(unknowns)} unknown files...[/yellow]")
                analyze_unknown_files(result.files, api_key)
                result.recompute_sizes()
        else:
            console.print("[yellow]No ANTHROPIC_API_KEY found -- skipping AI analysis.[/yellow]")

    if args.json:
        console.print_json(_result_to_json(result))
    else:
        console.print(_build_table(result.files))
        _print_summary(result)

    if result.errors:
        console.print(f"\n[dim]({len(result.errors)} warnings/errors during scan)[/dim]")

    return result


def _cmd_scan(args: argparse.Namespace) -> None:
    _do_scan(args)


def _cmd_move(args: argparse.Namespace) -> None:
    if not is_admin():
        console.print("[red]Administrator privileges required for move operations.[/red]")
        sys.exit(1)

    result = _do_scan(args)
    orphans = [f for f in result.files if f.classification == Classification.ORPHANED]

    if not orphans:
        console.print("[green]No orphaned files to move.[/green]")
        return

    archive = Path(args.archive_dir)
    console.print(
        f"\n[bold]Will move {len(orphans)} orphaned files "
        f"({_human_size(result.orphaned_size)}) to {archive}[/bold]"
    )

    if not args.dry_run:
        if not console.input("[bold]Proceed? (y/N): [/bold]").strip().lower().startswith("y"):
            console.print("Aborted.")
            return

    outcomes = move_files(orphans, archive, dry_run=args.dry_run)
    ok = sum(1 for _, s, _ in outcomes if s)
    fail = len(outcomes) - ok
    console.print(f"[green]{ok} moved[/green], [red]{fail} failed[/red]")


def _cmd_delete(args: argparse.Namespace) -> None:
    if not is_admin():
        console.print("[red]Administrator privileges required for delete operations.[/red]")
        sys.exit(1)

    result = _do_scan(args)
    orphans = [f for f in result.files if f.classification == Classification.ORPHANED]

    if not orphans:
        console.print("[green]No orphaned files to delete.[/green]")
        return

    console.print(
        f"\n[bold red]Will PERMANENTLY DELETE {len(orphans)} orphaned files "
        f"({_human_size(result.orphaned_size)})[/bold red]"
    )

    if not args.dry_run:
        if not console.input("[bold]Type YES to confirm: [/bold]").strip() == "YES":
            console.print("Aborted.")
            return

    outcomes = delete_files(orphans, dry_run=args.dry_run)
    ok = sum(1 for _, s, _ in outcomes if s)
    fail = len(outcomes) - ok
    console.print(f"[green]{ok} deleted[/green], [red]{fail} failed[/red]")


def _add_scan_args(p: argparse.ArgumentParser) -> None:
    """Add flags shared by all subcommands."""
    p.add_argument("--ai", action="store_true", help="Enable Claude AI analysis for unknown files")
    p.add_argument("--json", action="store_true", help="Output results as JSON")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="patchclean",
        description="Scan, classify, and clean orphaned files in C:\\Windows\\Installer",
    )

    sub = parser.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="Scan and classify files (default)")
    _add_scan_args(scan_p)

    move_p = sub.add_parser("move", help="Move orphaned files to archive directory")
    _add_scan_args(move_p)
    move_p.add_argument(
        "--archive-dir",
        default=str(INSTALLER_DIR / "_archive"),
        help="Directory to move orphaned files to (default: C:\\Windows\\Installer\\_archive)",
    )

    delete_p = sub.add_parser("delete", help="Permanently delete orphaned files")
    _add_scan_args(delete_p)

    # Also add flags to the parent parser so bare `patchclean --ai` works
    _add_scan_args(parser)
    parser.add_argument(
        "--archive-dir",
        default=str(INSTALLER_DIR / "_archive"),
        help=argparse.SUPPRESS,
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    cmd = args.command or "scan"
    {"scan": _cmd_scan, "move": _cmd_move, "delete": _cmd_delete}[cmd](args)
