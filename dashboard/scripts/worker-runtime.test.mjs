import test from "node:test";
import assert from "node:assert/strict";
import { gzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

test("Workers serves stored gzip as JSON after one HTTP decompression", async () => {
  const runtime = new Miniflare(
    convertV4MiniflareOptions({
      modules: true,
      scriptPath: fileURLToPath(new URL("../worker.mjs", import.meta.url)),
      compatibilityDate: "2026-10-30",
      r2Buckets: ["DATA"],
    }),
  );
  try {
    const bucket = await runtime.getR2Bucket("DATA");
    const key = "objects/" + "a".repeat(64) + ".json.gz";
    const document = { schema_version: 1, instruments: [{ symbol: "TEST" }] };
    const compressed = gzipSync(JSON.stringify(document));
    await bucket.put(key, compressed);
    // Use an actual HTTP request so the runtime's Content-Encoding handling runs.
    const url = new URL("/data/" + key, await runtime.ready);
    const response = await fetch(url);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-encoding"), "gzip");
    assert.deepEqual(await response.json(), document);
    const head = await fetch(url, { method: "HEAD" });
    assert.equal(head.status, 200);
    assert.equal(head.headers.get("content-length"), String(compressed.length));
    assert.equal((await head.arrayBuffer()).byteLength, 0);
  } finally {
    await runtime.dispose();
  }
});
