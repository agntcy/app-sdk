import click
import subprocess
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCKER_DIR = BASE_DIR / "services" / "docker"

@click.group()
def cli():
    """CLI for managing infrastructure (e.g. monitoring stack)."""
    pass

@cli.command()
def up():
    """Start infrastructure services using docker-compose."""
    subprocess.run(["docker-compose", "up", "-d"], cwd=DOCKER_DIR, check=True)
    click.echo("Infrastructure started.")

@cli.command()
def down():
    """Stop infrastructure services."""
    subprocess.run(["docker-compose", "down"], cwd=DOCKER_DIR, check=True)
    click.echo("Infrastructure stopped.")

@cli.command()
def status():
    """Check status of Docker containers."""
    subprocess.run(["docker-compose", "ps"], cwd=DOCKER_DIR, check=True)

if __name__ == "__main__":
    cli()