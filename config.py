"""Shared co    # Azure Synapse / SQL pool
synapse_server: str = ""  # e.g. myworkspace.sql.azuresynapse.net
synapse_database: str = "clinicaldw"
synapse_username: str = ""uration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Azure Event Hub
    eventhub_connection_string: str = ""
    eventhub_name: str = "adverse-events"

    # Azure Synapse / SQL pool
    synapse_server: str = ""  # e.g. myworkspace.sql.azuresynapse.net
    synapse_database: str = "clinical_dw"
    synapse_username: str = ""
    synapse_password: str = ""

    # Signal detection thresholds
    signal_rate_threshold: float = 0.05  # 5 % incidence rate triggers signal
    signal_window_minutes: int = 10  # tumbling window size
    signal_min_events: int = 3  # minimum events before evaluating

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Simulator
    simulator_events_per_second: float = 2.0
    simulator_signal_injection_rate: float = 0.08  # 8 % synthetic hot events


settings = Settings()
