/**
 * Application Insights via the Azure Monitor OpenTelemetry distro: requests, outgoing
 * HTTP, PostgreSQL calls, exceptions and logs. Only on the Node runtime, and only when
 * App Service provides a connection string, so local runs and tests send nothing.
 */
export async function register() {
  const connectionString = process.env.APPLICATIONINSIGHTS_CONNECTION_STRING;
  if (process.env.NEXT_RUNTIME !== "nodejs" || !connectionString) return;
  const { useAzureMonitor } = await import("@azure/monitor-opentelemetry");
  useAzureMonitor({
    azureMonitorExporterOptions: { connectionString },
    enableLiveMetrics: false,
  });
}
