"""Command-line entry point."""

from __future__ import annotations

import typer

from snag import __version__
from snag.config import get_settings
from substrate.obs import configure_logging

app = typer.Typer(
    name="snag",
    help="Paste a system prompt and tool definitions, find out what breaks them.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the version and exit."""
    typer.echo(f"snag {__version__}")


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
