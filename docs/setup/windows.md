# Windows 11 Setup Guide (via WSL2)

AI Agent UI runs on Linux. On Windows, you run it inside **WSL2**
(Windows Subsystem for Linux) — a lightweight Linux VM built into
Windows 11. This guide walks you through the complete setup from
a fresh Windows 11 machine.

---

## Step 1: Enable WSL2

Open **PowerShell as Administrator** (right-click Start > Terminal (Admin)):

```powershell
wsl --install -d Ubuntu
```

This command:

- Enables the WSL2 feature
- Downloads and installs Ubuntu from the Microsoft Store
- Sets WSL2 as the default version

!!! warning "Restart required"
    After the command completes, **restart your computer**. On
    reboot, Ubuntu will finish its first-time setup and ask you
    to create a Linux username and password.

After restart, verify WSL2 is running:

```powershell
wsl --list --verbose
```

Expected output:

```
  NAME      STATE           VERSION
* Ubuntu    Running         2
```

---

## Step 2: Open Your WSL2 Terminal

You have several options:

- **Windows Terminal** (recommended): Open Windows Terminal, click
  the dropdown arrow next to the tab bar, select **Ubuntu**
- **Start menu**: Search for "Ubuntu" and click the app
- **PowerShell**: Type `wsl` to enter the Linux shell

You should see a Linux command prompt like:

```
username@DESKTOP-XXXXX:~$
```

!!! tip "All remaining steps run inside this WSL2 terminal"
    From this point on, you are working in Linux. All commands
    should be typed in the WSL2 Ubuntu terminal, not PowerShell.

---

## Step 3: Install Prerequisites

Update the package list and install build tools:

```bash
sudo apt-get update
sudo apt-get install -y \
    git curl build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget \
    llvm libncurses5-dev libncursesw5-dev xz-utils \
    tk-dev libffi-dev liblzma-dev
```

### Install Node.js 18+

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
source ~/.bashrc
nvm install 18
```

Verify:

```bash
node --version && npm --version
# → v18.x.x
# → 9.x.x
```

### Install Redis

```bash
sudo apt-get install -y redis-server
```

!!! note "Redis on WSL2"
    WSL2 typically does not have systemd, so Redis cannot be started
    with `systemctl`. `setup.sh` and `run.sh` handle this
    automatically — they launch Redis directly as a daemon.

---

## Step 4: Clone and Install

```bash
cd ~
git clone git@github.com:asequitytrading-design/ai-agent-ui.git
cd ai-agent-ui
```

!!! tip "SSH key setup"
    If you haven't set up SSH keys in WSL2, you can use HTTPS instead:
    `git clone https://github.com/asequitytrading-design/ai-agent-ui.git`

### Run the installer

```bash
./setup.sh
```

The installer will guide you through:

1. **API keys** — numbered prompts `[1/6]` through `[6/6]`
   (only Anthropic is required)
2. **Admin account** — choose defaults (`admin@demo.local` /
   `Admin123!`) or enter custom credentials
3. **Everything else** is automatic: Python 3.12, virtualenv,
   pip install, npm ci, database init, git hooks

Expected output at the end:

```
════════════════════════════════════════════════════════════════════
  All 16 checks passed. Setup complete!
════════════════════════════════════════════════════════════════════

  Admin login:
    Email:    admin@demo.local
    Password: Admin123!
    Change your password on first login.
```

!!! info "If setup crashes or stops"
    Re-run `./setup.sh` — it automatically skips completed steps
    and resumes from where it stopped. Use `./setup.sh --force`
    to start completely fresh.

---

## Step 5: Start Services

```bash
./run.sh start
```

Wait for the status table:

```
  Service       PID       URL                               Status
  ──────────────────────────────────────────────────────────
  redis         —         redis://127.0.0.1:6379            ● up
  backend       12345     http://127.0.0.1:8181             ● up
  frontend      12346     http://localhost:3000              ● up
  docs          12347     http://127.0.0.1:8000             ● up
  dashboard     12348     http://127.0.0.1:8050             ● up
```

