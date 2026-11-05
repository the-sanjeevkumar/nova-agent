#!/usr/bin/env bash
# Nova Agent Installer — one-command install via curl
# Usage: curl -fsSL https://raw.githubusercontent.com/the-sanjeevkumar/nova-agent/main/install.sh | bash

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

REPO_URL="https://github.com/the-sanjeevkumar/nova-agent.git"
INSTALL_DIR="${NOVA_INSTALL_DIR:-$HOME/.nova}"
BIN_DIR="$INSTALL_DIR/bin"
NOVA_HOME="${NOVA_HOME:-$INSTALL_DIR}"
CHANNEL="${NOVA_INSTALL_CHANNEL:-stable}"

echo ""
echo -e "${CYAN}┌─────────────────────────────────────────────────────────┐${NC}"
echo -e "${CYAN}│${NC}           🌌 Nova Agent Installer                      ${CYAN}│${NC}"
echo -e "${CYAN}├─────────────────────────────────────────────────────────┤${NC}"
echo -e "${CYAN}│${NC}  Open source AI agent by Nova Labs                    ${CYAN}│${NC}"
echo -e "${CYAN}└─────────────────────────────────────────────────────────┘${NC}"
echo ""

# Check prerequisites
command -v git >/dev/null 2>&1 || { echo -e "${RED}✗ git not found${NC}"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo -e "${RED}✗ curl not found${NC}"; exit 1; }
command -v node >/dev/null 2>&1 || { echo -e "${RED}✗ Node.js not found (need 18+)${NC}"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo -e "${RED}✗ npm not found${NC}"; exit 1; }

NODE_VERSION=$(node --version | sed 's/v//' | cut -d. -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    echo -e "${RED}✗ Node.js 18+ required (found v$NODE_VERSION)${NC}"
    exit 1
fi

echo -e "${CYAN}→${NC} Prerequisites OK"
echo -e "${CYAN}→${NC} Installing to: $INSTALL_DIR"

# Create install directory
mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"

echo -e "${CYAN}→${NC} Fetching latest release info..."

download_and_install() {
    echo -e "${CYAN}→${NC} Downloading latest release..."
    
    # Detect platform
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64|amd64) ARCH="x86_64" ;;
        arm64|aarch64) ARCH="arm64" ;;
        *) echo -e "${RED}✗ Unsupported architecture: $ARCH${NC}"; exit 1 ;;
    esac
    
    PLATFORM_KEY=""
    case "$OS-$ARCH" in
        linux-x86_64) PLATFORM_KEY="linux-x86_64" ;;
        linux-arm64) PLATFORM_KEY="linux-aarch64" ;;
        darwin-x86_64) PLATFORM_KEY="macos-x86_64" ;;
        darwin-arm64) PLATFORM_KEY="macos-arm64" ;;
        *) echo -e "${RED}✗ Unsupported platform: $OS-$ARCH${NC}"; exit 1 ;;
    esac
    
    # Get latest release
    RELEASE_API="https://api.github.com/repos/the-sanjeevkumar/nova-agent/releases/latest"
    RELEASE_INFO=$(curl -sSL -H "Accept: application/vnd.github.v3+json" "$RELEASE_API" 2>/dev/null || true)
    
    ASSET_NAME="nova-agent-${PLATFORM_KEY}.tar.gz"
    [ "$PLATFORM_KEY" = "windows-x86_64" ] && ASSET_NAME="nova-agent-${PLATFORM_KEY}.zip"
    
    DOWNLOAD_URL=$(echo "$RELEASE_INFO" | grep -o "\"browser_download_url\": \"[^\"]*$ASSET_NAME\"" | cut -d'"' -f4 || true)
    
    if [ -z "$DOWNLOAD_URL" ]; then
        echo -e "${YELLOW}⚠${NC} No release asset found for $PLATFORM_KEY (release still building)."
        echo -e "${CYAN}→${NC} Falling back to source install via git clone..."
        SRC_DIR="$INSTALL_DIR/src"
        if [ -d "$SRC_DIR/.git" ]; then
            cd "$SRC_DIR" && git pull --ff-only
        else
            git clone "$REPO_URL" "$SRC_DIR"
        fi
        cd "$SRC_DIR"
        # Find a usable Python (system python is enough to bootstrap PM)
        PYTHON_BOOT=""
        for cand in python3 python; do
            if command -v "$cand" >/dev/null 2>&1; then
                PYTHON_BOOT="$cand"; break
            fi
        done
        if [ -z "$PYTHON_BOOT" ]; then
            echo -e "${RED}✗ No Python found. Install Python 3.11+ (https://www.python.org) then re-run this installer.${NC}"
            exit 1
        fi
        echo -e "${CYAN}→${NC} Provisioning runtime + dependencies via pm (this may take a few minutes)..."
        "$PYTHON_BOOT" -m pm.cli install
        # Locate the provisioned interpreter
        PYTHON_PATH=$(find "$INSTALL_DIR" -name "python3*" -type f -executable 2>/dev/null | head -1)
        [ -z "$PYTHON_PATH" ] && PYTHON_PATH=$(command -v "$PYTHON_BOOT")
        cat > "$BIN_DIR/nova" <<LAUNCHER_EOF
