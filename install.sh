#!/usr/bin/env bash
set -euo pipefail

RELEASES_API_URL="${ZUZZLER_RELEASES_API_URL:-https://api.github.com/repos/deexno/zuzzler/releases/latest}"
INSTALL_ROOT="${ZUZZLER_HOME:-$HOME/.local/share/zuzzler}"
BIN_DIR="${ZUZZLER_BIN_DIR:-$HOME/.local/bin}"
VENV_DIR="$INSTALL_ROOT/.venv"
LAUNCHER_PATH="$BIN_DIR/zuzzler"
PROFILE_FILE="$HOME/.profile"
PROFILE_SNIPPET='export PATH="$HOME/.local/bin:$PATH"'
VERSION_FILE_NAME=".zuzzler-version.json"
TMP_DIR=""

log() {
  printf '[zuzzler] %s\n' "$1"
}

fail() {
  printf '[zuzzler] ERROR: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  if [ -n "${TMP_DIR}" ] && [ -d "${TMP_DIR}" ]; then
    rm -rf "${TMP_DIR}"
  fi
}

trap cleanup EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

ensure_python() {
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
    return
  fi

  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
    return
  fi

  fail "Python 3 is required."
}

ensure_profile_path() {
  mkdir -p "$BIN_DIR"

  case ":${PATH}:" in
    *":$BIN_DIR:"*) return ;;
  esac

  touch "$PROFILE_FILE"
  if ! grep -Fqx "$PROFILE_SNIPPET" "$PROFILE_FILE"; then
    log "Adding $BIN_DIR to PATH in $PROFILE_FILE"
    printf '\n# Added by Zuzzler installer\n%s\n' "$PROFILE_SNIPPET" >> "$PROFILE_FILE"
  fi
}

fetch_release_metadata() {
  mapfile -t RELEASE_METADATA < <("$PYTHON_BIN" - <<'PY'
import json
import os
import urllib.request

url = os.environ["ZUZZLER_RELEASES_API_URL_EFFECTIVE"]
request = urllib.request.Request(
    url,
    headers={"Accept": "application/vnd.github+json", "User-Agent": "zuzzler-installer"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.load(response)

print(payload["tag_name"])
print(payload["tarball_url"])
PY
)

  RELEASE_TAG="${RELEASE_METADATA[0]:-}"
  RELEASE_TARBALL_URL="${RELEASE_METADATA[1]:-}"
  [ -n "$RELEASE_TAG" ] || fail "Could not determine latest release tag."
  [ -n "$RELEASE_TARBALL_URL" ] || fail "Could not determine latest release tarball URL."
}

download_and_extract_release() {
  TMP_DIR="$(mktemp -d)"
  TARBALL_PATH="$TMP_DIR/zuzzler-release.tar.gz"
  EXTRACT_DIR="$TMP_DIR/extracted"
  mkdir -p "$EXTRACT_DIR"

  log "Downloading latest release: $RELEASE_TAG"
  export ZUZZLER_TARBALL_PATH="$TARBALL_PATH"
  "$PYTHON_BIN" - <<'PY'
import os
import urllib.request

request = urllib.request.Request(
    os.environ["ZUZZLER_TARBALL_URL_EFFECTIVE"],
    headers={"Accept": "application/vnd.github+json", "User-Agent": "zuzzler-installer"},
)
with urllib.request.urlopen(request, timeout=60) as response, open(os.environ["ZUZZLER_TARBALL_PATH"], "wb") as destination:
    destination.write(response.read())
PY

  tar -xzf "$TARBALL_PATH" -C "$EXTRACT_DIR"
  RELEASE_CONTENT_DIR="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [ -n "$RELEASE_CONTENT_DIR" ] || fail "Could not extract release contents."
}

install_release_files() {
  mkdir -p "$INSTALL_ROOT"
  find "$INSTALL_ROOT" -mindepth 1 -maxdepth 1 ! -name '.venv' -exec rm -rf {} +
  cp -R "$RELEASE_CONTENT_DIR"/. "$INSTALL_ROOT"/
  printf '{\n  "version": "%s"\n}\n' "$RELEASE_TAG" > "$INSTALL_ROOT/$VERSION_FILE_NAME"
}

ensure_venv() {
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    log "Creating virtual environment"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    return
  fi

  if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    log "Existing virtual environment is missing pip, recreating it"
    rm -rf "$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
}

install_dependencies() {
  log "Installing Python dependencies"
  if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    log "Bootstrapping pip inside the virtual environment"
    if ! "$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1; then
      fail "pip is not available in the virtual environment. On Debian/Ubuntu install python3-venv and retry."
    fi
  fi
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r "$INSTALL_ROOT/requirements.txt"
}

write_launcher() {
  log "Installing launcher to $LAUNCHER_PATH"
  mkdir -p "$BIN_DIR"
  cat > "$LAUNCHER_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$VENV_DIR/bin/python" "$INSTALL_ROOT/zuzzler.py" "\$@"
EOF
  chmod +x "$LAUNCHER_PATH"
}

print_success() {
  log "Installed release $RELEASE_TAG"
  printf '\n'
  printf 'Run it with: %s\n' "zuzzler"
  if ! command -v zuzzler >/dev/null 2>&1; then
    printf 'If the command is not available yet, reload your shell or run:\n'
    printf '  source %s\n' "$PROFILE_FILE"
  fi
}

main() {
  require_command tar
  ensure_python
  ensure_profile_path

  export ZUZZLER_RELEASES_API_URL_EFFECTIVE="$RELEASES_API_URL"
  fetch_release_metadata
  export ZUZZLER_TARBALL_URL_EFFECTIVE="$RELEASE_TARBALL_URL"

  download_and_extract_release
  install_release_files
  ensure_venv
  install_dependencies
  write_launcher
  print_success
}

main "$@"
