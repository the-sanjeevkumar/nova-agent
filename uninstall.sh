#!/usr/bin/env bash
# Nova Agent Uninstaller — with temp and complete options

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

NOVA_HOME="${NOVA_HOME:-$HOME/.nova}"
NOVA_BIN_DIR="${NOVA_BIN_DIR:-$HOME/.local/bin}"
CONFIG_DIR="${NOVA_CONFIG_DIR:-$HOME/.nova}"
DATA_DIR="${NOVA_DATA_DIR:-$HOME/.nova}"

echo ""
echo -e "${CYAN}┌─────────────────────────────────────────────────────────┐${NC}"
echo -e "${CYAN}│${NC}           🌌 Nova Agent Uninstaller                     ${CYAN}│${NC}"
echo -e "${CYAN}├─────────────────────────────────────────────────────────┤${NC}"
echo -e "${CYAN}│${NC}  Open source AI agent by Nova Labs                     ${CYAN}│${NC}"
echo -e "${CYAN}└─────────────────────────────────────────────────────────┘${NC}"
echo ""

echo "Current Installation:"
echo "  Code:    $HOME/.local/share/nova (if installed via installer)"
echo "  Config:  $CONFIG_DIR/config.yaml"
echo "  Secrets: $CONFIG_DIR/.env"
echo "  Data:    $DATA_DIR/cron, $DATA_DIR/sessions, $DATA_DIR/logs"
echo ""

echo "Uninstall Options:"
echo ""
echo "  1) Temp Uninstall - Remove code only, keep configs/sessions/logs"
echo "     (Recommended - you can reinstall later with your settings intact)"
echo ""
echo "  2) Complete Uninstall - Remove everything including all data"
echo "     (Warning: This deletes all configs, sessions, and logs permanently)"
echo ""
echo "  3) Cancel - Don't uninstall"
echo ""

read -p "Select option [1/2/3]: " choice

case $choice in
    1)
        echo ""
        echo -e "${CYAN}→${NC} Temp Uninstall: Removing code only..."
        # Remove code but keep config/data
        rm -rf "$HOME/.local/share/nova" 2>/dev/null || true
        rm -f "$HOME/.local/bin/nova" 2>/dev/null || true
        
        # Remove from shell configs
        for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.config/fish/config.fish"; do
            if [ -f "$rc" ]; then
                sed -i '/# Nova Agent/d' "$rc" 2>/dev/null || true
                sed -i '/\.local\/share\/nova/d' "$rc" 2>/dev/null || true
                sed -i '/\.nova\/bin/d' "$rc" 2>/dev/null || true
            fi
        done
        echo -e "${GREEN}✓${NC} Temp uninstall complete. Configs and data preserved."
        ;;
    2)
        echo ""
        echo -e "${RED}⚠ WARNING: This will permanently delete ALL Nova data!${NC}"
        echo "   Including: configs, API keys, sessions, scheduled jobs, logs"
        echo ""
        read -p "Type 'yes' to confirm complete uninstall: " confirm
        if [ "$confirm" != "yes" ]; then
            echo "Cancelled."
            exit 0
        fi
        
        echo ""
        echo -e "${CYAN}→${NC} Complete Uninstall: Removing everything..."
        
        # Kill any running gateway
        pkill -f "nova.*gateway" 2>/dev/null || true
        sleep 1
        
        # Remove code
        rm -rf "$HOME/.local/share/nova" 2>/dev/null || true
        rm -f "$HOME/.local/bin/nova" 2>/dev/null || true
        
        # Remove shell PATH entries
        for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.config/fish/config.fish"; do
            if [ -f "$rc" ]; then
                sed -i '/# Nova Agent/d' "$rc" 2>/dev/null || true
                sed -i '/\.local\/share\/nova/d' "$rc" 2>/dev/null || true
                sed -i '/\.nova\/bin/d' "$rc" 2>/dev/null || true
            fi
        done
        
        # Remove all config and data
        rm -rf "$HOME/.nova" 2>/dev/null || true
        
        # Remove systemd service if exists
        rm -f "$HOME/.config/systemd/user/nova-gateway.service" 2>/dev/null || true
        systemctl --user daemon-reload 2>/dev/null || true
        
        echo -e "${GREEN}✓${NC} Complete uninstall done. All Nova data removed."
        ;;
    3)
        echo "Cancelled."
        exit 0
        ;;
    *)
        echo "Invalid option. Cancelled."
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}✓ Uninstall Complete!${NC}"
echo ""
echo "Reload your shell to complete the process:"
echo "  source ~/.bashrc  # or ~/.zshrc"
echo ""
echo "Thank you for using Nova Agent! 🌌"
