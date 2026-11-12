import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const demoRoot = new URL("../public/demo-data/", import.meta.url);
const manifest = JSON.parse(await readFile(new URL("manifest.json", demoRoot)));
const source = JSON.parse(
  await readFile(new URL(manifest.days[0].path, demoRoot)),
);
const documents = new Map();
const days = ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-06"].map(
  (date) => {
    const index = structuredClone(source);
    index.date = date;
    for (const instrument of index.instruments) {
      instrument.details = [];
      for (const hour of instrument.hourly) hour.at = date + hour.at.slice(10);
    }
    const body = JSON.stringify(index);
    const path = `objects/${createHash("sha256").update(body).digest("hex")}.json`;
    documents.set(`/demo-data/${path}`, body);
    return { ...manifest.days[0], date, path, has_detail: false };
  },
);
documents.set(
  "/demo-data/manifest.json",
  JSON.stringify({ ...manifest, days }),
);
const url = (process.env.DASHBOARD_URL || "http://127.0.0.1:4173") + "/?demo=1";
const browser = await chromium.launch({ headless: true });
try {
  // Cross the DST boundary, with browsers on both sides of Eastern Time.
  for (const timezoneId of ["America/Los_Angeles", "Asia/Tokyo"]) {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
      timezoneId,
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/demo-data/**", async (route) => {
      const body = documents.get(new URL(route.request().url()).pathname);
      assert.ok(
        body,
        "Only the expected archive partitions should be requested",
      );
      await route.fulfill({ contentType: "application/json", body });
    });
    await page.goto(url);
    const trigger = page.getByRole("button", { name: /Collection date/ });
    const dialog = page.getByRole("dialog", { name: "Choose collection date" });
    await page
      .getByRole("heading", { name: "DEMO-BOX", exact: true })
      .waitFor();
    assert.match(await trigger.innerText(), /2026-11-06/);
    await page
      .getByLabel("Strategy type", { exact: true })
      .selectOption("BOX_SPREAD");
    await page
      .getByLabel("Expiration date", { exact: true })
      .selectOption("2026-10-30");

    await trigger.focus();
    await page.keyboard.press("Enter");
    await dialog.waitFor();
    await page.locator(".rdp-selected button:focus").waitFor();
    for (const date of [/November 1st/, /November 3rd/, /November 7th/]) {
      assert.ok(await dialog.getByRole("button", { name: date }).isDisabled());
    }
    // An unavailable day cannot change the date or close the calendar.
    await dialog
      .getByRole("button", { name: /November 3rd/ })
      .click({ force: true });
    assert.match(await trigger.innerText(), /2026-11-06/);
    assert.ok(await dialog.isVisible());
    await dialog.getByRole("button", { name: /November 2nd/ }).click();
    await dialog.waitFor({ state: "hidden" });
    await page.locator("#date:focus").waitFor();
    assert.match(await trigger.innerText(), /2026-11-02/);
    await page.getByText(/Eastern collection date 2026-11-02/).waitFor();
    assert.equal(
      await page.getByLabel("Strategy type", { exact: true }).inputValue(),
      "ALL",
    );
    assert.equal(
      await page.getByLabel("Expiration date", { exact: true }).inputValue(),
      "ALL",
    );

    // Arrow navigation skips the closed weekend; Enter selects the prior Friday.
    await trigger.press("Enter");
    await page.locator(".rdp-selected button:focus").waitFor();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("Enter");
    await dialog.waitFor({ state: "hidden" });
    assert.match(await trigger.innerText(), /2026-10-30/);
    await page.getByText(/Eastern collection date 2026-10-30/).waitFor();

    await trigger.click();
    await dialog.getByRole("grid", { name: "October 2026" }).waitFor();
    assert.ok(
      await dialog
        .getByRole("button", { name: "Go to the Previous Month" })
        .isDisabled(),
    );
    await dialog.getByLabel("Choose the Month").selectOption("10");
    await dialog.getByRole("grid", { name: "November 2026" }).waitFor();
    assert.ok(
      await dialog
        .getByRole("button", { name: "Go to the Next Month" })
        .isDisabled(),
    );
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden" });
    await page.locator("#date:focus").waitFor();
    assert.match(await trigger.innerText(), /2026-10-30/);

    await trigger.click();
    await dialog.getByRole("grid", { name: "October 2026" }).waitFor();
    await page
      .getByRole("heading", { name: /Explore the complex book/ })
      .click();
    await dialog.waitFor({ state: "hidden" });

    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await trigger.click();
      await dialog.waitFor();
      const bounds = await dialog.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      if (timezoneId === "America/Los_Angeles" && [1440, 390].includes(width)) {
        await page.screenshot({ path: `/tmp/qsb-date-picker-${width}.png` });
      }
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "hidden" });
    }
    assert.deepEqual(errors, []);
    await page.close();
  }

  const empty = await browser.newPage();
  await empty.route("**/demo-data/manifest.json", (route) =>
    route.fulfill({ json: { ...manifest, days: [] } }),
  );
  await empty.goto(url);
  await empty
    .getByRole("heading", {
      name: "The archive is ready for its first session",
    })
    .waitFor();
  assert.ok(
    await empty.getByRole("button", { name: /Collection date/ }).isDisabled(),
  );
  await empty.close();
  console.log(
    "ok date picker: available dates, filter resets, Eastern dates across DST/timezones, keyboard and month navigation, dismissal/focus, responsive layout, empty archive",
  );
} finally {
  await browser.close();
}
