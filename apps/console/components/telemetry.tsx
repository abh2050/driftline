"use client";

import { useEffect } from "react";

let started = false;

// Browser spans go through ops-api to otel-collector (spec 5.2.4). Fetches to /api/ops carry
// traceparent, so a click joins the trace of the ops-api call it causes.
export function Telemetry() {
  useEffect(() => {
    if (started || process.env.NEXT_PUBLIC_BROWSER_TRACES === "off") return;
    started = true;
    void (async () => {
      const [{ WebTracerProvider }, { BatchSpanProcessor }, { OTLPTraceExporter }, { FetchInstrumentation }, { registerInstrumentations }, { resourceFromAttributes }] =
        await Promise.all([
          import("@opentelemetry/sdk-trace-web"),
          import("@opentelemetry/sdk-trace-base"),
          import("@opentelemetry/exporter-trace-otlp-http"),
          import("@opentelemetry/instrumentation-fetch"),
          import("@opentelemetry/instrumentation"),
          import("@opentelemetry/resources"),
        ]);
      const provider = new WebTracerProvider({
        resource: resourceFromAttributes({ "service.name": "console" }),
        spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter({ url: "/api/ops/v1/traces" }))],
      });
      provider.register();
      registerInstrumentations({
        instrumentations: [
          new FetchInstrumentation({
            propagateTraceHeaderCorsUrls: [/\/api\/ops\//],
            ignoreUrls: [/\/api\/ops\/v1\/traces/, /\/api\/ops\/stream/],
          }),
        ],
      });
    })();
  }, []);
  return null;
}
