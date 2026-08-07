# Setup Git completion
echo "source /usr/share/bash-completion/completions/git" >> ~/.bashrc

# Install Claude Code
curl -fsSL https://claude.ai/install.sh | bash

# Install Codex
curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh
