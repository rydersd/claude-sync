#!/bin/bash
# Quick install script for claude-sync
# Usage: curl -fsSL https://raw.githubusercontent.com/rydersd/claude-sync/main/install.sh | bash

set -e

echo "Installing claude-sync..."

# Check for Homebrew
if ! command -v brew &>/dev/null; then
    echo "Homebrew not found. Install it from https://brew.sh or use:"
    echo "  python3 <(curl -fsSL https://raw.githubusercontent.com/rydersd/claude-sync/main/claude-sync.py) --help"
    exit 1
fi

# Remove old tap if present
if brew tap | grep -q "rydersd/tools"; then
    echo "Removing old rydersd/tools tap..."
    brew untap rydersd/tools 2>/dev/null || true
fi

# Tap repo
if ! brew tap | grep -q "rydersd/claude-sync"; then
    echo "Adding rydersd/claude-sync tap..."
    brew tap rydersd/claude-sync https://github.com/rydersd/claude-sync
fi

# Install or upgrade
if brew list claude-sync &>/dev/null; then
    echo "Upgrading claude-sync..."
    brew upgrade claude-sync
else
    echo "Installing claude-sync..."
    brew install claude-sync
fi

echo ""
echo "Installed: $(which claude-sync)"
echo ""
echo "Quick start:"
echo "  cd your-git-repo"
echo "  claude-sync init"
echo "  claude-sync push"
