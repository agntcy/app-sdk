import click
import subprocess
import os
from pathlib import Path
import tempfile
import shutil

REPO_URL = "https://github.com/cisco-outshift-ai-agents/gateway-sdk.git"
BASE_DIR = Path(".gateway_infra")
DOCKER_DIR = BASE_DIR / "services" / "docker"

def ensure_infra():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Clone the repo into the temp dir
        subprocess.run(["git", "clone", REPO_URL, str(tmpdir_path)], check=True)

        # Move 'services' directory to BASE_DIR
        src_services = tmpdir_path / "services"
        if not src_services.exists():
            raise FileNotFoundError("The 'services' directory was not found in the cloned repo.")

        # Remove BASE_DIR entirely
        shutil.rmtree(BASE_DIR, ignore_errors=True)

        # Recreate it fresh
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_services), str(BASE_DIR))

    click.echo("Infrastructure setup complete.")

@click.group()
def cli():
    """CLI for managing infrastructure (e.g. monitoring stack)."""
    pass

@cli.command()
def up():
    """Start infrastructure services using docker-compose."""
    ensure_infra()
    subprocess.run(["docker-compose", "up", "-d"], cwd=DOCKER_DIR, check=True)
    click.echo("Infrastructure started.")

@cli.command()
def down():
    """Stop infrastructure services."""
    ensure_infra()
    subprocess.run(["docker-compose", "down"], cwd=DOCKER_DIR, check=True)
    click.echo("Infrastructure stopped.")

@cli.command()
def status():
    """Check status of Docker containers."""
    ensure_infra()
    subprocess.run(["docker-compose", "ps"], cwd=DOCKER_DIR, check=True)

@cli.command()
def clean():
    """Clean up the infrastructure."""
    ensure_infra()
    subprocess.run(["docker-compose", "down", "--volumes"], cwd=DOCKER_DIR, check=True)
    shutil.rmtree(BASE_DIR, ignore_errors=True)
    click.echo("Infrastructure cleaned up.")

if __name__ == "__main__":
    cli()