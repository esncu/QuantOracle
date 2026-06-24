# QuantOracle

FYP for Diploma in I.T. FYP

## Starter guide

- Follow the installation guide for server [here](#install)
- Run launch_linux.sh or launch_windows.ps1 (either via terminal or double-clicking)
- Go to <http://localhost:8000> on your browser (make sure its http and not https)
- Check for common issues [here](#issues)

## Installation guide

<a name="install"></a>

### Prerequisites

#### Note for Windows: Use PowerShell, not Command Prompt (CMD)

- python
- pip
- docker-cli

if Windows:

- wsl2
  
if NVIDIA GPU:

- cuda
  
if AMD GPU:

- ROCm
  
### Steps

1) Follow this guide: <https://pytorch.org/get-started/locally/>
2) run `pip install matplotlib passlib[bcrypt] fastapi[standard] psycopg2-binary requests` in your terminal

<a name="issues"></a>

## Common Issues

- "Is the server running..." "Network error: JSON.parse..." in login process
  - Docker does not have permissions to init.sql, run `chmod o+r init.sql` or give read permissions for init.sql to all
  - Alternatively, on Linux, you can run `./perms.sh` to give full perms to all related files automatically
