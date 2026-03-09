from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

from bol_scraper.pipeline import run_pipeline

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _expand_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted([p for p in input_path.rglob("*.pdf") if p.is_file()])
    raise typer.BadParameter(f"Path not found: {input_path}")


@app.command()
def scrape(
    input_paths: list[Path] = typer.Argument(
        None,
        exists=False,
        help="Optional PDF files and/or directories of PDFs (defaults to ./input or BOL_SCRAPER_INPUT_DIR)",
    ),
    out: Path = typer.Option(Path("out.json"), help="Output JSON file"),
    out_csv: Optional[Path] = typer.Option(None, help="Optional CSV output file"),
    debug_dir: Optional[Path] = typer.Option(
        None, help="Optional debug dir for rendered pages/OCR text"
    ),
    dpi: int = typer.Option(350, help="Render DPI for OCR"),
    keep_images: bool = typer.Option(True, help="Keep rendered/preprocessed images in debug dir"),
    cache_db: Path = typer.Option(Path(".cache/bol_scraper.sqlite"), help="SQLite cache file"),
    skip_llm: bool = typer.Option(False, help="Skip LLM extraction and use rule-based extraction"),
):
    """
    Extract pickup/delivery dates+locations, total rate, compute route miles via Google,
    and calculate rate-per-mile.
    """
    load_dotenv()

    pdfs: list[Path] = []

    # If no paths were provided, default to project input folder.
    if not input_paths:
        import os

        default_dir = os.getenv("BOL_SCRAPER_INPUT_DIR", "input")
        base = Path(default_dir)
        pdfs.extend(_expand_inputs(base))
    else:
        for p in input_paths:
            pdfs.extend(_expand_inputs(p))
    # De-dupe while keeping stable order
    seen: set[str] = set()
    deduped: list[Path] = []
    for p in pdfs:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    pdfs = deduped

    if not pdfs:
        raise typer.BadParameter("No PDFs found.")

    results = run_pipeline(
        pdfs=pdfs,
        dpi=dpi,
        debug_dir=debug_dir,
        keep_images=keep_images,
        cache_db=cache_db,
        skip_llm=skip_llm,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([r.model_dump(mode="json") for r in results], indent=2), encoding="utf-8")
    console.print(f"[green]Wrote[/green] {out} ({len(results)} documents)")

    if out_csv:
        from bol_scraper.export import export_csv

        out_csv.parent.mkdir(parents=True, exist_ok=True)
        export_csv(results, out_csv)
        console.print(f"[green]Wrote[/green] {out_csv}")


if __name__ == "__main__":
    app()