#!/bin/sh
exec "$PYTHON_PATH" -I -c "import sys; sys.path.insert(0, '$SRC_DIR'); from nova_cli.main import main; main()" "\$@"
LAUNCHER_EOF
        chmod +x "$BIN_DIR/nova"
        echo -e "${GREEN}✓${NC} Installed from source to $SRC_DIR"
        echo -e "${CYAN}→${NC} Run 'nova setup' to configure your provider and model."
        return 0
    fi
    
    echo -e "${CYAN}→${NC} Downloading $ASSET_NAME..."
    cd "$INSTALL_DIR"
    curl -fsSL -o "$ASSET_NAME" "$DOWNLOAD_URL"
    
    echo -e "${CYAN}→${NC} Extracting..."
    if [[ "$ASSET_NAME" == *.zip ]]; then
        unzip -q "$ASSET_NAME"
    else
        tar -xzf "$ASSET_NAME"
    fi
    rm -f "$ASSET_NAME"
    
    create_launcher
}
create_launcher() {
    echo -e "${CYAN}→${NC} Creating launcher..."
    
    # Find python in extracted files
    PYTHON_PATH=$(find "$INSTALL_DIR" -name "python3*" -type f -executable 2>/dev/null | head -1)
    [ -z "$PYTHON_PATH" ] && PYTHON_PATH=$(which python3)
    
    cat > "$BIN_DIR/nova" <<'LAUNCHER_EOF'
#!/usr/bin/env bash
export NOVA_HOME="$INSTALL_DIR"
export PATH="$PATH:$BIN_DIR"
exec "$PYTHON_PATH" "$INSTALL_DIR/nova" "$@"
LAUNCHER_EOF
    chmod +x "$BIN_DIR/nova"
    
    # Shell integration
    setup_shell
}


download_and_install



setup_shell() {
    SHELL_RC=""
    if [[ "$SHELL" == *"zsh"* ]]; then SHELL_RC="$HOME/.zshrc"
    elif [[ "$SHELL" == *"bash"* ]]; then SHELL_RC="$HOME/.bashrc"
    elif [[ "$SHELL" == *"fish"* ]]; then SHELL_RC="$HOME/.config/fish/config.fish"; fi
    
    if [ -n "$SHELL_RC" ] && [ -f "$SHELL_RC" ]; then
        if ! grep -q "$BIN_DIR" "$SHELL_RC" 2>/dev/null; then
            echo "" >> "$SHELL_RC"
            echo "# Nova Agent" >> "$SHELL_RC"
            echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$SHELL_RC"
            echo -e "${GREEN}✓${NC} Added to $SHELL_RC"
        fi
    fi
}

# Main
echo -e "${CYAN}→${NC} Installation complete!"
echo -e "${GREEN}✓${NC} Nova Agent installed to $INSTALL_DIR"
echo ""
echo "Next steps:"
echo "  1. Restart your shell: source ~/.bashrc  (or ~/.zshrc)"
echo "  2. Run: nova setup"
echo "  3. Start chatting: nova"
