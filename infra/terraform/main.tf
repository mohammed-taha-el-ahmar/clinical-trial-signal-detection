terraform {
  required_version = ">= 1.7"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}

provider "azurerm" {
  features {}
}

# ── Variables ──────────────────────────────────────────────────────────────────

variable "location"   { default = "westeurope" }
variable "prefix"     { default = "pharmasight" }
variable "env"        { default = "dev" }
variable "sql_admin"  { description = "Synapse SQL admin username" }
variable "sql_password" {
  description = "Synapse SQL admin password"
  sensitive   = true
}

locals {
  name = "${var.prefix}-${var.env}"
  tags = { project = "pharmasight", env = var.env }
}

# ── Resource group ─────────────────────────────────────────────────────────────

resource "azurerm_resource_group" "rg" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# ── Event Hub namespace + hub ──────────────────────────────────────────────────

resource "azurerm_eventhub_namespace" "ns" {
  name                = "evhns-${local.name}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "Standard"
  capacity            = 1
  tags                = local.tags
}

resource "azurerm_eventhub" "adverse_events" {
  name                = "adverse-events"
  namespace_name      = azurerm_eventhub_namespace.ns.name
  resource_group_name = azurerm_resource_group.rg.name
  partition_count     = 4
  message_retention   = 1
}

resource "azurerm_eventhub" "signal_alerts" {
  name                = "signal-alerts"
  namespace_name      = azurerm_eventhub_namespace.ns.name
  resource_group_name = azurerm_resource_group.rg.name
  partition_count     = 2
  message_retention   = 1
}

resource "azurerm_eventhub_authorization_rule" "sender" {
  name                = "simulator-sender"
  namespace_name      = azurerm_eventhub_namespace.ns.name
  eventhub_name       = azurerm_eventhub.adverse_events.name
  resource_group_name = azurerm_resource_group.rg.name
  listen = false
  send   = true
  manage = false
}

# ── ADLS Gen2 (bronze landing) ─────────────────────────────────────────────────

resource "azurerm_storage_account" "adls" {
  name                     = "adls${replace(local.name, "-", "")}${substr(md5(local.name), 0, 4)}"
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  is_hns_enabled           = true   # hierarchical namespace = ADLS Gen2
  tags                     = local.tags
}

resource "azurerm_storage_container" "bronze" {
  name                  = "bronze"
  storage_account_name  = azurerm_storage_account.adls.name
  container_access_type = "private"
}

# ── Azure Stream Analytics job ─────────────────────────────────────────────────

resource "azurerm_stream_analytics_job" "asa" {
  name                                     = "asa-${local.name}"
  resource_group_name                      = azurerm_resource_group.rg.name
  location                                 = azurerm_resource_group.rg.location
  compatibility_level                      = "1.2"
  data_locale                              = "en-GB"
  events_late_arrival_max_delay_in_seconds = 60
  events_out_of_order_max_delay_in_seconds = 10
  events_out_of_order_policy               = "Adjust"
  output_error_policy                      = "Drop"
  streaming_units                          = 3
  transformation_query                     = file("${path.module}/../../stream_analytics/signal_query.sql")
  tags                                     = local.tags
}

# ── Azure Synapse Analytics workspace ─────────────────────────────────────────

resource "azurerm_synapse_workspace" "synapse" {
  name                                 = "synw-${local.name}"
  resource_group_name                  = azurerm_resource_group.rg.name
  location                             = azurerm_resource_group.rg.location
  storage_data_lake_gen2_filesystem_id = azurerm_storage_data_lake_gen2_filesystem.synapse_fs.id
  sql_administrator_login              = var.sql_admin
  sql_administrator_login_password     = var.sql_password
  tags                                 = local.tags

  identity { type = "SystemAssigned" }
}

resource "azurerm_storage_data_lake_gen2_filesystem" "synapse_fs" {
  name               = "synapse"
  storage_account_id = azurerm_storage_account.adls.id
}

resource "azurerm_synapse_sql_pool" "clinical_dw" {
  name                 = "clinicaldw"
  synapse_workspace_id = azurerm_synapse_workspace.synapse.id
  sku_name             = "DW100c"
  create_mode          = "Default"
  tags                 = local.tags
}

# ── Outputs ────────────────────────────────────────────────────────────────────

output "eventhub_namespace" {
  value = azurerm_eventhub_namespace.ns.name
}

output "simulator_connection_string" {
  value     = azurerm_eventhub_authorization_rule.sender.primary_connection_string
  sensitive = true
}

output "synapse_workspace_name" {
  value = azurerm_synapse_workspace.synapse.name
}

output "synapse_sql_endpoint" {
  value = azurerm_synapse_workspace.synapse.connectivity_endpoints["sql"]
}

output "adls_account_name" {
  value = azurerm_storage_account.adls.name
}
