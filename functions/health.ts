const HOP_BY_HOP_HEADERS = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

export const onRequest: PagesFunction = async (context) => {
  const url = new URL(context.request.url);

  // Construct the target URL for backend health check
  const env = context.env as Record<string, string | undefined> | undefined;
  const backendBase = env?.BACKEND_URL || "https://phucsd-oloka-voice-studio.hf.space";
  const targetUrl = `${backendBase.replace(/\/$/, "")}/health${url.search}`;

  // Clone request headers cleanly for upstream subrequest
  const headers = new Headers(context.request.headers);
  headers.delete("host");
  for (const h of HOP_BY_HOP_HEADERS) {
    headers.delete(h);
  }

  const proxyRequest = new Request(targetUrl, {
    method: context.request.method,
    headers,
    body: context.request.method !== "GET" && context.request.method !== "HEAD" ? context.request.body : null,
    redirect: "follow",
  });

  try {
    const response = await fetch(proxyRequest, {
      cf: { cacheEverything: false },
    } as RequestInit);

    const respHeaders = new Headers(response.headers);
    for (const h of HOP_BY_HOP_HEADERS) {
      respHeaders.delete(h);
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: respHeaders,
    });
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error);
    return new Response(
      JSON.stringify({
        detail: `Cloudflare Pages Health Proxy Error: ${msg}`,
      }),
      {
        status: 502,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
};
