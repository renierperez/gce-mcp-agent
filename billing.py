import logging
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPIError
import pandas as pd
import asyncio

logger = logging.getLogger(__name__)

# Hardcoded for now based on exploration
BILLING_PROJECT_ID = "billing-logs-359516"
BILLING_TABLE_ID = "billing-logs-359516.detailed_billing_export.gcp_billing_export_resource_v1_01D318_0476A9_4CF367"

class BillingService:
    def __init__(self, billing_project_id=BILLING_PROJECT_ID, table_id=BILLING_TABLE_ID):
        # Initialize client with local/agent project for computing jobs, 
        # NOT the billing project (data source).
        # We trust the environment to provide the correct project ID (autonomous-agent-...)
        # or we could explicitly pass it if needed.
        self.client = bigquery.Client() 
        self.table_id = table_id

    def get_instance_cost_sync(self, target_project_id: str, instance_name: str, days: int = 30, extra_resource_names: list = None):
        """
        Calculates the True Cost (Net Cost) of a GCE instance from BigQuery Billing Export.
        Sync version to be run in executor.
        """
        # Build extra resources filter if provided
        extra_condition = ""
        if extra_resource_names:
            names_list = "', '".join(extra_resource_names)
            extra_condition = f"OR resource.name IN ('{names_list}')"
            for name in extra_resource_names:
                extra_condition += f"\n            OR ENDS_WITH(resource.name, '/{name}')"

        query = f"""
        SELECT
          sku.description AS sku_description,
          ROUND(SUM(cost), 2) AS gross_cost,
          ROUND(SUM((SELECT SUM(c.amount) FROM UNNEST(credits) AS c)), 2) AS total_credits,
          ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)), 2) AS net_cost,
          currency
        FROM
          `{self.table_id}`
        WHERE
          usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
          AND project.id = '{target_project_id}'
          AND (
            -- 1. Coincidencia exacta del nombre
            resource.name = '{instance_name}'
            -- 2. Si es un path completo, debe terminar exactamente en /nombre
            OR ENDS_WITH(resource.name, '/{instance_name}')
            -- 3. Etiquetas con valor exacto
            OR EXISTS(SELECT 1 FROM UNNEST(labels) WHERE value = '{instance_name}')
            OR EXISTS(SELECT 1 FROM UNNEST(system_labels) WHERE value = '{instance_name}')
            -- 4. Recursos adicionales (ej. discos con otros nombres)
            {extra_condition}
          )
        GROUP BY
          sku_description, currency
        ORDER BY
          net_cost DESC
        """
        
        try:
            query_job = self.client.query(query)
            df = query_job.to_dataframe()
            
            if df.empty:
                return None
            
            total_net = df['net_cost'].sum()
            currency = df['currency'].iloc[0] if not df.empty else "USD"
            
            return {
                "total_net_cost": float(total_net),
                "currency": currency,
                "breakdown": df.to_dict(orient="records")
            }

        except GoogleAPIError as e:
            logger.error(f"Error querying BigQuery for {instance_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in cost calculation for {instance_name}: {e}")
            return None

    async def get_instance_cost(self, target_project_id: str, instance_name: str, days: int = 30, extra_resource_names: list = None):
        """Async wrapper for get_instance_cost_sync."""
        return await asyncio.to_thread(self.get_instance_cost_sync, target_project_id, instance_name, days, extra_resource_names)

    def get_instance_sku_details_sync(self, target_project_id: str, instance_name: str, days: int = 30, extra_resource_names: list = None):
        """
        Fetches detailed SKU breakdown for a specific instance.
        """
        
        # Build extra resources filter if provided
        extra_condition = ""
        if extra_resource_names:
            # Safely format names for SQL
            names_list = "', '".join(extra_resource_names)
            # Create OR conditions for both exact name and full path (ENDS_WITH)
            # This handles cases where resource.name is just 'disk-2' or 'projects/.../disks/disk-2'
            extra_condition = f"""
            OR resource.name IN ('{names_list}')
            """
            # Also add ENDS_WITH for each extra resource to be safe
            for name in extra_resource_names:
                extra_condition += f"\n            OR ENDS_WITH(resource.name, '/{name}')"

        query = f"""
        SELECT
          sku.id AS sku_id,
          sku.description AS sku_description,
          -- Costo Neto (con créditos aplicados)
          ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)), 2) AS net_cost,
          -- Costo Bruto
          ROUND(SUM(cost), 2) AS gross_cost,
          currency,
          usage.unit AS usage_unit,
          SUM(usage.amount) AS total_usage_amount,
          -- Esto nos ayudará a ver bajo qué nombre exacto está reportando el cómputo
          ANY_VALUE(resource.name) AS sample_resource_name 
        FROM
          `{self.table_id}`
        WHERE
          usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
          AND project.id = '{target_project_id}'
          AND (
            -- 1. Coincidencia exacta del nombre
            resource.name = '{instance_name}'
            -- 2. Si es un path completo, debe terminar exactamente en /nombre
            OR ENDS_WITH(resource.name, '/{instance_name}')
            -- 3. Etiquetas con valor exacto
            OR EXISTS(SELECT 1 FROM UNNEST(labels) WHERE value = '{instance_name}')
            OR EXISTS(SELECT 1 FROM UNNEST(system_labels) WHERE value = '{instance_name}')
            -- 4. Recursos adicionales (ej. discos con otros nombres)
            {extra_condition}
          )
        GROUP BY
          sku_id,
          sku_description,
          currency,
          usage_unit
        -- FILTRO CRÍTICO: Solo mostrar si alguno de los dos costos es distinto de cero
        HAVING 
          net_cost != 0 OR gross_cost != 0
        ORDER BY
          net_cost DESC
        """
        
        try:
            query_job = self.client.query(query)
            df = query_job.to_dataframe()
            
            if df.empty:
                return None
            
            return df.to_dict(orient="records")

        except GoogleAPIError as e:
            logger.error(f"Error querying BigQuery SKU details for {instance_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in SKU details for {instance_name}: {e}")
            return None

    async def get_instance_sku_details(self, target_project_id: str, instance_name: str, days: int = 30, extra_resource_names: list = None):
        """Async wrapper for get_instance_sku_details_sync."""
        return await asyncio.to_thread(self.get_instance_sku_details_sync, target_project_id, instance_name, days, extra_resource_names)
