import { chromium } from "playwright";
import assert from "node:assert/strict";
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(
    (process.env.DASHBOARD_URL || "http://127.0.0.1:4173") + "/?demo=1",
  );
  await page
    .getByRole("heading", { name: "Book replay", exact: true })
    .waitFor();
  await page.getByText("Quoted book", { exact: true }).waitFor();
  await page.getByLabel("COLLECTION DATE · EASTERN TIME").waitFor();
  await page.getByText("08:00:00 EDT", { exact: true }).waitFor();
  assert.ok(await page.locator(".session-regular").count());
  assert.ok(await page.locator(".session-extended").count());
  await page.getByLabel("Search instruments").fill("100.0000");
  const legList = page.getByLabel("DEMO-BOX legs");
  assert.match(await legList.innerText(), /100\.0000.*2026-10-30/);
  await page.getByLabel("Search instruments").fill("2026-10-30");
  assert.equal(await page.locator("button.instrument").count(), 2);
  await page.getByLabel("Search instruments").fill("CONDOR");
  await page
    .getByRole("heading", { name: "DEMO-CONDOR", exact: true })
    .waitFor();
  await page.getByLabel("Search instruments").fill("BOX");
  await page.getByRole("heading", { name: "DEMO-BOX", exact: true }).waitFor();
  await page.getByLabel("Observation", { exact: true }).fill("10");
  await page.getByText("Capture failed", { exact: true }).waitFor();
  assert.equal(
    await page.getByText("The previous book is not carried forward.").count(),
    1,
  );
  await page.getByLabel("Observation", { exact: true }).fill("15");
  await page.getByText("Valid empty book", { exact: true }).waitFor();
  await page.screenshot({
    path: "/tmp/qsb-dashboard-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: "/tmp/qsb-dashboard-mobile.png",
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  console.log(
    "ok browser: Eastern times, session shading, leg strike/expiry search, replay transitions, responsive layout, no runtime errors",
  );
} finally {
  await browser.close();
}
