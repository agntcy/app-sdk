# Copyright 2025 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0
import click
import subprocess
from pathlib import Path
import tempfile
import shutil

REPO_URL = "https://github.com/cisco-outshift-ai-agents/gateway-sdk.git"
BASE_DIR = Path(".gateway_infra")
DOCKER_DIR = BASE_DIR / "infra" / "docker"


def ensure_infra():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Clone the repo into the temp dir
        subprocess.run(
            ["git", "clone", "-b", "main", REPO_URL, str(tmpdir_path)], check=True
        )

        # Move 'services' directory to BASE_DIR
        src_services = tmpdir_path / "infra"
        if not src_services.exists():
            raise FileNotFoundError(
                "The 'infra' directory was not found in the cloned repo."
            )

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
