"""Command-line entry point."""

from __future__ import annotations

import asyncio

import typer

from snag import __version__
from snag.config import get_settings
from substrate.obs import configure_logging, new_run_id

app = typer.Typer(
    name="snag",
    help="Paste a system prompt and tool definitions, find out what breaks them.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the version and exit."""
    typer.echo(f"snag {__version__}")


@app.command("work")
def work(
    concurrency: int = typer.Option(1, "--concurrency", "-c"),
    forever: bool = typer.Option(False, "--forever", help="Stay up instead of draining"),
) -> None:
    """Run a scan worker: claim queued scans and execute the real attack matrix."""
    from snag.runner import run_scan_worker

    settings = get_settings()
    configure_logging(settings.log_level)
    new_run_id()
    stats = asyncio.run(run_scan_worker(concurrency=concurrency, drain=not forever))
    typer.echo(f"processed={stats.processed} failed={stats.failed}")


@app.command("queue-stats")
def queue_stats() -> None:
    """Show the scan queue's state."""
    from snag.runner import QUEUE_NAME
    from substrate.db import Database
    from substrate.queue import JobQueue

    async def main() -> None:
        settings = get_settings()
        async with Database.open(settings.database_url) as db:
            typer.echo((await JobQueue(db, queue=QUEUE_NAME).stats()).model_dump_json())

    asyncio.run(main())


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", envvar="HOST"),
    # Render (and most PaaS free tiers) inject $PORT and expect the
    # container to bind it — the CLI flag alone would need a wrapper script
    # just to pass the value through.
    port: int = typer.Option(8000, "--port", envvar="PORT"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the HTTP API."""
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "snag.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
