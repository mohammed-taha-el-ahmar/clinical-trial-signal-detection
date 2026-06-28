# DEMO.md — Clinical Trial Signal Detection

Step-by-step guide to running both the local demo and the full Azure deployment.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | `python --version` |
| uv | latest | `pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Azure CLI | ≥ 2.58 | `az --version` (Azure demo only) |
| Terraform | ≥ 1.7 | `terraform --version` (Azure demo only) |
| ODBC Driver 18 | — | Required only for Synapse writes; [install guide](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server) |

---

## Mode A — Local demo (no Azure, zero cost)

### 1. Install dependencies

```bash
uv venv
uv pip install -e ".[dev]"
```

### 2. Run tests

```bash
uv run pytest -v
```

Expected: `42 passed` (16 unit + 6 integration + 20 smoke tests).

### 3. Start the API

```bash
cp .env.example .env   # no edits needed for local mode
uv run uvicorn api.main:app --reload --port 8000
```

### 4. Open the dashboard

Navigate to `http://localhost:8000`.

**What to verify:**
- KPI strip shows event counts, active signals, trial and site counts
- Signal cards appear with confidence badges (low/medium/high)
- Event feed shows the 20 most recent adverse events
- Severity and arm bar charts populate
- "Re-run Detection" button triggers a re-scan and updates signal count

**Screenshot targets:**
- Full dashboard with at least one signal card — shows pharmacovigilance use case
- Close-up of a `high` confidence signal card — demonstrates confidence scoring
- "Re-run Detection" response toast — shows interactive detection

---

## Mode B — Full Azure demo

### 1. Authenticate

```bash
az login
az account set --subscription <YOUR_SUBSCRIPTION_ID>
```

### 2. Provision infrastructure

```bash
cd infra/terraform
terraform init
terraform apply \
  -var="sql_admin=sqladmin" \
  -var="sql_password=ChangeMe123."
```

Terraform creates: resource group, Event Hub namespace (2 hubs), ADLS Gen2, Stream Analytics job, Synapse workspace + dedicated SQL pool (DW100c).

**Cost note:** DW100c ≈ $1.51/hr. Run `terraform destroy` immediately after the demo.

### 3. Configure environment

```bash
# From Terraform outputs:
terraform output -raw simulator_connection_string  # → EVENTHUB_CONNECTION_STRING
terraform output synapse_sql_endpoint              # → SYNAPSE_SERVER

# Edit .env with these values
cp .env.example .env
```

### 4. Bootstrap Synapse schema

```bash
uv run python -c "from agent.synapse_client import ensure_schema; ensure_schema()"
```

### 5. Deploy Stream Analytics query

