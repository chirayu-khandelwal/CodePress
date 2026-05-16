#!/usr/bin/env bash
# ------------------------------------------------------------
#  install.sh – universal installer for CodePress
#  Linux (any distro) + macOS
#  Installs into /opt/CodePress  (Linux)
#              or /usr/local/CodePress (macOS)
#  Supports:  CodePress --delete
# ------------------------------------------------------------
set -euo pipefail

GREEN='\033[1;32m'
CYAN='\033[1;36m'
RED='\033[1;31m'
RESET='\033[0m'

# Detect OS and set install prefix
case "$(uname -s)" in
    Linux*)  INSTALL_DIR="/opt/CodePress" ;;
    Darwin*) INSTALL_DIR="/usr/local/CodePress" ;;
    *)       echo -e "${RED}Unsupported OS${RESET}"; exit 1 ;;
esac

WRAPPER_PATH="/usr/local/bin/CodePress"
USER_BIN="$HOME/.local/bin"

echo -e "${GREEN}🔧 Installing CodePress to ${INSTALL_DIR}${RESET}"

# Ensure we have privileges to write to INSTALL_DIR
if [[ ! -w "$(dirname "$INSTALL_DIR")" ]]; then
    echo -e "${CYAN}Requesting sudo to create ${INSTALL_DIR}${RESET}"
    sudo mkdir -p "$INSTALL_DIR"
else
    mkdir -p "$INSTALL_DIR"
fi

# Download latest CodePress.py
curl -fsSL https://raw.githubusercontent.com/chirayu-khandelwal/CodePress/main/CodePress.py \
     -o "${INSTALL_DIR}/CodePress.py"
chmod 644 "${INSTALL_DIR}/CodePress.py"

# Install UV for the current user (not root)
if [[ ! -x "$USER_BIN/uv" ]]; then
    echo -e "${GREEN}📦 Installing UV to $USER_BIN${RESET}"
    mkdir -p "$USER_BIN"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$USER_BIN" sh
fi

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$USER_BIN:"* ]]; then
    case "$SHELL" in
        */fish*)
            echo "set -gx PATH $USER_BIN \$PATH" >> ~/.config/fish/config.fish ;;
        */zsh*)
            echo 'export PATH="'"$USER_BIN"':$PATH"' >> ~/.zshrc ;;
        */bash*)
            echo 'export PATH="'"$USER_BIN"':$PATH"' >> ~/.bashrc ;;
        *) ;;
    esac
    export PATH="$USER_BIN:$PATH"
fi

# Write wrapper script
wrapper_body() {
cat <<EOF
#!/usr/bin/env bash
set -euo pipefail

case "\$(uname -s)" in
    Linux*)  INSTALL_DIR="/opt/CodePress" ;;
    Darwin*) INSTALL_DIR="/usr/local/CodePress" ;;
    *) echo "Unsupported OS"; exit 1 ;;
esac

if [[ "\${1:-}" == "--delete" ]]; then
    echo "🧹 Uninstalling CodePress..."
    sudo rm -f "\$INSTALL_DIR/CodePress.py"
    sudo rm -f /usr/local/bin/CodePress
    echo "✅ CodePress removed."
else
    exec uv run "\$INSTALL_DIR/CodePress.py" "\$@"
fi
EOF
}

if [[ ! -w "$(dirname "$WRAPPER_PATH")" ]]; then
    echo -e "${CYAN}Requesting sudo to write wrapper to ${WRAPPER_PATH}${RESET}"
    wrapper_body | sudo tee "$WRAPPER_PATH" >/dev/null
    sudo chmod +x "$WRAPPER_PATH"
else
    wrapper_body > "$WRAPPER_PATH"
    chmod +x "$WRAPPER_PATH"
fi

echo -e "${GREEN}✅ Installation complete!${RESET}"
echo -e "Run:  CodePress --help"
echo -e "Run:  CodePress --delete   (to uninstall)"
