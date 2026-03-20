# macOS Setup Guide

Step-by-step guide to install AI Agent UI on macOS (Intel or Apple Silicon).

---

## Prerequisites

### 1. Xcode Command Line Tools

```bash
xcode-select --install
```

Follow the dialog to complete installation. Verify:

```bash
xcode-select -p
# → /Library/Developer/CommandLineTools
```

### 2. Homebrew

If not already installed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Verify:

```bash
brew --version
# → Homebrew 4.x.x
```

### 3. Python 3.12

`setup.sh` installs Python 3.12 via pyenv automatically, but you can install it manually:

```bash
brew install pyenv
pyenv install 3.12.9
```

Or install directly:

```bash
brew install python@3.12
```

Verify:

```bash
python3.12 --version
# → Python 3.12.9
```

### 4. Node.js 18+

Install via Homebrew or [nvm](https://github.com/nvm-sh/nvm):

```bash
# Option A: Homebrew
brew install node

# Option B: nvm (recommended for version management)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
nvm install 18
```

Verify:

```bash
node --version
# → v18.x.x or higher
npm --version
# → 9.x.x or higher
```

### 5. Redis

```bash
brew install redis
brew services start redis
```

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
| `xcode-select: error` | Run `xcode-select --install` and complete the dialog |
| `brew: command not found` | Install Homebrew (see above) |
| `python3.12: command not found` | `brew install python@3.12` or let `setup.sh` install via pyenv |
| Redis not responding | `brew services restart redis` |
| Port already in use | `./run.sh stop` then `./run.sh start` |
| Services show "down" but are running | `./run.sh doctor` for diagnostics |

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