Configure inputs and outputs **before** pasting the query (the query won't compile without them):

**Step A — Add Input:**
- Go to **Inputs** → **+ Add stream input** → **Event Hub**
- Input alias: `adverse-events` (must match exactly, including the hyphen)
- Select your Event Hub namespace → `adverse-events` hub
- Serialization: **JSON**, Encoding: **UTF-8**

**Step B — Add Outputs:**
- Go to **Outputs** → **+ Add** → **Blob storage/ADLS Gen2**
  - Output alias: `raw-events` (exact match)
  - Select your ADLS storage account → container `bronze`
  - Path pattern: `adverse-events/{date}/{time}`
  - Serialization: JSON
- Go to **Outputs** → **+ Add** → **Event Hub**
  - Output alias: `signal-alerts` (exact match)
  - Select your Event Hub namespace → `signal-alerts` hub
  - Serialization: JSON

**Step C — Paste query:**
- Go to **Query** tab
- Paste the contents of `stream_analytics/signal_query.sql`
- Click **Save query** — it should now compile successfully

**Step D — Start:**
- Click **Start job** → **Now** → **Start**

> **Note:** The "diagnostic settings" warning is non-blocking — configure it optionally under Monitoring → Diagnostic settings.


### 6. Start the live simulator

```bash
uv run python -m simulator.runner --mode live --duration 300
```

This pushes ~2 adverse events/second for 5 minutes, with ~8% synthetic signal-injected events.

**Verification checkpoint:** In the Event Hub namespace → `adverse-events` → Monitor, incoming messages should show ~120/min.

### 7. Start the API (pointed at Synapse)

```bash
uv run uvicorn api.main:app --port 8000
```

On startup, the API will:
1. Try `fetch_events()` / `fetch_signals()` from Synapse
2. If Synapse returns data → serve that (real pipeline data)
3. If Synapse is empty or unreachable → fall back to local fixtures

Check the startup log for confirmation:
```
INFO: Startup: loaded 60 events and 3 signals from Synapse
```

### 8. Watch signals appear

Open `http://localhost:8000`. Within 10 minutes (one tumbling window), signal cards should appear as Stream Analytics emits alerts to the `signal-alerts` hub.

**Screenshot targets:**
- Stream Analytics job metrics in Azure Portal showing input/output event rates
- Synapse Studio query against `signal_alerts` table showing rows
- Dashboard with live signal cards sourced from Synapse

---

## Teardown (cost safety)

```bash
cd infra/terraform
terraform destroy -var="sql_admin=sqladmin" -var="sql_password=ChangeMe123."
```

Verify in Azure Portal that the resource group `rg-pharmasight-dev` is deleted.

---

## Useful commands (quick reference)

### Server management

```bash
# Start with hot-reload (development)
uv run uvicorn api.main:app --reload --port 8000

# Start production-style
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2

# Check if server is alive
curl -s http://localhost:8000/api/health | python -m json.tool

# Restart (kill existing + relaunch)
kill $(lsof -ti :8000) 2>/dev/null; sleep 1
uv run uvicorn api.main:app --port 8000
```

### Fixture & data management

```bash
# Regenerate fixtures (default 200 events)
uv run python -m simulator.runner --mode fixture

# Generate larger dataset for better signal density
uv run python -c "from simulator.runner import generate_fixtures; generate_fixtures(n=500)"

# Load fixtures and check signal count
uv run python -c "
from simulator.runner import load_fixtures
from agent.detector import detect_signals
events = load_fixtures()
signals = detect_signals(events)
print(f'{len(events)} events → {len(signals)} signals detected')
"

# Write fixtures to Synapse manually
uv run python -c "
from agent.synapse_client import ensure_schema, insert_events, insert_signals
from simulator.runner import load_fixtures
from agent.detector import detect_signals
ensure_schema()
events = load_fixtures()
signals = detect_signals(events)
insert_events(events)
insert_signals(signals)
print(f'Wrote {len(events)} events + {len(signals)} signals to Synapse')
"
```

### Synapse read-back (verify pipeline data)

```bash
# Check what Synapse has (event + signal counts)
uv run python -c "
from agent.synapse_client import fetch_events, fetch_signals
events = fetch_events(limit=500)
signals = fetch_signals()
print(f'Synapse: {len(events)} events, {len(signals)} signals')
for s in signals:
    print(f'  [{s.confidence}] {s.trial_id}/{s.arm} - {s.symptom_label} ({s.event_count} events)')
"

# Compare API vs Synapse
curl -s http://localhost:8000/api/stats | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'API: {d[\"total_events_today\"]} events today, {d[\"active_signals\"]} signals')
print(f'Trials: {d[\"trials_monitored\"]} | Sites: {d[\"sites_reporting\"]}')
"
```

### Testing

```bash
# Full suite
uv run pytest -v

# Specific test layers
uv run pytest tests/test_signal_detection.py -v   # unit tests
uv run pytest tests/test_integration.py -v        # integration
uv run pytest tests/test_api_smoke.py -v          # API smoke

# Single test by keyword
uv run pytest -k "test_high_rate_raises_signal"

# CI-style (quiet + short traceback)
uv run pytest --tb=short -q
```

### Linting & formatting

```bash
# Lint check
uv run ruff check .

# Lint auto-fix
uv run ruff check . --fix

# Format check
uv run ruff format --check .

# Format auto-fix
uv run ruff format .
```

### API exploration (curl)

```bash
# All endpoints
curl -s http://localhost:8000/api/health | python -m json.tool
curl -s http://localhost:8000/api/events?limit=5 | python -m json.tool
curl -s http://localhost:8000/api/signals | python -m json.tool
curl -s http://localhost:8000/api/stats | python -m json.tool
curl -s -X POST http://localhost:8000/api/detect | python -m json.tool
```

### Terraform

```bash
cd infra/terraform
terraform init
terraform plan -var="sql_admin=sqladmin" -var="sql_password=<PASSWORD>"
terraform apply -var="sql_admin=sqladmin" -var="sql_password=<PASSWORD>"
terraform output -raw simulator_connection_string
terraform destroy -var="sql_admin=sqladmin" -var="sql_password=<PASSWORD>"
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'config'`

The project uses flat imports (`from config import settings`). Always run from the project root:

```bash
cd clinical-trial-signal-detection
uv run pytest                       # ✓
uv run python -m simulator.runner   # ✓
```

### Port already in use

```bash
# Find and kill the process on port 8000
lsof -ti :8000 | xargs kill -9

# Or simply pick another port
uv run uvicorn api.main:app --port 9000
```

### Zero signals detected

Signals depend on symptom clustering. If you're unlucky with randomness:

```bash
# Regenerate with more events
uv run python -c "from simulator.runner import generate_fixtures; generate_fixtures(n=500)"
# Restart the API to reload fixtures
```

### `pyodbc` installation fails (macOS)

The Synapse client is a no-op when `SYNAPSE_SERVER` is empty. For local-only mode, pyodbc errors are cosmetic. To actually install:

```bash
brew install unixodbc
brew tap microsoft/mssql-release https://github.com/microsoft/homebrew-mssql-release
brew install msodbcsql18
```

### Tests fail with `pyodbc.OperationalError` (TCP Provider)

This happens when your `.env` has a real `SYNAPSE_SERVER` configured. The `conftest.py` patches `_SYNAPSE_AVAILABLE = False` at test time, but if the patch isn't applied (e.g. module-scoped fixture imported before conftest runs):

```bash
# Quick fix — clear Synapse env for the test run:
SYNAPSE_SERVER="" uv run pytest

# Or run the failing test in isolation to confirm it's a patch-ordering issue:
uv run pytest tests/test_api_smoke.py::TestEndToEndSmoke::test_full_pipeline_flow -v
```

**Root cause:** `_SYNAPSE_AVAILABLE` is set at import time. The conftest monkeypatches it to `False`, preventing all `insert_*` and `fetch_*` calls from hitting the network.

### API shows in-memory data instead of Synapse

The API startup logic is:
1. Try `fetch_events()` from Synapse
2. If Synapse returns data → use it (reflects real pipeline)
3. If empty or unreachable → fall back to `fixtures.json` + local detection

To force a Synapse reload:
```bash
kill $(lsof -ti :8000) 2>/dev/null
uv run uvicorn api.main:app --port 8000
```

Check the log line — it will say either:
- `loaded N events and M signals from Synapse`
- `loaded N events from fixtures, detected M signals`

### Dashboard "Loading…" state persists

1. Confirm the API is running: `curl http://localhost:8000/api/health`
2. Confirm events are loaded: `curl http://localhost:8000/api/events?limit=1`
3. Open browser DevTools (F12) → Network tab to check for failed fetches
4. The frontend polls `/api/stats` every 8 seconds — wait one cycle

### Event Hub connection refuses

- Verify connection string format: `Endpoint=sb://<ns>.servicebus.windows.net/;SharedAccessKeyName=...;SharedAccessKey=...`
- Ensure the SAS policy has `Send` permission
- Check network/firewall rules if behind a VPN

### Stream Analytics "Query not compiling"

The query references input/output aliases that must exist **before** the query will compile:

| Alias | Type | Must point to |
|---|---|---|
| `adverse-events` | Input | Event Hub `adverse-events` |
| `raw-events` | Output | ADLS Gen2 container `bronze` |
| `signal-alerts` | Output | Event Hub `signal-alerts` |

**Fix:** Add all inputs/outputs first (Step 5 above), then paste and save the query.

**ASA SQL limitations to know:**
- No `NEWID()` — use `CONCAT(...)` for IDs
- No nested aggregates: `SUM(COUNT(*)) OVER (...)` won't compile
- No CTE-to-CTE JOINs on the same stream — use single `GROUP BY` + `HAVING`
- `TIMESTAMP BY` field must exist in your input schema as a datetime

### Synapse SQL pool timeout

- Confirm endpoint ends with `.sql.azuresynapse.net`
- Add your IP to the Synapse firewall (Synapse Studio → Manage → Firewalls)
- Ensure the SQL pool is **running** (not paused) — pools auto-pause after 60 min inactivity

### DeprecationWarning in tests

Pydantic emits `datetime.utcnow()` deprecation warnings. These are cosmetic:

```bash
uv run pytest -W ignore::DeprecationWarning
```

---

## Coverage matrix

| Capability | Local demo | Azure demo |
|---|---|---|
| Synthetic adverse event generation | ✅ | ✅ |
| Signal detection (local tumbling window) | ✅ | — |
| Signal detection (Stream Analytics) | — | ✅ |
| Dashboard with live KPIs | ✅ | ✅ |
| Synapse SQL Pool persistence | ❌ (in-memory) | ✅ |
| Synapse read-back to API | ✅ (when configured) | ✅ |
| ADLS Gen2 bronze landing | ❌ | ✅ |
| Synapse Spark gold aggregation | ❌ | manual trigger |
| Infrastructure as code | — | ✅ Terraform |

**Data loading priority:**
```
API startup → fetch_events() from Synapse
                 ├─ data found → serve Synapse content (Azure demo path)
                 └─ empty/error → load fixtures.json + run local detection (local path)
```

**Known gaps:**
- Synapse Spark gold notebook requires manual trigger from Synapse Studio (no Synapse Pipeline trigger wired in this scaffold)
- Power BI integration not included; Synapse serverless SQL pool can serve as a direct BI source
