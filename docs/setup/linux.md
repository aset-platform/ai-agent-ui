# Linux Setup Guide

Step-by-step guide to install AI Agent UI on Ubuntu/Debian Linux.

!!! note "WSL2 users"
    If you are running Linux inside WSL2 on Windows, follow the
    [Windows Setup Guide](windows.md) first for WSL2 installation,
    then return here for the Linux steps inside your WSL2 terminal.

---

## Prerequisites

### 1. System packages

```bash
sudo apt-get update
sudo apt-get install -y \
    git curl build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget \
    llvm libncurses5-dev libncursesw5-dev xz-utils \
    tk-dev libffi-dev liblzma-dev
```

### 2. Python 3.12

`setup.sh` installs Python 3.12 via pyenv automatically. To install manually:

```bash
# Install pyenv
curl -fsSL https://pyenv.run | bash

# Add to your shell profile (~/.bashrc or ~/.zshrc)
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc
source ~/.bashrc

# Install Python 3.12
pyenv install 3.12.9
```

Verify:

```bash
python3.12 --version
# → Python 3.12.9
```

### 3. Node.js 18+

Install via [nvm](https://github.com/nvm-sh/nvm) (recommended) or NodeSource:

```bash
# Option A: nvm (recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
source ~/.bashrc
nvm install 18

# Option B: NodeSource
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs
```

Verify:

```bash
node --version
# → v18.x.x or higher
npm --version
# → 9.x.x or higher
```

### 4. Redis

```bash
sudo apt-get install -y redis-server
```

Start Redis:

```bash
# With systemd (native Linux)
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Without systemd (WSL2 — see note below)
redis-server --port 6379 --daemonize yes
```

!!! tip "WSL2 and systemd"
    Most WSL2 installations do not have systemd enabled by default.
    `setup.sh` and `run.sh` automatically detect this and launch
    Redis directly as a daemon. No manual action needed.

Verify:

```bash
redis-cli ping
# → PONG
```

---

## Installation

### 1. Clone the repository

```bash
git clone git@github.com:asequitytrading-design/ai-agent-ui.git
cd ai-agent-ui
```

### 2. Run the installer

```bash
./setup.sh
```

The installer will:

- Create a Python 3.12 virtualenv at `~/.ai-agent-ui/venv`
- Install all Python and Node.js dependencies
- Prompt for API keys (Anthropic is required, others optional)
- Create a default admin account (`admin@demo.local` / `Admin123!`)
- Generate config files and symlinks
- Initialise the Iceberg database
- Install git hooks

!!! info "Crash recovery"
    If setup crashes mid-way (network timeout, permission error),
    simply re-run `./setup.sh`. Completed steps are automatically
    skipped. Use `./setup.sh --force` to start fresh.

### 3. Start all services

```bash
./run.sh start
```

### 4. Open the app

Navigate to [http://localhost:3000](http://localhost:3000) and log in with the admin credentials shown at the end of setup.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `python3.12: command not found` | Install via pyenv (see above) or let `setup.sh` handle it |
| `npm: command not found` | Install Node.js (see above) |
| Redis not responding | `redis-server --port 6379 --daemonize yes` |
| `ENOSPC: inotify` (file watcher limit) | `echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf && sudo sysctl -p` |
| Symlinks broken | `./setup.sh --repair` |
| Services show "down" but are running | `./run.sh doctor` for diagnostics |
| Setup crashed mid-way | Re-run `./setup.sh` — it resumes from where it stopped |

---

## Useful Commands

```bash
./run.sh start          # Start all services
./run.sh stop           # Stop all services
./run.sh status         # Health check for all services
./run.sh logs           # View recent logs
./run.sh logs --errors  # View errors across all logs
./run.sh doctor         # Run diagnostic checks
./setup.sh --repair     # Fix broken symlinks/hooks
./setup.sh --force      # Re-run setup from scratch
```
