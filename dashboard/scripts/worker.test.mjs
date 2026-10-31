import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker.mjs";
const req = (path, method = "GET") =>
  new Request("https://example.test" + path, { method });
test("static files bypass data logic", async () => {
  const r = await worker.fetch(req("/assets/app.js"), {
    ASSETS: { fetch: () => new Response("asset") },
  });
  assert.equal(await r.text(), "asset");
});
test("only manifest and content hashes can reach object storage", async () => {
  let reads = 0;
  const env = {
    DATA: {
      get: async () => {
        reads++;
        return null;
      },
    },
  };
  for (const path of [
    "/data/secret.json",
    "/data/objects/nope.json",
    "/data/manifest.json/extra",
  ])
    assert.equal((await worker.fetch(req(path), env)).status, 404);
  assert.equal(reads, 0);
  assert.equal(
    (await worker.fetch(req("/data/manifest.json", "POST"), env)).status,
    405,
  );
});
test("unconfigured storage has a clear non-cacheable response", async () => {
  const r = await worker.fetch(req("/data/manifest.json"), {});
  assert.equal(r.status, 503);
  assert.equal(r.headers.get("cache-control"), "no-store");
});
test("immutable data streams gzip without decoding", async () => {
  const r = await worker.fetch(
    req("/data/objects/" + "a".repeat(64) + ".json.gz"),
    {
      DATA: {
        get: async () => ({ body: "bytes", httpEtag: '"abc"', size: 5 }),
      },
    },
  );
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("content-encoding"), "gzip");
  assert.match(r.headers.get("cache-control"), /immutable/);
  assert.equal(await r.text(), "bytes");
});
test("manifest revalidates quickly and conditional requests return 304", async () => {
  const r = await worker.fetch(req("/data/manifest.json"), {
    DATA: { get: async () => ({ httpEtag: '"abc"', size: 0 }) },
  });
  assert.equal(r.status, 304);
  assert.equal(r.headers.get("cache-control"), "private, max-age=60");
});
test("object-store failures do not expose exception text", async () => {
  const r = await worker.fetch(req("/data/manifest.json"), {
    DATA: {
      get: async () => {
        throw Error("secret");
      },
    },
  });
  assert.equal(r.status, 503);
  assert.doesNotMatch(await r.text(), /secret/);
});
