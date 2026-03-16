import base64
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import quote
from urllib.parse import urlparse

import requests
import yaml

try:
    import questionary
except ImportError:
    questionary = None

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.widgets import TextArea
except ImportError:
    Application = None
    FormattedTextControl = None
    HSplit = None
    KeyBindings = None
    Layout = None
    TextArea = None
    Window = None


API_ROOT = "https://api.github.com"
PACKAGE_TYPES = ["container", "docker", "npm", "maven", "rubygems", "nuget"]
APP_NAME = "Zuzzler"
RELEASES_API_URL = "https://api.github.com/repos/deexno/zuzzler/releases/latest"
VERSION_FILE_NAME = ".zuzzler-version.json"
SHORTCUT_LIMIT = 36
VERSION_PAGE_SIZE = 20
COMPOSE_FILENAMES = [
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
]
DOCKER_REGISTRY = "ghcr.io"
GENERATED_PROJECTS_DIR = "generated-projects"
SOURCE_EXPORT_DIR = ".zuzzler-generated"
BACK = "__back__"
NEXT = "__next__"
PREVIOUS = "__prev__"


def clear_console():
    # Prefer native terminal commands and fall back to ANSI escape sequences
    # so the script behaves consistently on Windows and Unix-like systems.
    command = "cls" if os.name == "nt" else "clear"

    try:
        subprocess.run(command, check=False, shell=True)
    except OSError:
        print("\033[2J\033[H", end="")


def render_screen(title, details=None):
    clear_console()
    print(title)
    print("=" * len(title))
    if details:
        for detail in details:
            print(detail)
        print()


def run_command(args, input_text=None, check=True, cwd=None):
    return subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
        cwd=cwd,
    )


def parse_version_tag(version_text):
    cleaned = version_text.strip().lower()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]

    parts = []
    for token in cleaned.split("."):
        digits = "".join(character for character in token if character.isdigit())
        parts.append(int(digits) if digits else 0)

    while len(parts) < 3:
        parts.append(0)

    return tuple(parts)


def app_install_root():
    return Path(__file__).resolve().parent


def version_file_path():
    return app_install_root() / VERSION_FILE_NAME


def write_installed_version(version_text):
    version_file_path().write_text(
        json.dumps({"version": version_text}, indent=2),
        encoding="utf-8",
    )


def read_installed_version():
    path = version_file_path()
    if not path.exists():
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    version_text = str(payload.get("version", "")).strip()
    return version_text or None


