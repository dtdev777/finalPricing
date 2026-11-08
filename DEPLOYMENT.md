# Deployment

How to run finalPricing on a Windows machine.

## What you need

| Item | Why | Required |
|---|---|---|
| Windows 10 or 11 | Task Scheduler and the PowerShell scripts | Yes |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) with the WSL 2 backend | Runs MongoDB, PostgreSQL, the API and the dashboard | Yes |
| [Git](https://git-scm.com/download/win) | Clone the repository | Yes |
| Internet access | Build images and crawl retailer websites | Yes |
| About 3 GB of free disk space | Docker images and database volumes | Yes |
| Free local ports 8000, 8501, 5432 and 27017 | Service ports, changeable in `.env` | Yes |
| [uv](https://docs.astral.sh/uv/) | Run tests and code outside Docker | Only for development |
| OpenAI API key | LLM parsing in the assistant | No, it falls back to rules |

## 1. Get the code

```powershell
git clone https://github.com/dtdev777/finalPricing.git
cd finalPricing
```

## 2. Create `.env`

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

- Replace both `change_me` values with long random passwords.
- Change `POSTGRES_PORT`, `MONGO_PORT`, `API_PORT` or `DASHBOARD_PORT` if one is already in use.
- Optional: set `OPENAI_TOKEN_API_KEY` to enable the LLM in the assistant.

Never commit `.env`; it is already in `.gitignore`.

## 3. Start the stack

Start Docker Desktop, then run:

```powershell
docker compose up -d --build --wait
```

The command returns when every service is healthy. The first build takes a few minutes.

| Open | URL |
|---|---|
| Dashboard | <http://localhost:8501> |
| API docs | <http://localhost:8000/docs> |
| API health | <http://localhost:8000/health> |

## 4. Run the pipeline once

```powershell
docker compose --profile jobs run --rm pipeline
```

A successful run ends with `Run <id>: success` and is listed at <http://localhost:8000/api/v1/pipeline/runs>.

## 5. Schedule the daily run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
```

This registers `finalPricing-daily` for the current user:

- It runs every day at 07:00 Vietnam time, converted to the machine's clock.
- The last run is on 8 December 2026. To change this, pass `-EndDate yyyy-MM-dd` (and `-VietnamTime HH:mm` to change the time).
- If the machine is off at the scheduled time, the job runs as soon as it is back on.
- The script starts Docker Desktop if needed. The user must be signed in to Windows.

Check the task:

```powershell
Get-ScheduledTaskInfo -TaskName finalPricing-daily
```

Run it now, or remove it:

```powershell
Start-ScheduledTask -TaskName finalPricing-daily
Unregister-ScheduledTask -TaskName finalPricing-daily -Confirm:$false
```

If the machine's time zone or daylight saving changes, run `register_task.ps1` again.

## Day-to-day commands

| Task | Command |
|---|---|
| Service status | `docker compose ps` |
| Service logs | `docker compose logs -f api` |
| Pipeline and scheduler logs | Files in `logs/` |
| Stop, keeping data | `docker compose down` |
| Update to the latest code | `git pull`, then `docker compose up -d --build --wait` |
| Delete all data | `docker compose down -v` (cannot be undone) |

## Development setup (optional)

```powershell
uv sync
uv run pytest
```

If the repository is inside OneDrive, `uv sync` fails because OneDrive rejects hardlinks. Create the environment outside OneDrive first:

```powershell
$venv = "$env:LOCALAPPDATA\finalPricing\.venv"
New-Item -ItemType Directory -Force $venv | Out-Null
New-Item -ItemType Junction -Path .venv -Target $venv | Out-Null
uv sync
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `port is already allocated` | Change that port in `.env`, then run `docker compose up -d --wait` again |
| `failed to connect to the docker API` | Start Docker Desktop and wait until it reports that the engine is running |
| `set POSTGRES_USER in .env` or a similar message | `.env` is missing or incomplete; repeat step 2 |
| Dashboard shows "API unavailable" | Check `docker compose ps` and `docker compose logs api` |
| Assistant answers with `rules` despite a key | Check `docker compose logs api` for the OpenAI error; the rules still answer |
| Schema changes are not applied | `db/init` runs only on a new volume; reset with `docker compose down -v` (deletes data) |