!!! note "Status shows `◐ listening` instead of `● up`?"
    This is normal on WSL2. It means the service is responding to
    health checks but the PID couldn't be detected (WSL2 limitation).
    The service is working correctly.

---

## Step 6: Access from Windows Browser

Open your Windows browser and navigate to:

- **App**: [http://localhost:3000](http://localhost:3000)
- **Backend API**: [http://localhost:8181](http://localhost:8181)
- **Dashboard**: [http://localhost:8050](http://localhost:8050)
- **Docs**: [http://localhost:8000](http://localhost:8000)

!!! tip "Port forwarding is automatic"
    WSL2 automatically forwards `localhost` ports to Windows.
    No manual port forwarding configuration is needed.

Log in with the admin credentials shown at the end of setup.

---

## VS Code Integration (Optional)

For the best development experience, use VS Code with the
Remote-WSL extension:

1. Install [VS Code](https://code.visualstudio.com/) on Windows
2. Install the **WSL** extension from the Extensions marketplace
3. In your WSL2 terminal, navigate to the project and open VS Code:

```bash
cd ~/ai-agent-ui
code .
```

VS Code will open with full Linux filesystem access, terminal,
and debugging support.

---

## Enable Developer Mode for Symlinks (Optional)

By default, WSL2 may not support creating symbolic links.
`setup.sh` automatically falls back to copying files, but
symlinks are preferred because they stay in sync with the
master env files.

To enable symlinks:

1. Open **Windows Settings**
2. Go to **Privacy & Security** > **For developers**
3. Toggle **Developer Mode** to **ON**
4. Restart your WSL2 instance: `wsl --shutdown` then reopen

After enabling, run:

```bash
./setup.sh --repair
```

This will replace the copied files with proper symlinks.

---

## Accessing WSL2 Files from Windows

You can browse your WSL2 filesystem from Windows Explorer:

```
\\wsl$\Ubuntu\home\<username>\ai-agent-ui
```

Or from the WSL2 terminal:

```bash
explorer.exe .
```

This opens the current directory in Windows Explorer.

---

## WSL2-Specific Tips

### File watcher limit (inotify)

If the frontend dev server crashes with `ENOSPC: System limit
for number of file watchers reached`:

```bash
echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### Memory usage

WSL2 can consume significant RAM. To limit it, create or edit
`%USERPROFILE%\.wslconfig` on Windows:

```ini
[wsl2]
memory=4GB
processors=2
```

Then restart WSL2: `wsl --shutdown`

### Shutting down WSL2

From PowerShell:

```powershell
wsl --shutdown
```

This stops all WSL2 instances and frees memory.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `wsl --install` fails | Enable Hyper-V and Virtual Machine Platform in Windows Features |
| Services show `○ down` | Run `./run.sh doctor` for diagnostics |
| Symlinks failed during setup | Enable Developer Mode (see above), then `./setup.sh --repair` |
| `ENOSPC: inotify` error | Increase file watcher limit (see above) |
| Redis not starting | `redis-server --port 6379 --daemonize yes` |
| `Too many levels of symbolic links` | `./setup.sh --repair` |
| Setup crashed mid-way | Re-run `./setup.sh` — it resumes automatically |
| Port not accessible from Windows | Restart WSL2: `wsl --shutdown` then reopen |
| Slow filesystem performance | Keep project files in Linux filesystem (`~/`), not `/mnt/c/` |

---

## Useful Commands

```bash
# Service management
./run.sh start          # Start all services
./run.sh stop           # Stop all services
./run.sh status         # Health check for all services
./run.sh logs           # View recent logs
./run.sh logs --errors  # View errors across all logs
./run.sh doctor         # Run diagnostic checks

# Setup management
./setup.sh --repair     # Fix broken symlinks/hooks
./setup.sh --force      # Re-run setup from scratch
```
