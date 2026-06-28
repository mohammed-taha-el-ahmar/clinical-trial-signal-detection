# Clinical Trial Signal Detection

**Real-time pharmacovigilance using Azure Stream Analytics + Synapse Analytics**

A production-pattern pipeline that monitors adverse event streams across clinical trial sites, detects safety signals in real time using tumbling-window analysis, and surfaces them in a live dashboard — without waiting for end-of-day batch reports.

---

## Architecture

```
Trial Sites
    │
    ▼
[Event Hub] ──────────────────────────────────────────────────────┐
    │                                                              │
    ▼                                                             │
[Stream Analytics]                                                 │
 ├─ Tumbling window (10 min)                                       │
 ├─ Count threshold by (trial, arm, symptom)                       │
 ├─ Confidence scoring (low / medium / high)                       │
 │                                                                 │
 ├──► [Event Hub: signal-alerts] ──► [ADF] ──► Synapse SQL Pool   │
 │                                                                 │
 └──► [ADLS Gen2: bronze/]  ◄────────────────────────────────────┘
          │
          ▼
    [Synapse Spark]  (gold_signal_aggregation notebook)
    7-day trend · daily incidence · per-arm comparison
          │
          ▼
    [FastAPI + vanilla JS dashboard]
    Live KPIs · Signal cards · Event feed · Severity charts
```

**Hot path**: Event Hub → Stream Analytics → signal-alerts hub → Synapse SQL Pool (seconds)  
**Cold path**: Event Hub → ADLS Gen2 bronze → Synapse Spark gold layer (daily)

---

## Business problem

Phase II/III trials generate thousands of adverse event reports across dozens of sites. Pharmacovigilance teams historically reviewed weekly summaries — missing early safety signals that could have triggered earlier protocol amendments. This pipeline cuts signal detection latency from days to minutes.

---

## Key design decisions

| Decision | Rationale |
|---|---|
| Tumbling vs sliding windows | Tumbling windows produce non-overlapping, auditable signal periods — easier to trace back to a regulatory dossier |
| Count threshold in ASA, rate downstream | ASA SQL doesn't support nested aggregates or analytic functions over `GROUP BY`; incidence rate is computed in the Synapse gold layer and local detector for full accuracy |
| Dual-backend (simulator + Event Hub) | CI runs against static fixtures at zero cost; demo pushes to real Event Hub |
| Local detector mirrors ASA query | Allows unit testing of detection logic without an Azure dependency |
| ADLS Gen2 bronze layer | Raw events preserved immutably; enables reprocessing if thresholds change |
| Synapse-first data loading | API prefers Synapse data when connection is available, falls back to in-memory fixtures for local dev and CI — single codebase, two modes |

---

## Quick start (local, no Azure)

```bash
# 1. Clone and set up
git clone https://github.com/yourhandle/clinical-trial-signal-detection
cd clinical-trial-signal-detection
uv venv && uv pip install -e ".[dev]"

# 2. Run tests
uv run pytest

# 3. Generate fixtures and start the API
cp .env.example .env          # no changes needed for local mode
uv run uvicorn api.main:app --reload --port 8000

# 4. Open dashboard
open http://localhost:8000
```

---

## Azure demo

See [DEMO.md](DEMO.md) for full step-by-step instructions including Terraform provisioning, live simulator, and Stream Analytics deployment.

---

## Project structure

```
├── .github/workflows/ci.yml          # GitHub Actions CI pipeline
├── config.py                          # Pydantic settings (from .env)
├── models.py                          # AdverseEvent, SignalAlert, DashboardStats
├── agent/
│   ├── detector.py                    # Local tumbling-window signal detector
│   └── synapse_client.py              # Synapse SQL Pool reader/writer (no-op if unconfigured)
├── simulator/
│   ├── runner.py                      # Synthetic event generator (fixture + live)
│   └── fixtures.json                  # Auto-generated test data (gitignored)
├── api/
│   └── main.py                        # FastAPI app + static file serving
├── frontend/static/
│   ├── index.html                     # Dashboard shell
│   ├── style.css                      # Dark-theme styles
│   └── app.js                         # Polling + rendering logic
├── stream_analytics/
│   └── signal_query.sql               # ASA query (mirrors detector.py)
├── synapse/
│   └── notebooks/gold_signal_aggregation.py
├── infra/terraform/
│   └── main.tf                        # Event Hub, ADLS Gen2, ASA job, Synapse workspace
└── tests/
    ├── conftest.py                    # Shared fixtures, Azure mocking
    ├── test_signal_detection.py       # 16 unit tests (detector + models)
    ├── test_integration.py            # 6 integration tests (simulator → detector)
    └── test_api_smoke.py              # 20 API smoke tests (all endpoints)
```