def latest_release_info():
    response = requests.get(
        RELEASES_API_URL,
        headers={"Accept": "application/vnd.github+json"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "tag_name": payload["tag_name"],
        "tarball_url": payload["tarball_url"],
        "html_url": payload["html_url"],
    }


def prompt_current_version(suggested_version):
    if questionary is not None:
        version_text = questionary.text(
            "Installed version:",
            default=suggested_version,
            qmark=">",
        ).ask()
        return (version_text or "").strip()

    return input(f"Installed version [{suggested_version}]: ").strip() or suggested_version


def determine_current_version(latest_release=None):
    stored_version = read_installed_version()
    if stored_version:
        return stored_version

    suggested_version = latest_release["tag_name"] if latest_release else "v0.0.0"
    render_screen(
        "Version Information Missing",
        [
            f"{VERSION_FILE_NAME} was not found in the installation directory.",
            "Please enter the version currently installed on this machine.",
            f"Suggested version: {suggested_version}",
        ],
    )
    selected_version = prompt_current_version(suggested_version)
    if not selected_version:
        selected_version = suggested_version
    write_installed_version(selected_version)
    return selected_version


def remove_installed_app_files(install_root):
    for child in install_root.iterdir():
        if child.name in {".venv", "__pycache__"}:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def extract_release_tarball(tarball_bytes, install_root):
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise RuntimeError("Release archive is empty.")

        top_level = members[0].name.split("/", 1)[0]
        for member in members:
            relative_name = member.name[len(top_level):].lstrip("/")
            if not relative_name:
                continue
            target_path = install_root / relative_name

            if member.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            with extracted, open(target_path, "wb") as destination:
                shutil.copyfileobj(extracted, destination)


def reinstall_python_dependencies(install_root):
    venv_root = install_root / ".venv"
    if os.name == "nt":
        python_bin = venv_root / "Scripts" / "python.exe"
    else:
        python_bin = venv_root / "bin" / "python"

    if not python_bin.exists():
        raise RuntimeError("Virtual environment not found for self-update.")

    run_command([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    run_command(
        [str(python_bin), "-m", "pip", "install", "-r", str(install_root / "requirements.txt")],
        check=True,
    )


def self_update_and_restart(release):
    install_root = app_install_root()
    response = requests.get(release["tarball_url"], timeout=60)
    response.raise_for_status()
    tarball_bytes = response.content

    backup_dir = install_root.parent / f"{install_root.name}.backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(install_root, backup_dir, dirs_exist_ok=True)

    try:
        remove_installed_app_files(install_root)
        extract_release_tarball(tarball_bytes, install_root)
        write_installed_version(release["tag_name"])
        reinstall_python_dependencies(install_root)
    except Exception:
        remove_installed_app_files(install_root)
        shutil.copytree(backup_dir, install_root, dirs_exist_ok=True)
        raise
    finally:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def github_get(session, url, params=None):
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response


def paginate(session, url, params=None):
    page = 1

    while True:
        merged_params = {"per_page": 100, "page": page}
        if params:
            merged_params.update(params)

        response = github_get(session, url, params=merged_params)
        data = response.json()

        if not data:
            break

        yield from data
        page += 1


def list_user_orgs(session):
    return [org["login"] for org in paginate(session, f"{API_ROOT}/user/orgs")]


def list_packages_for_namespace(session, scope_name, api_url):
    # GitHub packages are queried per package type, so we aggregate them here
    # into a single scope-local list.
    packages = []
    errors = []

    for package_type in PACKAGE_TYPES:
        try:
            entries = list(
                paginate(session, api_url, params={"package_type": package_type})
            )
            for entry in entries:
                entry["_scope"] = scope_name
                packages.append(entry)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            errors.append((package_type, status))

    return packages, errors


def list_package_versions(session, scope, package):
    package_type = package["package_type"]
    package_name = quote(package["name"], safe="")

    if scope["kind"] == "user":
        api_url = f"{API_ROOT}/user/packages/{package_type}/{package_name}/versions"
    else:
        api_url = f"{API_ROOT}/orgs/{scope['label']}/packages/{package_type}/{package_name}/versions"

    return list(paginate(session, api_url))


def repo_contents_exists(session, owner, repo, path):
    api_url = f"{API_ROOT}/repos/{owner}/{repo}/contents/{quote(path, safe='/')}"
    response = session.get(api_url, timeout=30)
    if response.status_code == 404:
        return False

    response.raise_for_status()
    return True


def get_repo_file_content(session, owner, repo, path):
    api_url = f"{API_ROOT}/repos/{owner}/{repo}/contents/{quote(path, safe='/')}"
    response = session.get(api_url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("encoding") != "base64" or "content" not in payload:
        raise RuntimeError(f"Could not decode repository file '{path}'.")

    return base64.b64decode(payload["content"]).decode("utf-8")


def unique_packages(packages):
    unique = {}

    for package in packages:
        # The same package can appear in repeated fetches across retries or
        # package-type sweeps, so de-duplicate on stable display fields.
        key = (
            package.get("_scope", ""),
            package.get("package_type", ""),
            package.get("name", ""),
            package.get("html_url", ""),
        )
        unique[key] = package

    return list(unique.values())


def ensure_questionary():
    if questionary is None:
        print("The 'questionary' package is not installed.")
        print("Install it with: pip install questionary")
        return False
    return True


def prompt_github_token():
    if questionary is not None:
        token = questionary.password(
            "API key:",
            qmark=">",
        ).ask()
        return (token or "").strip()

    import getpass

    return getpass.getpass("API key: ").strip()


def extract_source_repo_url(package, version):
    metadata = version.get("metadata") or {}
    container = metadata.get("container") or {}
    labels = container.get("labels") or {}

    if isinstance(labels, dict):
        source_url = labels.get("org.opencontainers.image.source")
        if source_url:
            return source_url

    repository = package.get("repository") or {}
    if isinstance(repository, dict):
        html_url = repository.get("html_url")
        if html_url:
            return html_url
        repository_url = repository.get("url")
        if repository_url:
            return repository_url

    package_url = package.get("html_url") or ""
    if "/packages/" in package_url:
        return package_url.split("/packages/")[0]

    return None


def parse_github_repo_url(source_url):
    if not source_url:
        return None

    parsed = urlparse(source_url)
    if parsed.netloc not in {"github.com", "www.github.com"}:
        return None

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    return owner, repo


def find_compose_file(session, owner, repo):
    # Keep the initial check cheap and predictable by probing the canonical
    # compose filenames in the repository root.
    for filename in COMPOSE_FILENAMES:
        try:
            if repo_contents_exists(session, owner, repo, filename):
                return filename
        except requests.HTTPError:
            return None

    return None


def docker_available():
    try:
        run_command(["docker", "version"], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

    return True


def docker_compose_command():
    candidates = [
        ["docker", "compose"],
        ["docker-compose"],
    ]
    for command in candidates:
        try:
            result = run_command(command + ["version"], check=False)
        except FileNotFoundError:
            continue
        if result.returncode == 0:
            return command

    return None


def list_docker_containers(all_containers=False):
    format_string = "{{.Names}}\t{{.Image}}\t{{.Status}}"
    command = ["docker", "ps", "--format", format_string]
    if all_containers:
        command.insert(2, "-a")

    try:
        result = run_command(command, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []

    containers = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        containers.append({"name": parts[0], "image": parts[1], "status": parts[2]})

    return containers


def docker_login(registry, username, token):
    result = run_command(
        ["docker", "login", registry, "-u", username, "--password-stdin"],
        input_text=f"{token}\n",
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker login failed"
        raise RuntimeError(stderr)


def docker_pull(image_reference):
    result = run_command(["docker", "pull", image_reference], check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker pull failed"
        raise RuntimeError(stderr)


def docker_run(image_reference, container_name):
    result = run_command(
        ["docker", "run", "-d", "--name", container_name, image_reference],
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker run failed"
        raise RuntimeError(stderr)

    return result.stdout.strip()


def docker_inspect_container(container_name):
    result = run_command(["docker", "inspect", container_name], check=False)
    if result.returncode != 0:
        stderr = (
            result.stderr.strip() or result.stdout.strip() or "docker inspect failed"
        )
        raise RuntimeError(stderr)

    return result.stdout


def docker_logs(container_name, tail=20):
    result = run_command(
        ["docker", "logs", "--tail", str(tail), container_name],
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker logs failed"
        raise RuntimeError(stderr)

    # Docker writes logs to stderr for some drivers, so combine both streams.
    combined = []
    if result.stdout.strip():
        combined.append(result.stdout.strip())
    if result.stderr.strip():
        combined.append(result.stderr.strip())
    return "\n".join(combined).strip()


def docker_compose_ps(compose_command, compose_file_path):
    result = run_command(
        compose_command + ["-f", compose_file_path, "ps"],
        check=False,
        cwd=str(Path(compose_file_path).parent),
    )
    if result.returncode != 0:
        stderr = (
            result.stderr.strip() or result.stdout.strip() or "docker compose ps failed"
        )
        raise RuntimeError(stderr)
    return result.stdout.strip()


def docker_compose_logs(compose_command, compose_file_path, tail=20):
    result = run_command(
        compose_command + ["-f", compose_file_path, "logs", "--tail", str(tail)],
        check=False,
        cwd=str(Path(compose_file_path).parent),
    )
    if result.returncode != 0:
        stderr = (
            result.stderr.strip()
            or result.stdout.strip()
            or "docker compose logs failed"
        )
        raise RuntimeError(stderr)
    combined = []
    if result.stdout.strip():
        combined.append(result.stdout.strip())
    if result.stderr.strip():
        combined.append(result.stderr.strip())
    return "\n".join(combined).strip()


def docker_compose_up(compose_command, compose_file_path, project_dir):
    result = run_command(
        compose_command + ["-f", compose_file_path, "up", "-d"],
        check=False,
        cwd=project_dir,
    )
    if result.returncode != 0:
        stderr = (
            result.stderr.strip() or result.stdout.strip() or "docker compose up failed"
        )
        raise RuntimeError(stderr)
    return result.stdout.strip() or result.stderr.strip()


def docker_stop(container_name):
    result = run_command(["docker", "stop", container_name], check=False)
    if result.returncode not in {0, 1}:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker stop failed"
        raise RuntimeError(stderr)


def docker_remove(container_name):
    result = run_command(["docker", "rm", container_name], check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker rm failed"
        raise RuntimeError(stderr)


def normalize_image_component(value):
    normalized = value.strip().lower().replace(" ", "-")
    normalized = re.sub(r"[^a-z0-9._/-]+", "-", normalized)
    normalized = re.sub(r"/{2,}", "/", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    normalized = normalized.strip("/.-")
    return normalized


def normalize_container_name(value):
    normalized = value.strip().lower().replace("_", "-").replace(" ", "-")
    normalized = re.sub(r"[^a-z0-9.-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    normalized = normalized.strip(".-")
    return normalized


def container_image_reference(scope, package, version):
    metadata = version.get("metadata") or {}
    container = metadata.get("container") or {}
    tags = container.get("tags") or []
    if not tags:
        return None

    package_name = normalize_image_component(package["name"].lstrip("/"))
    namespace = normalize_image_component(scope["label"])
    if not package_name or not namespace:
        return None
    return f"{DOCKER_REGISTRY}/{namespace}/{package_name}:{tags[0]}"


def split_image_reference(image_reference):
    digest_split = image_reference.split("@", 1)
    without_digest = digest_split[0]

    last_slash = without_digest.rfind("/")
    last_colon = without_digest.rfind(":")
    if last_colon > last_slash:
        return without_digest[:last_colon], without_digest[last_colon + 1 :]

    return without_digest, None


def normalized_repo_reference(image_reference):
    repository, _ = split_image_reference(image_reference)
    return normalize_image_component(repository)


def likely_service_match(service_name, service_definition, package, source_repo):
    package_leaf = normalize_container_name(package["name"].strip("/").split("/")[-1])
    repo_name = (
        normalize_container_name(source_repo[1]) if source_repo else package_leaf
    )
    normalized_service_name = normalize_container_name(service_name)
    container_name = normalize_container_name(
        str((service_definition or {}).get("container_name", ""))
    )

    candidates = {value for value in [package_leaf, repo_name] if value}
    if normalized_service_name in candidates:
        return True

    for candidate in candidates:
        if candidate and candidate in container_name:
            return True

    return False


def auto_patch_compose_images(compose_content, image_reference, package, source_repo):
    try:
        compose_data = yaml.safe_load(compose_content) or {}
    except yaml.YAMLError:
        return compose_content, []

    if not isinstance(compose_data, dict):
        return compose_content, []

    services = compose_data.get("services")
    if not isinstance(services, dict):
        return compose_content, []

    target_repo = normalized_repo_reference(image_reference)
    updated_services = []

    for service_name, service_definition in services.items():
        if not isinstance(service_definition, dict):
            continue

        current_image = service_definition.get("image")
        if isinstance(current_image, str) and current_image.strip():
            current_repo = normalized_repo_reference(current_image.strip())
            # Only rewrite services that already point to the same repository but
            # with an outdated tag or missing digest/tag details.
            if current_repo == target_repo and current_image.strip() != image_reference:
                service_definition["image"] = image_reference
                updated_services.append(service_name)
            continue

        # Only fill in a missing image when the service strongly looks like the
        # selected package, to avoid mutating unrelated services in multi-service stacks.
        if likely_service_match(service_name, service_definition, package, source_repo):
            service_definition["image"] = image_reference
            updated_services.append(service_name)

    if not updated_services:
        return compose_content, []

    normalized_content = yaml.safe_dump(
        compose_data,
        sort_keys=False,
        default_flow_style=False,
    )
    return normalized_content, updated_services


def default_container_name(package, source_repo):
    if source_repo:
        return normalize_container_name(source_repo[1])

    package_name = package["name"].strip("/").split("/")[-1]
    return normalize_container_name(package_name)


def running_container_lines(containers):
    if not containers:
        return ["Running containers: none"]

    lines = ["Running containers:"]
    for container in containers[:8]:
        lines.append(
            f"- {container['name']}  [{container['image']}]  {container['status']}"
        )
    if len(containers) > 8:
        lines.append(f"- +{len(containers) - 8} more")

    return lines


def prompt_target_container(existing_containers, running_containers, suggested_name):
    if not ensure_questionary():
        return None

    details = running_container_lines(running_containers)
    render_screen(
        "Choose Container Name",
        details + [f"Suggested new container name: {suggested_name}"],
    )

    existing_by_name = {
        container["name"]: container for container in existing_containers
    }
    choices = [
        questionary.Choice(
            title=f"Create new container ({suggested_name})",
            value={"mode": "new"},
        )
    ]
    choices.extend(
        questionary.Choice(
            title=f"{container['name']}  [{container['image']}]  {container['status']}",
            value={"mode": "existing", "container": container},
        )
        for container in sorted(
            existing_containers, key=lambda item: item["name"].lower()
        )
    )
    choices.append(questionary.Choice(title="Back", value={"mode": "back"}))

    selection = questionary.select(
        "Select a target container or create a new one.",
        choices=choices,
        use_shortcuts=len(choices) <= SHORTCUT_LIMIT,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()
    if selection is None:
        return None

    if selection["mode"] == "back":
        return BACK

    if selection["mode"] == "existing":
        return selection["container"]["name"]

    while True:
        render_screen(
            "Enter Container Name",
            details + [f"Suggested name: {suggested_name}"],
        )
        container_name = questionary.text(
            "Container name:",
            default=suggested_name,
            qmark=">",
        ).ask()
        if container_name is None:
            return BACK

        container_name = normalize_container_name(container_name)
        if not container_name:
            continue

        return container_name


def prompt_update_existing(container_name):
    if not ensure_questionary():
        return None

    choices = [
        questionary.Choice(
            title=f"Update existing container ({container_name})", value=True
        ),
        questionary.Choice(title="Do not update", value=False),
        questionary.Choice(title="Back", value=BACK),
    ]

    return questionary.select(
        f"A container named '{container_name}' already exists. What do you want to do?",
        choices=choices,
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def install_direct_container(
    github_token,
    github_username,
    scope,
    package,
    version,
    source_repo,
):
    image_reference = container_image_reference(scope, package, version)
    if not image_reference:
        render_screen(
            "Direct Installation Unavailable",
            ["The selected package version does not expose a usable container tag."],
        )
        return BACK

    if not docker_available():
        render_screen(
            "Docker Not Available",
            ["Docker CLI is not installed or not available in PATH."],
        )
        return BACK

    while True:
        running_containers = list_docker_containers(all_containers=False)
        existing_containers = list_docker_containers(all_containers=True)
        suggested_name = default_container_name(package, source_repo)
        target_container_name = prompt_target_container(
            existing_containers,
            running_containers,
            suggested_name,
        )
        if target_container_name is None or target_container_name == BACK:
            return BACK

        existing_names = {container["name"] for container in existing_containers}
        should_update = target_container_name in existing_names
        if should_update:
            render_screen(
                "Existing Container Detected",
                running_container_lines(running_containers)
                + [
                    f"Selected container: {target_container_name}",
                    f"New image: {image_reference}",
                ],
            )
            should_update = prompt_update_existing(target_container_name)
            if should_update is None or should_update == BACK:
                continue
            if not should_update:
                render_screen(
                    "No Changes Applied",
                    [
                        f"Skipped update for existing container '{target_container_name}'."
                    ],
                )
                if prompt_retry("Do you want to choose another target container?"):
                    continue
                return BACK

        render_screen(
            "Installing Container",
            [
                f"Container: {target_container_name}",
                f"Image: {image_reference}",
                "Authenticating with GHCR and pulling the image.",
            ],
        )

        try:
            docker_login(DOCKER_REGISTRY, github_username, github_token)
            docker_pull(image_reference)
            if should_update:
                update_result = update_existing_container(
                    target_container_name, image_reference
                )
                render_screen(
                    "Container Updated",
                    [
                        f"Container: {target_container_name}",
                        f"Image: {image_reference}",
                        f"Container ID: {update_result['container_id']}",
                        f"Recreated from: {update_result['recreated_command']}",
                    ],
                )
                watch_container_status(target_container_name)
            else:
                container_id = docker_run(image_reference, target_container_name)
                render_screen(
                    "Container Installed",
                    [
                        f"Container: {target_container_name}",
                        f"Image: {image_reference}",
                        f"Container ID: {container_id}",
                    ],
                )
                watch_container_status(target_container_name)
            return "done"
        except RuntimeError as exc:
            render_screen(
                "Docker Operation Failed",
                [
                    f"Container: {target_container_name}",
                    f"Image: {image_reference}",
                    str(exc),
                ],
            )
            if not prompt_retry("The Docker operation failed. What do you want to do?"):
                return BACK


def shell_join(parts):
    return " ".join(shlex.quote(part) for part in parts)


def format_timestamp(value):
    if not value:
        return "unknown"

    return value.replace("T", " ").replace("Z", " UTC")


def container_status_lines(container_name):
    inspect_raw = docker_inspect_container(container_name)
    inspect_payload = json.loads(inspect_raw)
    container = inspect_payload[0]
    state = container.get("State") or {}
    config = container.get("Config") or {}

    lines = [
        f"Container: {container_name}",
        f"Image: {config.get('Image', 'unknown')}",
        f"Status: {state.get('Status', 'unknown')}",
        f"Running: {state.get('Running', False)}",
        f"Started at: {format_timestamp(state.get('StartedAt'))}",
        f"Finished at: {format_timestamp(state.get('FinishedAt'))}",
        f"Restarting: {state.get('Restarting', False)}",
        f"Exit code: {state.get('ExitCode', 'unknown')}",
    ]

    health = state.get("Health") or {}
    if health:
        lines.append(f"Health: {health.get('Status', 'unknown')}")

    return lines


def watch_container_status(container_name, refresh_seconds=2):
    while True:
        try:
            details = container_status_lines(container_name)
            logs = docker_logs(container_name, tail=20)
        except RuntimeError as exc:
            render_screen(
                "Container Watch Failed",
                [
                    f"Container: {container_name}",
                    str(exc),
                ],
            )
            return

        render_screen(
            "Container Watch",
            details
            + [
                "",
                f"Refreshing every {refresh_seconds} seconds. Press Ctrl+C to exit.",
                "",
                "Recent logs:",
                logs or "(no logs available)",
            ],
        )

        try:
            time.sleep(refresh_seconds)
        except KeyboardInterrupt:
            render_screen(
                "Container Watch Stopped",
                [f"Stopped watching container '{container_name}'."],
            )
            return


def prompt_retry(message):
    if not ensure_questionary():
        return False

    return questionary.select(
        message,
        choices=[
            questionary.Choice(title="Try again", value=True),
            questionary.Choice(title="Back", value=False),
        ],
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def inspect_config_to_run_args(inspect_payload, image_reference, container_name):
    container = inspect_payload[0]
    config = container.get("Config") or {}
    host_config = container.get("HostConfig") or {}
    network_settings = container.get("NetworkSettings") or {}

    args = ["docker", "run", "-d", "--name", container_name]

    if config.get("Hostname"):
        args.extend(["--hostname", config["Hostname"]])

    for env_var in config.get("Env") or []:
        args.extend(["-e", env_var])

    binds = host_config.get("Binds") or []
    for bind in binds:
        args.extend(["-v", bind])

    port_bindings = host_config.get("PortBindings") or {}
    for container_port, bindings in port_bindings.items():
        for binding in bindings or [{}]:
            host_ip = binding.get("HostIp")
            host_port = binding.get("HostPort")
            publish_value = ""
            if host_ip:
                publish_value = f"{host_ip}:"
            if host_port:
                publish_value = f"{publish_value}{host_port}:"
            publish_value = f"{publish_value}{container_port}"
            args.extend(["-p", publish_value])

    restart_policy = host_config.get("RestartPolicy") or {}
    restart_name = restart_policy.get("Name")
    if restart_name:
        if restart_policy.get("MaximumRetryCount"):
            restart_name = f"{restart_name}:{restart_policy['MaximumRetryCount']}"
        args.extend(["--restart", restart_name])

    if host_config.get("Privileged"):
        args.append("--privileged")

    if host_config.get("AutoRemove"):
        args.append("--rm")

    if host_config.get("NetworkMode") and host_config["NetworkMode"] not in {
        "default",
        "bridge",
    }:
        args.extend(["--network", host_config["NetworkMode"]])

    mounts = container.get("Mounts") or []
    for mount in mounts:
        mount_type = mount.get("Type")
        if mount_type == "bind":
            source = mount.get("Source")
            destination = mount.get("Destination")
            if source and destination:
                args.extend(["-v", f"{source}:{destination}"])
        elif mount_type == "volume":
            name = mount.get("Name")
            destination = mount.get("Destination")
            if name and destination:
                args.extend(["-v", f"{name}:{destination}"])

    image = image_reference
    args.append(image)

    entrypoint = config.get("Entrypoint")
    if isinstance(entrypoint, list):
        args.extend(entrypoint)
    elif isinstance(entrypoint, str) and entrypoint:
        args.append(entrypoint)

    cmd = config.get("Cmd")
    if isinstance(cmd, list):
        args.extend(cmd)
    elif isinstance(cmd, str) and cmd:
        args.append(cmd)

    return args


def update_existing_container(container_name, image_reference):
    inspect_raw = docker_inspect_container(container_name)
    inspect_payload = json.loads(inspect_raw)
    run_args = inspect_config_to_run_args(
        inspect_payload, image_reference, container_name
    )

    was_running = inspect_payload[0].get("State", {}).get("Running", False)

    docker_stop(container_name)
    docker_remove(container_name)

    result = run_command(run_args, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker run failed"
        raise RuntimeError(
            "Failed to recreate container.\n"
            f"Command: {shell_join(run_args)}\n"
            f"Error: {stderr}"
        )

    return {
        "container_id": result.stdout.strip(),
        "recreated_command": shell_join(run_args),
        "was_running": was_running,
    }


def select_with_paging(message, items, title_builder, page_size=20, allow_back=False):
    if not ensure_questionary():
        return None

    if not items:
        return None

    page_index = 0
    total_pages = (len(items) + page_size - 1) // page_size

    while True:
        start = page_index * page_size
        end = start + page_size
        page_items = items[start:end]

        choices = [
            questionary.Choice(title=title_builder(item), value=item)
            for item in page_items
        ]

        if total_pages > 1 and page_index > 0:
            choices.append(questionary.Choice(title="Previous page", value=PREVIOUS))
        if total_pages > 1 and page_index < total_pages - 1:
            choices.append(questionary.Choice(title="Next page", value=NEXT))
        if allow_back:
            choices.append(questionary.Choice(title="Back", value=BACK))

        use_shortcuts = len(choices) <= SHORTCUT_LIMIT
        selection = questionary.select(
            f"{message}  (page {page_index + 1}/{total_pages})",
            choices=choices,
            use_shortcuts=use_shortcuts,
            use_arrow_keys=True,
            qmark=">",
            pointer=">>",
        ).ask()

        if selection == PREVIOUS:
            page_index -= 1
            continue
        if selection == NEXT:
            page_index += 1
            continue

        return selection


def prompt_scope(scopes):
    return select_with_paging(
        "Which scope do you want to list packages from?",
        scopes,
        lambda scope: f"{scope['label']}  [{scope['kind']}]",
        page_size=20,
        allow_back=True,
    )


def prompt_package(packages):
    sorted_packages = sorted(
        packages,
        key=lambda item: (
            item.get("package_type", "").lower(),
            item.get("name", "").lower(),
        ),
    )
    return select_with_paging(
        "Which package do you want to manage?",
        sorted_packages,
        lambda package: f"{package['name']}  [{package['package_type']}]",
        page_size=20,
        allow_back=True,
    )


def version_label(version):
    metadata = version.get("metadata") or {}
    container = metadata.get("container") or {}
    tags = container.get("tags") or []
    primary_tag = tags[0] if tags else "untagged"
    if len(tags) > 1:
        return f"{primary_tag}  [+{len(tags) - 1} more tags]"
    return primary_tag


def tagged_versions_only(versions):
    tagged_versions = []

    for version in versions:
        metadata = version.get("metadata") or {}
        container = metadata.get("container") or {}
        tags = container.get("tags") or []
        if tags:
            tagged_versions.append(version)

    return tagged_versions


def prompt_version(versions):
    # Version lists can become very large for container packages, so page them
    # aggressively to keep the selector responsive and within questionary's
    # keyboard shortcut limit.
    return select_with_paging(
        "Which version do you want to use?",
        versions,
        version_label,
        page_size=VERSION_PAGE_SIZE,
        allow_back=True,
    )


def prompt_install_strategy(package_name, compose_filename):
    if not ensure_questionary():
        return None

    choices = [
        questionary.Choice(
            title=f"Install container directly ({package_name})",
            value="direct",
        ),
        questionary.Choice(
            title=f"Use compose file ({compose_filename})",
            value="compose",
        ),
        questionary.Choice(title="Back", value=BACK),
    ]

    return questionary.select(
        "How do you want to continue?",
        choices=choices,
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def prompt_main_mode():
    if not ensure_questionary():
        return None

    return questionary.select(
        "What do you want to do?",
        choices=[
            questionary.Choice(title="Manage published packages", value="packages"),
            questionary.Choice(title="Package and publish a local project", value="publish"),
        ],
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def templates_root():
    return app_install_root() / "templates"


def discover_templates():
    root = templates_root()
    if not root.exists():
        return []

    templates = []
    for template_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        metadata_path = template_dir / "template.yaml"
        if not metadata_path.exists():
            continue
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        metadata["_path"] = template_dir
        templates.append(metadata)

    return templates


def prompt_template(templates):
    return select_with_paging(
        "Which template do you want to use?",
        templates,
        lambda template: f"{template['name']}  [{template.get('slug', 'template')}]",
        page_size=20,
        allow_back=True,
    )


def prompt_existing_file_path(prompt_text):
    if not ensure_questionary():
        return None

    while True:
        path_text = questionary.text(prompt_text, qmark=">").ask()
        if path_text is None:
            return None

        candidate = Path(path_text).expanduser()
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()


def prompt_optional_text(prompt_text, default=""):
    if questionary is not None:
        value = questionary.text(prompt_text, default=default, qmark=">").ask()
        return (value or "").strip()

    return input(f"{prompt_text} ").strip() or default


def prompt_template_value(question, context):
    prompt_text = question["prompt"]
    default = question.get("default")
    if question.get("default_from"):
        default = context.get(question["default_from"], default)
    return prompt_optional_text(prompt_text, default=str(default or ""))


def detect_git_remote_url(source_path):
    try:
        result = run_command(
            ["git", "-C", str(source_path), "remote", "get-url", "origin"],
            check=False,
        )
    except FileNotFoundError:
        return ""

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


def prompt_publish_namespace(current_user, orgs):
    scopes = [{"label": current_user, "kind": "user"}] + [
        {"label": org, "kind": "org"} for org in orgs
    ]
    return select_with_paging(
        "Which GitHub namespace should receive the image?",
        scopes,
        lambda scope: f"{scope['label']}  [{scope['kind']}]",
        page_size=20,
        allow_back=True,
    )


def json_command(command_text):
    return json.dumps(shlex.split(command_text))


def load_template_asset(template, source_name):
    asset_path = Path(template["_path"]) / source_name
    return asset_path.read_text(encoding="utf-8")


def render_template_text(template_text, values):
    def replace(match):
        key = match.group(1).strip()
        return str(values.get(key, ""))

    return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace, template_text)


def copy_project_source(source_path, target_path):
    def ignore(_dir, names):
        ignored = []
        for name in names:
            if name in {".git", "__pycache__", ".venv", "venv", "env", SOURCE_EXPORT_DIR}:
                ignored.append(name)
                continue
            if name.endswith((".pyc", ".pyo", ".pyd")):
                ignored.append(name)
        return ignored

    shutil.copytree(source_path, target_path, ignore=ignore)


def render_project_template(template, values, source_path, workspace_dir):
    workspace_path = Path(workspace_dir)
    app_target = workspace_path / "app"
    copy_project_source(source_path, app_target)

    generated_files = []
    for file_definition in template.get("files", []):
        content = load_template_asset(template, file_definition["source"])
        rendered = render_template_text(content, values)
        target_path = workspace_path / file_definition["target"]
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")
        if target_path.name == "entrypoint.sh" and os.name != "nt":
            target_path.chmod(0o755)
        generated_files.append(target_path)

    return generated_files


def prompt_generated_file(generated_files):
    return select_with_paging(
        "Which generated file do you want to review or edit?",
        generated_files,
        lambda file_path: str(file_path.name),
        page_size=20,
        allow_back=True,
    )


def docker_build(image_reference, context_dir):
    result = run_command(
        ["docker", "build", "-t", image_reference, "."],
        check=False,
        cwd=str(context_dir),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker build failed"
        raise RuntimeError(stderr)


def docker_push(image_reference):
    result = run_command(["docker", "push", image_reference], check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker push failed"
        raise RuntimeError(stderr)


def docker_remove_local_image(image_reference):
    result = run_command(["docker", "image", "rm", image_reference], check=False)
    if result.returncode not in {0, 1}:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker image rm failed"
        raise RuntimeError(stderr)


def docker_remove_local_container(container_name):
    result = run_command(["docker", "rm", "-f", container_name], check=False)
    if result.returncode not in {0, 1}:
        stderr = result.stderr.strip() or result.stdout.strip() or "docker rm failed"
        raise RuntimeError(stderr)


def prompt_publish_action(workspace_dir, image_reference):
    if not ensure_questionary():
        return None

    return questionary.select(
        f"Workspace: {workspace_dir}\nImage: {image_reference}",
        choices=[
            questionary.Choice(title="Edit a generated file", value="edit"),
            questionary.Choice(title="Build and push image", value="publish"),
            questionary.Choice(title="Back", value=BACK),
        ],
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def prompt_save_generated_files():
    if not ensure_questionary():
        return False

    return questionary.select(
        "Do you want to save the generated Docker and deployment files?",
        choices=[
            questionary.Choice(title="Yes (recommended)", value=True),
            questionary.Choice(title="No", value=False),
        ],
        default=True,
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def prompt_save_destination():
    if not ensure_questionary():
        return None

    return questionary.select(
        "Where should the generated files be saved?",
        choices=[
            questionary.Choice(title="Inside Zuzzler (recommended)", value="zuzzler"),
            questionary.Choice(title="Inside the source project", value="source"),
            questionary.Choice(title="Back", value=BACK),
        ],
        default="zuzzler",
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def saved_bundle_path(base_dir, template_slug, project_name):
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return base_dir / template_slug / f"{normalize_container_name(project_name)}-{timestamp}"


def persist_generated_files(generated_files, destination_dir):
    destination_dir.mkdir(parents=True, exist_ok=True)
    for file_path in generated_files:
        shutil.copy2(file_path, destination_dir / file_path.name)


def save_generated_bundle(
    generated_files,
    destination_mode,
    source_path,
    template_slug,
    project_name,
):
    if destination_mode == "zuzzler":
        destination_dir = saved_bundle_path(
            app_install_root() / GENERATED_PROJECTS_DIR,
            template_slug,
            project_name,
        )
    else:
        destination_dir = saved_bundle_path(
            source_path / SOURCE_EXPORT_DIR,
            template_slug,
            project_name,
        )

    persist_generated_files(generated_files, destination_dir)
    return destination_dir


def collect_template_values(template, source_path, namespace, source_repository_url):
    normalized_project_name = normalize_container_name(source_path.name)
    values = {
        "source_dir_name": source_path.name,
        "normalized_project_name": normalized_project_name,
        "source_repository_url": source_repository_url
        or "https://github.com/unknown/unknown",
    }

    for question in template.get("questions", []):
        answer = prompt_template_value(question, values)
        if answer is None:
            return None
        values[question["id"]] = answer

    image_name = normalize_image_component(values["image_name"])
    namespace_name = normalize_image_component(namespace["label"])
    image_tag = values["image_tag"].strip() or "latest"
    image_reference = f"{DOCKER_REGISTRY}/{namespace_name}/{image_name}:{image_tag}"

    values["image_name"] = image_name
    values["container_name"] = normalize_container_name(values["container_name"])
    values["image_reference"] = image_reference
    values["startup_command_json"] = json_command(values["startup_command"])
    return values


def summarize_generated_files(generated_files):
    return [f"- {path.name}" for path in generated_files]


def publish_project_template(session, github_token, current_user, orgs):
    templates = discover_templates()
    if not templates:
        render_screen(
            "No Templates Available",
            ["No project templates were found in the templates directory."],
        )
        return

    while True:
        render_screen(
            "Select Project Template",
            ["Choose a template to package and publish a local project to GHCR."],
        )
        selected_template = prompt_template(templates)
        if selected_template is None or selected_template == BACK:
            return

        render_screen(
            "Project Source",
            ["Select the directory containing the application source code."],
        )
        source_path = prompt_existing_file_path("Application source directory:")
        if source_path is None:
            continue

        namespace = prompt_publish_namespace(current_user, orgs)
        if namespace is None or namespace == BACK:
            continue

        detected_source_url = detect_git_remote_url(source_path)
        source_repository_url = prompt_optional_text(
            "Source repository URL:",
            default=detected_source_url,
        )

        values = collect_template_values(
            selected_template,
            source_path,
            namespace,
            source_repository_url,
        )
        if values is None:
            continue

        requirements_path = source_path / values.get("requirements_file", "requirements.txt")
        if not requirements_path.exists():
            render_screen(
                "Template Validation Failed",
                [
                    f"Requirements file not found: {requirements_path}",
                    "Adjust the template values and try again.",
                ],
            )
            continue

        if not docker_available():
            render_screen(
                "Docker Not Available",
                ["Docker CLI is not installed or not available in PATH."],
            )
            return

        with tempfile.TemporaryDirectory(prefix="zuzzler-build-") as workspace_dir:
            generated_files = render_project_template(
                selected_template,
                values,
                source_path,
                workspace_dir,
            )

            while True:
                render_screen(
                    "Generated Project Package",
                    [
                        f"Template: {selected_template['name']}",
                        f"Source path: {source_path}",
                        f"Workspace: {workspace_dir}",
                        f"Image: {values['image_reference']}",
                        "Generated files:",
                        *summarize_generated_files(generated_files),
                    ],
                )
                action = prompt_publish_action(workspace_dir, values["image_reference"])
                if action is None or action == BACK:
                    break

                if action == "edit":
                    selected_file = prompt_generated_file(generated_files)
                    if selected_file is None or selected_file == BACK:
                        continue
                    try:
                        updated_content = nano_style_text_editor(
                            selected_file.read_text(encoding="utf-8"),
                            selected_file.name,
                        )
                    except RuntimeError as exc:
                        render_screen("Editor Failed", [str(exc)])
                        if not prompt_retry("Re-open the editor or go back?"):
                            break
                        continue
                    if updated_content is None:
                        continue
                    selected_file.write_text(updated_content, encoding="utf-8")
                    continue

                render_screen(
                    "Publishing Image",
                    [
                        f"Workspace: {workspace_dir}",
                        f"Image: {values['image_reference']}",
                        "Building image and pushing it to GHCR.",
                    ],
                )
                saved_bundle = None
                if prompt_save_generated_files():
                    destination_mode = prompt_save_destination()
                    if destination_mode == BACK:
                        continue
                    saved_bundle = save_generated_bundle(
                        generated_files,
                        destination_mode,
                        source_path,
                        selected_template["slug"],
                        values["project_name"],
                    )
                try:
                    docker_login(DOCKER_REGISTRY, current_user, github_token)
                    docker_build(values["image_reference"], workspace_dir)
                    docker_push(values["image_reference"])
                    docker_remove_local_image(values["image_reference"])
                except RuntimeError as exc:
                    render_screen(
                        "Publish Failed",
                        [
                            str(exc),
                            "",
                            "The generated workspace is still available for adjustments.",
                        ],
                    )
                    if prompt_retry("Build or push failed. Edit files and try again?"):
                        continue
                    break

                render_screen(
                    "Publish Complete",
                    [
                        f"Image published: {values['image_reference']}",
                        f"Template: {selected_template['name']}",
                        f"Source path: {source_path}",
                        (
                            f"Saved generated files: {saved_bundle}"
                            if saved_bundle
                            else "Saved generated files: no"
                        ),
                        "",
                        "Local Docker image cleanup completed.",
                        "Use Zuzzler on another system to pull and deploy this image.",
                    ],
                )
                return


def prompt_self_update(current_version, release):
    if not ensure_questionary():
        return False

    choices = [
        questionary.Choice(
            title=f"Update to {release['tag_name']} and restart",
            value=True,
        ),
        questionary.Choice(title="Continue without updating", value=False),
    ]

    return questionary.select(
        f"A newer {APP_NAME} release is available ({current_version} -> {release['tag_name']}).",
        choices=choices,
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def prompt_compose_action(compose_filename, workspace_dir):
    if not ensure_questionary():
        return None

    choices = [
        questionary.Choice(title="Edit compose file", value="edit"),
        questionary.Choice(title="Save and deploy", value="deploy"),
        questionary.Choice(title="Back", value=BACK),
    ]

    return questionary.select(
        f"Compose workspace: {workspace_dir}\nFile: {compose_filename}",
        choices=choices,
        use_shortcuts=True,
        use_arrow_keys=True,
        qmark=">",
        pointer=">>",
    ).ask()


def nano_style_text_editor(initial_content, file_label):
    if TextArea is None or Application is None:
        raise RuntimeError(
            "prompt_toolkit is not available. Install it with: pip install prompt_toolkit"
        )

    saved = {"value": False}
    canceled = {"value": False}
    status_message = {
        "value": "Ctrl+S Save  Ctrl+Q Back  Ctrl+G Help  Arrow keys to navigate",
    }

    editor = TextArea(
        text=initial_content,
        multiline=True,
        wrap_lines=False,
        scrollbar=True,
        line_numbers=True,
    )

    def status_text():
        document = editor.buffer.document
        return [
            ("reverse", f" {file_label} "),
            ("", " "),
            (
                "",
                f"Ln {document.cursor_position_row + 1}, Col {document.cursor_position_col + 1} ",
            ),
            ("", status_message["value"]),
        ]

    def help_text():
        return [
            ("reverse", " Shortcuts "),
            (
                "",
                " Ctrl+S Save and return    Ctrl+Q Back without saving    Ctrl+G Toggle help ",
            ),
        ]

    key_bindings = KeyBindings()
    show_help = {"value": False}

    @key_bindings.add("c-s")
    def _(event):
        saved["value"] = True
        event.app.exit(result=editor.text)

    @key_bindings.add("c-q")
    def _(event):
        canceled["value"] = True
        event.app.exit(result=None)

    @key_bindings.add("c-g")
    def _(event):
        show_help["value"] = not show_help["value"]
        if show_help["value"]:
            status_message["value"] = "Help visible"
        else:
            status_message["value"] = (
                "Ctrl+S Save  Ctrl+Q Back  Ctrl+G Help  Arrow keys to navigate"
            )

    root = HSplit(
        [
            editor,
            Window(height=1, content=FormattedTextControl(status_text)),
            Window(height=1, content=FormattedTextControl(help_text)),
        ]
    )
    app = Application(
        layout=Layout(root),
        key_bindings=key_bindings,
        full_screen=True,
    )

    result = app.run()
    if canceled["value"]:
        return None

    if saved["value"] and result is not None:
        normalized = result
        if not normalized.endswith("\n"):
            normalized = f"{normalized}\n"
        return normalized

    return initial_content


def watch_compose_project(compose_command, compose_file_path, refresh_seconds=2):
    while True:
        try:
            ps_output = docker_compose_ps(compose_command, compose_file_path)
            logs_output = docker_compose_logs(
                compose_command, compose_file_path, tail=20
            )
        except RuntimeError as exc:
            render_screen(
                "Compose Watch Failed",
                [str(exc)],
            )
            return

        render_screen(
            "Compose Watch",
            [
                f"Compose file: {compose_file_path}",
                "",
                "Services:",
                ps_output or "(no services reported)",
                "",
                f"Refreshing every {refresh_seconds} seconds. Press Ctrl+C to exit.",
                "",
                "Recent logs:",
                logs_output or "(no logs available)",
            ],
        )

        try:
            time.sleep(refresh_seconds)
        except KeyboardInterrupt:
            render_screen(
                "Compose Watch Stopped",
                [f"Stopped watching compose file '{compose_file_path}'."],
            )
            return


def install_with_compose(
    session, source_repo, compose_filename, image_reference, package
):
    if not source_repo:
        render_screen(
            "Compose Installation Unavailable",
            ["No GitHub source repository could be resolved for this package."],
        )
        return BACK
    if not image_reference:
        render_screen(
            "Compose Installation Unavailable",
            [
                "The selected package version does not expose a usable container image reference."
            ],
        )
        return BACK

    compose_command = docker_compose_command()
    if not compose_command:
        render_screen(
            "Docker Compose Not Available",
            ["Neither 'docker compose' nor 'docker-compose' is available in PATH."],
        )
        return BACK

    try:
        compose_content = get_repo_file_content(
            session,
            source_repo[0],
            source_repo[1],
            compose_filename,
        )
    except (requests.HTTPError, RuntimeError) as exc:
        render_screen(
            "Compose Download Failed",
            [str(exc)],
        )
        return BACK

    compose_content, auto_updated_services = auto_patch_compose_images(
        compose_content,
        image_reference,
        package,
        source_repo,
    )

    with tempfile.TemporaryDirectory(prefix="zuzzler-compose-") as workspace_dir:
        compose_path = Path(workspace_dir) / compose_filename
        compose_path.write_text(compose_content, encoding="utf-8")
        editor_intro = [
            f"Workspace: {workspace_dir}",
            f"Compose file: {compose_path.name}",
            f"Selected image: {image_reference}",
        ]
        if auto_updated_services:
            editor_intro.append(
                f"Auto-updated services before opening editor: {', '.join(auto_updated_services)}"
            )
        else:
            editor_intro.append("No compose service was auto-updated.")
        render_screen("Compose Editor", editor_intro)
        try:
            updated_content = nano_style_text_editor(
                compose_path.read_text(encoding="utf-8"),
                compose_path.name,
            )
        except RuntimeError as exc:
            render_screen(
                "Compose Editor Failed",
                [str(exc)],
            )
            return BACK
        if updated_content is None:
            return BACK
        compose_path.write_text(updated_content, encoding="utf-8")

        while True:
            render_screen(
                "Compose Deployment",
                [
                    f"Workspace: {workspace_dir}",
                    f"Compose file: {compose_path.name}",
                    f"Selected image: {image_reference}",
                    (
                        f"Auto-updated services: {', '.join(auto_updated_services)}"
                        if auto_updated_services
                        else "Auto-updated services: none"
                    ),
                    "Edit the compose file as needed, then deploy it with Docker Compose.",
                ],
            )
            action = prompt_compose_action(compose_path.name, workspace_dir)
            if action is None or action == BACK:
                return BACK

            if action == "edit":
                try:
                    updated_content = nano_style_text_editor(
                        compose_path.read_text(encoding="utf-8"),
                        compose_path.name,
                    )
                except RuntimeError as exc:
                    render_screen(
                        "Compose Editor Failed",
                        [str(exc)],
                    )
                    if not prompt_retry("Re-open the editor or go back?"):
                        return BACK
                    continue
                if updated_content is None:
                    continue
                compose_path.write_text(updated_content, encoding="utf-8")
                continue

            render_screen(
                "Deploying Compose Stack",
                [
                    f"Workspace: {workspace_dir}",
                    f"Compose file: {compose_path.name}",
                    f"Command: {shell_join(compose_command + ['-f', str(compose_path), 'up', '-d'])}",
                ],
            )
            try:
                output = docker_compose_up(
                    compose_command, str(compose_path), workspace_dir
                )
            except RuntimeError as exc:
                render_screen(
                    "Compose Deployment Failed",
                    [
                        str(exc),
                        "",
                        "The compose file remains available in the temporary workspace for further edits.",
                    ],
                )
                if prompt_retry(
                    "Deployment failed. Do you want to edit and try again?"
                ):
                    continue
                return BACK

            render_screen(
                "Compose Stack Deployed",
                [
                    f"Workspace: {workspace_dir}",
                    f"Compose file: {compose_path.name}",
                    output or "Deployment completed.",
                ],
            )
            watch_compose_project(compose_command, str(compose_path))
            return "done"


def print_namespace_warnings(
    namespace_errors, selected_scope=None, selected_errors=None
):
    if not namespace_errors and not selected_errors:
        return

    print("Warnings:")
    for namespace, errors in namespace_errors:
        details = ", ".join(
            f"{package_type} (HTTP {status})" for package_type, status in errors
        )
        print(f"- {namespace}: {details}")
    if selected_scope and selected_errors:
        details = ", ".join(
            f"{package_type} (HTTP {status})"
            for package_type, status in selected_errors
        )
        print(f"- {selected_scope['label']} (current fetch): {details}")
    print(
        "If something is missing, check the token for read:packages and for org scopes also read:org."
    )


def run_package_manager(session, github_token, current_user, orgs):
    namespace_errors = []
    scopes = [
        {"label": current_user, "kind": "user", "api_url": f"{API_ROOT}/user/packages"}
    ]

    # Probe the user namespace once up front so we can surface permission issues
    # before the interactive selection.
    _, user_errors = list_packages_for_namespace(
        session,
        current_user,
        f"{API_ROOT}/user/packages",
    )
    if user_errors:
        namespace_errors.append((current_user, user_errors))

    try:
        orgs = list_user_orgs(session)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        orgs = []
        print(
            f"Could not query organizations. HTTP {status}. "
            "For organization packages you often need read:org."
        )

    for org in orgs:
        scopes.append(
            {"label": org, "kind": "org", "api_url": f"{API_ROOT}/orgs/{org}/packages"}
        )
        # Probe org scopes as well so the user sees incomplete permissions even
        # if they do not pick that scope in the current run.
        _, org_errors = list_packages_for_namespace(
            session, org, f"{API_ROOT}/orgs/{org}/packages"
        )
        if org_errors:
            namespace_errors.append((org, org_errors))

    while True:
        render_screen(
            "Select Scope",
            [
                f"Authenticated user: {current_user}",
                f"Available scopes: {', '.join(scope['label'] for scope in scopes)}",
            ],
        )
        selected_scope = prompt_scope(scopes)
        if selected_scope is None or selected_scope == BACK:
            return

        while True:
            render_screen(
                "Loading Packages",
                [
                    f"Selected scope: {selected_scope['label']} ({selected_scope['kind']})"
                ],
            )
            selected_packages, selected_errors = list_packages_for_namespace(
                session,
                selected_scope["label"],
                selected_scope["api_url"],
            )
            selected_packages = unique_packages(selected_packages)

            if not selected_packages:
                render_screen(
                    "No Packages Found",
                    [f"No packages found in scope '{selected_scope['label']}'."],
                )
                print_namespace_warnings(
                    namespace_errors, selected_scope, selected_errors
                )
                break

            render_screen(
                "Select Package",
                [
                    f"Scope: {selected_scope['label']} ({selected_scope['kind']})",
                    f"Packages found: {len(selected_packages)}",
                ],
            )
            selected_package = prompt_package(selected_packages)
            if selected_package is None or selected_package == BACK:
                break

            while True:
                render_screen(
                    "Loading Versions",
                    [
                        f"Scope: {selected_scope['label']} ({selected_scope['kind']})",
                        f"Package: {selected_package['name']} [{selected_package['package_type']}]",
                    ],
                )
                try:
                    versions = list_package_versions(
                        session, selected_scope, selected_package
                    )
                except requests.HTTPError as exc:
                    status = (
                        exc.response.status_code
                        if exc.response is not None
                        else "unknown"
                    )
                    render_screen(
                        "Version Lookup Failed",
                        [
                            f"Package: {selected_package['name']} [{selected_package['package_type']}]",
                            f"HTTP {status} while querying versions.",
                        ],
                    )
                    if prompt_retry("Version lookup failed. What do you want to do?"):
                        continue
                    break

                versions = tagged_versions_only(versions)
                if not versions:
                    render_screen(
                        "No Versions Found",
                        [
                            f"Package: {selected_package['name']} [{selected_package['package_type']}]",
                            "GitHub returned no tagged versions for this package.",
                        ],
                    )
                    break

                render_screen(
                    "Select Version",
                    [
                        f"Scope: {selected_scope['label']} ({selected_scope['kind']})",
                        f"Package: {selected_package['name']} [{selected_package['package_type']}]",
                        f"Versions found: {len(versions)}",
                    ],
                )
                selected_version = prompt_version(versions)
                if selected_version is None or selected_version == BACK:
                    break

                render_screen(
                    "Inspecting Source Repository",
                    [
                        f"Scope: {selected_scope['label']} ({selected_scope['kind']})",
                        f"Package: {selected_package['name']} [{selected_package['package_type']}]",
                        f"Version: {version_label(selected_version)}",
                    ],
                )
                source_repo_url = extract_source_repo_url(
                    selected_package, selected_version
                )
                source_repo = parse_github_repo_url(source_repo_url)
                compose_filename = None
                selected_image_reference = container_image_reference(
                    selected_scope,
                    selected_package,
                    selected_version,
                )
                if source_repo:
                    compose_filename = find_compose_file(
                        session, source_repo[0], source_repo[1]
                    )

                while True:
                    if source_repo and compose_filename:
                        render_screen(
                            "Choose Installation Strategy",
                            [
                                f"Scope: {selected_scope['label']} ({selected_scope['kind']})",
                                f"Package: {selected_package['name']} [{selected_package['package_type']}]",
                                f"Version: {version_label(selected_version)}",
                                f"Source repository: {source_repo_url}",
                                f"Compose file detected: {compose_filename}",
                            ],
                        )
                        selected_strategy = prompt_install_strategy(
                            selected_package["name"],
                            compose_filename,
                        )
                        if selected_strategy is None or selected_strategy == BACK:
                            break
                        if selected_strategy == "compose":
                            compose_result = install_with_compose(
                                session,
                                source_repo,
                                compose_filename,
                                selected_image_reference,
                                selected_package,
                            )
                            if compose_result == BACK:
                                continue
                            print_namespace_warnings(
                                namespace_errors, selected_scope, selected_errors
                            )
                            return

                    install_result = install_direct_container(
                        github_token,
                        current_user,
                        selected_scope,
                        selected_package,
                        selected_version,
                        source_repo,
                    )
                    if install_result == BACK:
                        break
                    print_namespace_warnings(
                        namespace_errors, selected_scope, selected_errors
                    )
                    return

                if source_repo and compose_filename:
                    continue
                break


def main():
    render_screen(APP_NAME)
    try:
        release = latest_release_info()
        current_version = determine_current_version(release)
        has_update = parse_version_tag(release["tag_name"]) > parse_version_tag(
            current_version
        )
    except (requests.RequestException, KeyError, ValueError):
        current_version = read_installed_version() or "unknown"
        has_update = False
        release = None

    if has_update and release:
        render_screen(
            "Update Available",
            [
                f"Installed version: {current_version}",
                f"Latest release: {release['tag_name']}",
                f"Release page: {release['html_url']}",
            ],
        )
        if prompt_self_update(current_version, release):
            render_screen(
                "Updating",
                [
                    f"Downloading release {release['tag_name']}",
                    "Updating files and restarting Zuzzler...",
                ],
            )
            try:
                self_update_and_restart(release)
            except Exception as exc:
                render_screen(
                    "Update Failed",
                    [str(exc), "Continuing with the current installation."],
                )

    render_screen(f"{APP_NAME} {current_version}")
    github_token = prompt_github_token()
    if not github_token:
        render_screen(f"{APP_NAME} {current_version}", ["No API key entered. Exiting."])
        return
    render_screen(f"{APP_NAME} {current_version}", ["Token accepted."])

    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )

    try:
        viewer = github_get(session, f"{API_ROOT}/user").json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        print(f"Authentication failed or token is incomplete. HTTP {status}")
        return

    current_user = viewer["login"]
    try:
        orgs = list_user_orgs(session)
    except requests.HTTPError:
        orgs = []

    render_screen(
        "Select Mode",
        [
            f"Authenticated user: {current_user}",
            f"Version: {current_version}",
        ],
    )
    selected_mode = prompt_main_mode()
    if selected_mode is None:
        return

    if selected_mode == "publish":
        publish_project_template(session, github_token, current_user, orgs)
        return

    run_package_manager(session, github_token, current_user, orgs)


if __name__ == "__main__":
    main()
