#!/usr/bin/env bash
# Nova Agent one-line installer (npm-based).
#
# Usage:  curl -fsSL https://raw.githubusercontent.com/the-sanjeevkumar/nova-agent/main/scripts/install.sh | bash
#
# Ensures a usable Node.js (>=18) is present, then installs the agent
# globally via npm. The agent provisions its own Python environment on
# first run, so no Python is required up front.
set -euo pipefail

NOVA_NPM_PACKAGE="@the-sanjeevkumar/nova-agent"
MIN_NODE_MAJOR=18

have() { command -v "$1" >/dev/null 2>&1; }

node_major() {
  if ! have node; then echo 0; return; fi
  node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0
}

install_node() {
  echo "Node.js >= ${MIN_NODE_MAJOR} not found. Installing Node..."
  if have apt-get; then
    sudo apt-get update
    sudo apt-get install -y curl
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
  elif have dnf; then
    sudo dnf install -y nodejs npm
  elif have pacman; then
    sudo pacman -S --noconfirm nodejs npm
  elif have brew; then
    brew install node
  elif have apk; then
    sudo apk add nodejs npm
  else
    echo "Could not detect a supported package manager. Install Node.js >= ${MIN_NODE_MAJOR} manually:"
    echo "  https://nodejs.org/en/download"
    exit 1
  fi
}

if [ "$(node_major)" -lt "$MIN_NODE_MAJOR" ]; then
  install_node
fi

if [ "$(node_major)" -lt "$MIN_NODE_MAJOR" ]; then
  echo "Node.js installation failed (need >= $MIN_NODE_MAJOR, found $(node --version 2>/dev/null || echo none))."
  exit 1
fi

echo "Installing Nova Agent via npm..."
npm install -g "$NOVA_NPM_PACKAGE"

echo ""
echo "✅ Nova Agent installed."
echo "Run: nova"
echo ""
echo "If 'nova' is not found, add npm's global bin to your PATH:"
echo "  npm bin -g"
