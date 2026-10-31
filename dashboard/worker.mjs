// Portable serving layer. Account, domains, R2 bucket identity and access live in infra.
const objectPattern = /^objects\/[a-f0-9]{64}\.json(?:\.gz)?$/;
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/data/")) return env.ASSETS.fetch(request);
    if (!["GET", "HEAD"].includes(request.method))
      return new Response("Method not allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    const key = url.pathname.slice(6);
    if (key !== "manifest.json" && !objectPattern.test(key))
      return new Response("Not found", { status: 404 });
    if (!env.DATA)
      return Response.json(
        { error: "Historical data has not been published yet." },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    try {
      const object = await env.DATA.get(key, { onlyIf: request.headers });
      if (!object) return new Response("Not found", { status: 404 });
      const headers = new Headers({
        "Content-Type": "application/json",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control":
          key === "manifest.json"
            ? "public, max-age=60"
            : "public, max-age=31536000, immutable",
        ETag: object.httpEtag,
      });
      if (key.endsWith(".gz")) headers.set("Content-Encoding", "gzip");
      if (!("body" in object))
        return new Response(null, { status: 304, headers });
      headers.set("Content-Length", String(object.size));
      return new Response(request.method === "HEAD" ? null : object.body, {
        headers,
      });
    } catch {
      return Response.json(
        { error: "Historical data is temporarily unavailable." },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
  },
};
