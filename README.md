# Zuzzler

Zuzzler is a terminal-first GitHub Container Registry manager for Linux and Windows.

It helps you:

- browse package scopes available to your GitHub token
- select a package and tagged version
- install a container directly from GHCR
- update an existing container in place
- detect a Docker Compose file from the source repository
- edit and deploy a Compose stack from inside the CLI
- watch container or Compose status after deployment
- detect newer published releases and self-update

## Highlights

- Cross-platform CLI flow for Windows and Linux
- Masked GitHub token input
- Interactive selectors with keyboard navigation
- Back navigation across scopes, packages, versions, and installation choices
- Built-in full-screen terminal editor for Docker Compose files
- Automatic image normalization for GHCR references
- Automatic best-effort Compose image correction for likely matching services
- Release-aware self-update via GitHub Releases
- Post-install live watch for direct containers and Compose stacks

## Requirements

- Python 3.10+
- Docker
- A GitHub token with package access

Recommended GitHub token scopes:

- `read:packages`
- `read:org` if you need organization packages

## Installation on Linux

One command install or update:

```bash
curl -fsSL https://raw.githubusercontent.com/deexno/zuzzler/main/install.sh | bash
```

What this does:

- downloads the latest published GitHub Release
- installs it into `~/.local/share/zuzzler`
- writes the installed release tag into `~/.local/share/zuzzler/.zuzzler-version.json`
- creates a virtual environment in `~/.local/share/zuzzler/.venv`
- installs Python dependencies
- installs a launcher at `~/.local/bin/zuzzler`
- ensures `~/.local/bin` is added to your `PATH` through `~/.profile` if needed

After installation, start the tool with:

```bash
zuzzler
```

If the command is not available yet in the current shell:

```bash
source ~/.profile
```

## Updating on Linux

Run the same command again:

```bash
curl -fsSL https://raw.githubusercontent.com/deexno/zuzzler/main/install.sh | bash
```

The installer detects an existing installation and updates it in place.

## In-App Self-Update

At startup, Zuzzler checks the latest GitHub Release for:

https://github.com/deexno/zuzzler

If a newer release is available, it prompts you to update. If you accept, Zuzzler:

- downloads the newest release
- replaces the installed application files
- updates the local version metadata file
- refreshes Python dependencies
- restarts itself automatically

If the update fails, it falls back to the current installation and continues running.

If the local version metadata file is missing, Zuzzler will tell you and ask which version is currently installed. It suggests the latest published release tag as the default and then stores your answer for future runs.

## Local Development Setup

If you want to run the repository directly:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python zuzzler.py
```

On Windows:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python .\zuzzler.py
```

## How It Works

### 1. Authenticate

Zuzzler asks for your GitHub API token using a masked input prompt.

### 2. Select a Scope

It discovers the authenticated user namespace and any accessible organizations, then lets you choose which scope to inspect.

### 3. Select a Package

Packages are listed interactively by scope and type. Large lists are paginated.

### 4. Select a Tagged Version

Only versions with at least one tag are shown. The selector displays tag names instead of internal GitHub version IDs.

### 5. Choose an Installation Strategy

If the source repository exposes a root-level Compose file, Zuzzler offers:

- direct container install
- Docker Compose install

If no Compose file is detected, it goes straight into direct installation.

## Direct Container Installation

For direct installs, Zuzzler can:

- log in to GHCR with your GitHub token
- pull the selected image
- suggest a valid default container name
- list existing containers so you can reuse a name for updates
- install a new container
- update an existing container by recreating it with the selected image

After a successful install or update, Zuzzler opens a live watch view showing:

- container status
- running state
- timestamps
- exit code
- health state if available
- recent logs

Exit the watch view with `Ctrl+C`.

## Docker Compose Flow

If a Compose file is found in the source repository, Zuzzler downloads it into a temporary workspace and opens a built-in full-screen terminal editor.

Editor controls:

- `Ctrl+S`: save and return
- `Ctrl+Q`: go back without saving
- `Ctrl+G`: toggle the help/status hint
- arrow keys and normal text editing work directly in the editor

Before the editor opens, Zuzzler tries to auto-correct likely matching `image:` entries so they point to the exact image and tag you selected earlier.

It only updates services when that is likely safe:

- the existing `image:` already points to the same repository but with the wrong tag
- or the service has no `image:` and its service/container name strongly matches the selected package

After editing, you can:

- reopen the editor
- deploy with Docker Compose
- go back

On successful deployment, Zuzzler opens a live Compose watch view with:

- `docker compose ps`
- recent Compose logs

Exit the watch view with `Ctrl+C`.

## Notes and Limitations

- Compose detection currently checks the repository root for:
  - `docker-compose.yml`
  - `docker-compose.yaml`
  - `compose.yml`
  - `compose.yaml`
- Compose auto-correction rewrites YAML through `PyYAML`, which can reformat the file and does not preserve comments.
- Compose installation currently edits and deploys the detected Compose file itself, not a full project tree with extra include files or `.env` files.
- Container update uses best-effort reconstruction from `docker inspect`. Common options are preserved, but highly customized containers may still need manual adjustments.

## Dependencies

Python packages used by Zuzzler:

- `prompt_toolkit`
- `PyYAML`
- `questionary`
- `requests`

Install them with:

```bash
pip install -r requirements.txt
```

## Repository

Source repository:

https://github.com/deexno/zuzzler
