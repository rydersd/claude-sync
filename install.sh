#!/bin/bash
# Install script for claude-sync
# Usage: curl -fsSL https://raw.githubusercontent.com/rydersd/claude-sync/main/install.sh | bash
#
# Or inspect before running:
#   curl -fsSL https://raw.githubusercontent.com/rydersd/claude-sync/main/install.sh -o install.sh
#   less install.sh
#   bash install.sh

set -e

echo "Setting up claude-sync..."

# Check for Homebrew
if ! command -v brew &>/dev/null; then
    echo "Homebrew not found. Install it from https://brew.sh or use:"
    echo "  python3 <(curl -fsSL https://raw.githubusercontent.com/rydersd/claude-sync/main/claude-sync.py) --help"
    exit 1
fi

# Remove old tap if present
if brew tap | grep -q "rydersd/tools"; then
    echo "Removing old rydersd/tools tap..."
    if ! brew untap rydersd/tools 2>&1; then
        echo "Warning: Could not remove old tap. You may need: brew untap rydersd/tools --force"
    fi
fi

# Tap repo
if ! brew tap | grep -q "rydersd/claude-sync"; then
    echo "Adding rydersd/claude-sync tap..."
    brew tap rydersd/claude-sync https://github.com/rydersd/claude-sync
fi

# Install or upgrade
if brew list claude-sync &>/dev/null; then
    echo "Upgrading claude-sync..."
    brew upgrade claude-sync || echo "Already up-to-date."
else
    echo "Installing claude-sync..."
    brew install claude-sync
fi

echo ""
echo "Installed: $(command -v claude-sync)"
echo ""
echo "Quick start:"
echo "  cd your-git-repo"
echo "  claude-sync init"
echo "  claude-sync push"
