/**
 * Application Insights via the Azure Monitor OpenTelemetry distro: requests, outgoing
 * HTTP, PostgreSQL calls, exceptions and logs. Only on the Node runtime, and only when
 * App Service provides a connection string, so local runs and tests send nothing.
 */
export async function register() {
  const connectionString = process.env.APPLICATIONINSIGHTS_CONNECTION_STRING;
  if (process.env.NEXT_RUNTIME !== "nodejs" || !connectionString) return;
  // Aliased: the SDK's "use" prefix is not a React hook, but the hooks lint rule treats it as one.
  const { useAzureMonitor: startAzureMonitor } = await import("@azure/monitor-opentelemetry");
  startAzureMonitor({
    azureMonitorExporterOptions: { connectionString },
    enableLiveMetrics: false,
  });
}