---

## CI / CD

The project uses **GitHub Actions** (`.github/workflows/ci.yml`) with three parallel jobs:

| Job | What it does |
|---|---|
| **lint** | `ruff check .` + `ruff format --check .` |
| **test** | Unit tests + integration tests via pytest |
| **smoke** | API smoke tests + live server curl validation |

CI triggers on every push/PR to `main`. No Azure credentials required — all tests run against local fixtures.

---

## Useful commands

### Environment setup

```bash
# Create virtual environment
uv venv

# Install all dependencies (including dev)
uv pip install -e ".[dev]"

# Activate venv manually (optional — uv run handles this)
source .venv/bin/activate
```

### Running the application

```bash
# Start API server (development mode with hot-reload)
uv run uvicorn api.main:app --reload --port 8000

# Start API server (production-like)
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2

# Generate fresh fixture data
uv run python -m simulator.runner --mode fixture

# Run live simulator (pushes to Event Hub — requires .env config)
uv run python -m simulator.runner --mode live --duration 300
```

### Testing

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_signal_detection.py
uv run pytest tests/test_api_smoke.py
uv run pytest tests/test_integration.py

# Run a single test by name
uv run pytest -k "test_high_rate_raises_signal"

# Run with short traceback (CI-style)
uv run pytest --tb=short -q

# Run only smoke tests
uv run pytest tests/test_api_smoke.py -v
```

### Linting & formatting

```bash
# Check for lint errors
uv run ruff check .

# Auto-fix lint errors
uv run ruff check . --fix

# Check formatting
uv run ruff format --check .

# Auto-format all files
uv run ruff format .
```

### API interaction (curl)

```bash
# Health check
curl -s http://localhost:8000/api/health | python -m json.tool

# Get recent events
curl -s "http://localhost:8000/api/events?limit=10" | python -m json.tool

# Get active signals
curl -s http://localhost:8000/api/signals | python -m json.tool

# Get dashboard stats
curl -s http://localhost:8000/api/stats | python -m json.tool

# Trigger signal re-detection
curl -s -X POST http://localhost:8000/api/detect | python -m json.tool
```

### Synapse queries (when connected)

```bash
# Check what the API loaded on startup
uv run python -c "
from agent.synapse_client import fetch_events, fetch_signals
events = fetch_events(limit=10)
signals = fetch_signals()
print(f'Synapse: {len(events)} events (showing first 10), {len(signals)} signals')
for s in signals:
    print(f'  [{s.confidence}] {s.trial_id}/{s.arm} - {s.symptom_label} ({s.event_count} events)')
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

### Terraform (Azure deployment)

```bash
cd infra/terraform

# Initialise providers
terraform init

# Preview changes
terraform plan -var="sql_admin=sqladmin" -var="sql_password=<PASSWORD>"

# Apply infrastructure
terraform apply -var="sql_admin=sqladmin" -var="sql_password=<PASSWORD>"

# Get connection details
terraform output -raw simulator_connection_string
terraform output synapse_sql_endpoint

# Teardown (important — avoids ongoing costs)
terraform destroy -var="sql_admin=sqladmin" -var="sql_password=<PASSWORD>"
```

---

## Troubleshooting

### Common issues

#### `ModuleNotFoundError: No module named 'config'`

The project uses flat imports (e.g., `from config import settings`). Make sure you're running from the project root:

```bash
cd clinical-trial-signal-detection
uv run pytest                       # ✓ correct
uv run python -m simulator.runner   # ✓ correct
```

Do **not** `cd` into subdirectories before running commands.

#### Port 8000 already in use

```bash
# Find what's using the port
lsof -ti :8000

# Kill it
kill $(lsof -ti :8000)

# Or use a different port
uv run uvicorn api.main:app --port 9000
```

#### Fixtures not generating signals

The fixture generator uses randomness. If detection returns 0 signals, regenerate with more events:

```bash
uv run python -c "from simulator.runner import generate_fixtures; generate_fixtures(n=500)"
```

Then restart the API — signals depend on symptom clustering within tumbling windows.

#### `pyodbc` installation fails (macOS)

`pyodbc` requires the ODBC driver. For local-only usage you can skip Synapse features — the app runs fully in-memory without it. To install the driver:

```bash
# macOS
brew install unixodbc
brew tap microsoft/mssql-release https://github.com/microsoft/homebrew-mssql-release
brew install msodbcsql18

# Ubuntu / CI
sudo apt-get install -y unixodbc-dev
```

#### Tests fail with `pyodbc.OperationalError` (TCP Provider)

If your `.env` has a real `SYNAPSE_SERVER` configured, tests may try to connect. The `conftest.py` patches `_SYNAPSE_AVAILABLE = False` to prevent this, but if you see TCP errors:

```bash
# Verify conftest is patching correctly:
uv run pytest tests/test_api_smoke.py::TestEndToEndSmoke::test_full_pipeline_flow -v

# Or temporarily clear Synapse env vars:
SYNAPSE_SERVER="" uv run pytest
```

The root cause: `_SYNAPSE_AVAILABLE` is evaluated at import time from `.env`. The conftest monkeypatches it to `False` for all tests.

#### API shows fewer events than Synapse

On startup, the API tries to read from Synapse first (`fetch_events`). If Synapse is available:
- Events and signals come directly from the database
- This reflects data written by Event Hub → ASA → Synapse pipeline

If Synapse is unreachable (local dev / CI):
- Falls back to `simulator/fixtures.json`
- Runs local detection over those fixtures

To force a fresh reload from Synapse, restart the API:
```bash
kill $(lsof -ti :8000) 2>/dev/null
uv run uvicorn api.main:app --port 8000
```

#### Tests show `DeprecationWarning: datetime.datetime.utcnow()`

This is a known warning from Pydantic's default factories. It doesn't affect functionality and will be resolved in a future Pydantic release. Suppress in test output:

```bash
uv run pytest -W ignore::DeprecationWarning
```

#### Dashboard shows "Loading…" but no data

1. Check the API is running: `curl http://localhost:8000/api/health`
2. Check events are loaded: `curl http://localhost:8000/api/events?limit=1`
3. Check browser console (F12) for fetch errors
4. Ensure the static path resolves — the frontend is served from `frontend/static/`

#### Azure Event Hub connection fails

```bash
# Verify your connection string format:
# Endpoint=sb://<namespace>.servicebus.windows.net/;SharedAccessKeyName=...;SharedAccessKey=...

# Test connectivity
uv run python -c "
from azure.eventhub import EventHubProducerClient
client = EventHubProducerClient.from_connection_string('<YOUR_CONN_STRING>', eventhub_name='adverse-events')
print(client.get_eventhub_properties())
client.close()
"
```

#### Synapse connection timeout

- Verify `SYNAPSE_SERVER` ends with `.sql.azuresynapse.net`
- Confirm the firewall rule allows your IP in Synapse Studio → Networking
- Ensure the dedicated SQL pool is **not paused** (pools auto-pause after inactivity)

#### Stream Analytics query "not compiling"

The ASA query won't compile until **all input/output aliases** are configured:

1. Add **Input** first: alias must be `adverse-events` (Event Hub, JSON, UTF-8)
2. Add **Output** `raw-events` → ADLS Gen2 `bronze` container (JSON)
3. Add **Output** `signal-alerts` → Event Hub `signal-alerts` (JSON)
4. **Then** paste the query and click **Save query**

Common ASA SQL pitfalls:
- `NEWID()` is not supported — use `CONCAT(...)` for synthetic IDs
- `SUM(COUNT(*)) OVER (PARTITION BY ...)` is not supported — no nested aggregates over analytic windows
- CTE-to-CTE JOINs on the same stream often fail — use a single `GROUP BY` + `HAVING` instead
- `TIMESTAMP BY` must reference a field that exists in the input schema

---

## Coverage matrix

| Layer | Local demo | Azure demo |
|---|---|---|
| Synthetic event generation | ✅ fixture mode | ✅ live → Event Hub |
| Signal detection logic | ✅ local detector | ✅ Stream Analytics query |
| Signal storage | ✅ in-memory | ✅ Synapse SQL Pool |
| Signal read-back | ✅ from Synapse if configured, else in-memory | ✅ Synapse SQL Pool |
| Bronze landing | stub | ✅ ADLS Gen2 via ASA |
| Gold aggregation | stub | ✅ Synapse Spark notebook |
| Dashboard | ✅ FastAPI + JS | ✅ same, reads from Synapse |

---

## Tech stack

Python 3.11+ · FastAPI · Azure Event Hubs · Azure Stream Analytics · Azure Synapse Analytics · ADLS Gen2 · Terraform · uv · ruff · pytest
