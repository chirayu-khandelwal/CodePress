#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
#  CodePress — Installer
#  One-liner:  curl -fsSL https://raw.githubusercontent.com/chirayu-khandelwal/CodePress/main/install.sh | bash
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Constants ────────────────────────────────────────────────────────────
readonly REPO="chirayu-khandelwal/CodePress"
readonly BRANCH="main"
readonly SCRIPT_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/install.sh"
readonly SOURCE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/CodePress.py"
readonly UV_URL="https://astral.sh/uv/install.sh"

# ── OS detection ─────────────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux" ;;
        Darwin*) echo "darwin" ;;
        *)       echo "unknown" ;;
    esac
}

readonly OS
OS="$(detect_os)"

if [[ "$OS" == "unknown" ]]; then
    echo "✗ Unsupported OS: $(uname -s)" >&2
    exit 1
fi

# ── Install paths ────────────────────────────────────────────────────────
if [[ "$OS" == "linux" ]]; then
    readonly INSTALL_DIR="/opt/CodePress"
else
    readonly INSTALL_DIR="/usr/local/CodePress"
fi

readonly WRAPPER="/usr/local/bin/CodePress"
readonly USER_BIN="$HOME/.local/bin"
readonly SHELL_RC="$HOME/.config/fish/config.fish"

# ── Logging ──────────────────────────────────────────────────────────────
info()  { echo "  $*"; }
ok()    { echo "✓ $*"; }
fail()  { echo "✗ $*" >&2; }
step()  { echo; echo "▸ $*"; }

# ── Cleanup trap ─────────────────────────────────────────────────────────
TMPFILE=""
cleanup() { [[ -n "$TMPFILE" && -f "$TMPFILE" ]] && rm -f "$TMPFILE"; }
trap cleanup EXIT

# ── Self-bootstrap: if stdin is a pipe, download & re-exec ───────────────
if [[ ! -t 0 ]]; then
    TMPFILE="$(mktemp /tmp/codepress-install.XXXXXX.sh)"
    if ! curl -fsSL "$SCRIPT_URL" -o "$TMPFILE"; then
        fail "Failed to download installer"
        exit 1
    fi
    chmod +x "$TMPFILE"
    exec bash "$TMPFILE" "$@" </dev/tty
fi

# ── Privilege check ──────────────────────────────────────────────────────
need_sudo() {
    [[ ! -w "$(dirname "$INSTALL_DIR")" ]] || [[ ! -w "$(dirname "$WRAPPER")" ]]
}

if need_sudo && ! sudo -n true 2>/dev/null; then
    step "Elevated privileges required"
    sudo -v || { fail "Authentication failed"; exit 1; }
fi

run_sudo() {
    if need_sudo; then
        sudo "$@"
    else
        "$@"
    fi
}

# ── Install steps ────────────────────────────────────────────────────────
step "Installing CodePress to $INSTALL_DIR"

# 1. Create install directory
run_sudo mkdir -p "$INSTALL_DIR"
ok "Directory created"

# 2. Download CodePress.py
if ! curl -fsSL "$SOURCE_URL" -o "${INSTALL_DIR}/CodePress.py"; then
    fail "Failed to download CodePress.py"
    exit 1
fi
run_sudo chmod 644 "${INSTALL_DIR}/CodePress.py"
ok "CodePress.py downloaded"

# 3. Install uv (user-local, never root)
if [[ ! -x "$USER_BIN/uv" ]]; then
    step "Installing uv to $USER_BIN"
    mkdir -p "$USER_BIN"
    if ! curl -LsSf "$UV_URL" | env UV_INSTALL_DIR="$USER_BIN" sh; then
        fail "Failed to install uv"
        exit 1
    fi
    ok "uv installed"
else
    ok "uv already installed"
fi

# 4. Add user bin to shell PATH
if [[ ":$PATH:" != *":$USER_BIN:"* ]]; then
    case "$SHELL" in
        */fish*)
            grep -qF "set -gx PATH $USER_BIN" "$SHELL_RC" 2>/dev/null \
                || echo "set -gx PATH $USER_BIN \$PATH" >> "$SHELL_RC"
            ;;
        */zsh*)
            grep -qF "$USER_BIN" "$HOME/.zshrc" 2>/dev/null \
                || echo "export PATH=\"$USER_BIN:\$PATH\"" >> "$HOME/.zshrc"
            ;;
        */bash*)
            grep -qF "$USER_BIN" "$HOME/.bashrc" 2>/dev/null \
                || echo "export PATH=\"$USER_BIN:\$PATH\"" >> "$HOME/.bashrc"
            ;;
    esac
    export PATH="$USER_BIN:$PATH"
    ok "Shell PATH updated"
fi

# 5. Create wrapper script
cat > "$WRAPPER" << 'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
case "$(uname -s)" in
    Linux*)  INSTALL_DIR="/opt/CodePress" ;;
    Darwin*) INSTALL_DIR="/usr/local/CodePress" ;;
    *)       echo "Unsupported OS" >&2; exit 1 ;;
esac
if [[ "${1:-}" == "--delete" ]]; then
    echo "Uninstalling CodePress..."
    sudo rm -rf "$INSTALL_DIR"
    sudo rm -f /usr/local/bin/CodePress
    echo "✓ CodePress removed"
else
    exec uv run "$INSTALL_DIR/CodePress.py" "$@"
fi
WRAPPER

run_sudo chmod 755 "$WRAPPER"
ok "Wrapper installed at $WRAPPER"

# ── Done ─────────────────────────────────────────────────────────────────
echo
ok "Installation complete"
echo
echo "  CodePress --help        Show usage"
echo "  CodePress /path/to/project    Process a project"
echo "  CodePress --delete      Uninstall"
echo
