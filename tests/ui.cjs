// Run with node tests/ui.cjs; set PLAYWRIGHT_MODULE if Playwright is installed elsewhere.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const fixture = {
  status: { active: { email: "ana@example.com", managed: true } },
  list: {
    activeAccountNumber: 1,
    accounts: [
      {
        number: 1,
        email: "ana@example.com",
        alias: "Personal",
        active: true,
        usage: {
          fiveHour: { pct: 42, resetsAt: "2099-09-17T20:00:00Z" },
          sevenDay: { pct: 68 },
        },
      },
      {
        number: 2,
        email: "work@example.com",
        alias: "Trabajo",
        usage: { fiveHour: { pct: 92 }, sevenDay: { pct: 20 } },
      },
      {
        number: 3,
        email: "o'connor@example.com",
        alias: '<img src=x onerror="window.injected=true">',
        disabled: true,
        usage: null,
      },
    ],
  },
  projects: Array.from({ length: 9 }, (_, i) => ({
    name: i === 0 ? "Proyecto de Ana's" : `Proyecto ${i + 1}`,
    path: i === 0 ? "/tmp/Ana's project" : `/tmp/project-${i + 1}`,
    exists: i !== 1,
  })),
  auto_running: false,
  auto_config: { threshold: 90, strategy: "best" },
};
let failStatus = false,
  failurePath = "",
  delayMutation = false;
const mutations = [];
const server = http.createServer(async (req, res) => {
  if (req.url === "/api/status") {
    res.writeHead(failStatus ? 503 : 200, {
      "Content-Type": "application/json",
    });
    res.end(
      JSON.stringify(
        failStatus
          ? { error: "Servicio temporalmente no disponible." }
          : fixture,
      ),
    );
    return;
  }
  if (req.method === "POST") {
    let body = "";
    for await (const part of req) body += part;
    mutations.push({ path: req.url, body: JSON.parse(body) });
    if (delayMutation) await new Promise((resolve) => setTimeout(resolve, 300));
    res.writeHead(req.url === failurePath ? 502 : 200, {
      "Content-Type": "application/json",
    });
    res.end(
      JSON.stringify(
        req.url === failurePath
          ? { success: false, error: "No se pudo guardar el cambio." }
          : { success: true },
      ),
    );
    return;
  }
  const file = {
    "/": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
    "/assets/claudio.svg": "assets/claudio.svg",
  }[req.url];
  if (!file) {
    res.writeHead(404);
    res.end();
    return;
  }
  res.writeHead(200, {
    "Content-Type": file.endsWith("svg") ? "image/svg+xml" : file.endsWith("css")
      ? "text/css"
      : file.endsWith("js")
        ? "text/javascript"
        : "text/html",
  });
  res.end(fs.readFileSync(path.join(root, file)));
});
(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROMIUM_PATH
      ? { executablePath: process.env.CHROMIUM_PATH }
      : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(
      () => document.querySelector("#stat-accounts").textContent === "3",
    );
    assert.equal(await page.locator(".account-card").count(), 3);
    assert.equal(await page.locator(".project-card").count(), 6);
    assert.equal(await page.locator(".account-card img").count(), 0);
    assert.match(
      await page.locator(".account-card").nth(2).innerText(),
      /Sin datos/,
    );
    await page.locator("#account-search").fill("trabajo");
    assert.equal(await page.locator(".account-card").count(), 1);
    await page.locator("#account-search").fill("");
    await page.locator("#account-filter").selectOption("warning");
    assert.equal(await page.locator(".account-card").count(), 1);
    await page.locator("#account-filter").selectOption("all");
    await page.locator("#account-sort").selectOption("quota");
    assert.match(
      await page.locator(".account-card").first().innerText(),
      /Personal/,
    );
    await page.locator("#show-projects").click();
    assert.equal(await page.locator(".project-card").count(), 9);
    await page.locator("#project-search").fill("project-9");
    assert.equal(await page.locator(".project-card").count(), 1);
    await page.locator(".favorite").click();
    await page.locator("#project-search").fill("");
    await page.locator("#project-filter").selectOption("favorites");
    assert.equal(await page.locator(".project-card").count(), 1);
    await page.reload();
    await page.waitForFunction(
      () => document.querySelector("#stat-accounts").textContent === "3",
    );
    assert.equal(
      await page.locator(".favorite").first().getAttribute("aria-pressed"),
      "true",
    );
    await page.locator("#theme-button").click();
    await page.reload();
    await page.waitForFunction(
      () => document.querySelector("#stat-accounts").textContent === "3",
    );
    assert.equal(
      await page.locator("html").getAttribute("data-theme"),
      "light",
    );
    await page.locator("#theme-button").click();
    failurePath = "/api/alias";
    await page.locator('[data-action="alias"]').first().click();
    await page.locator("#alias-value").fill("Nuevo alias");
    await page.locator('#alias-form [type="submit"]').click();
    await page.locator("#alias-form .form-error").waitFor({ state: "visible" });
    assert.equal(await page.locator("#alias-dialog").getAttribute("open"), "");
    await page.keyboard.press("Escape");
    failurePath = "/api/toggle-account";
    await page.locator('[data-action="disable"]').first().click();
    await page.waitForFunction(
      () =>
        document.querySelector("#toast").textContent ===
        "No se pudo guardar el cambio.",
    );
    assert.equal(
      await page.locator("#toast").getAttribute("class"),
      "toast error",
    );
    failurePath = "";
    await page.locator("#project-search").fill("Ana's");
    await page.locator('.project-card [data-action="terminal"]').click();
    await page.waitForFunction(
      () => document.querySelector("#refresh-button").disabled === false,
    );
    assert.equal(mutations.at(-1).body.cwd, "/tmp/Ana's project");
    await page.locator('.project-card [data-action="parallel"]').click();
    await page.locator("#parallel-account").selectOption("2");
    await page.locator('#parallel-form [type="submit"]').click();
    await page.locator("#parallel-dialog").waitFor({ state: "hidden" });
    assert.equal(mutations.at(-1).body.mode, "parallel");
    assert.equal(mutations.at(-1).body.account, "2");
    // Backend failure retains the last known state and recovers on retry.
    failStatus = true;
    await page.locator("#refresh-button").click();
    await page.locator("#connection-error").waitFor({ state: "visible" });
    assert.equal(await page.locator("#stat-accounts").innerText(), "3");
    failStatus = false;
    await page.locator("#connection-error button").click();
    await page.locator("#connection-error").waitFor({ state: "hidden" });
    // Failed forms keep input; closing a token dialog clears the secret.
    await page.locator('[data-action="link"]').first().click();
    await page.locator("#tab-token").click();
    await page.locator("#token-value").fill("example-secret");
    await page.locator("#token-alias").fill("Trabajo");
    await page.locator('#link-form [type="submit"]').click();
    await page.locator("#link-form .form-error").waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await page.waitForFunction(
      () => document.querySelector("#token-value").value === "",
    );
    // Duplicate clicks cannot issue concurrent mutations.
    delayMutation = true;
    const before = mutations.length;
    await page
      .locator('[data-action="quick-switch"]')
      .first()
      .evaluate((button) => {
        button.click();
        button.click();
      });
    await page.waitForFunction(
      () =>
        document.querySelector('[data-action="quick-switch"]').disabled ===
        false,
    );
    assert.equal(mutations.length, before + 1);
    delayMutation = false;
    await page.locator("#project-search").fill("");
    await page.screenshot({ path: "/tmp/claudio-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({ path: "/tmp/claudio-mobile.png", fullPage: true });
    await page.locator('[data-action="link"]').first().click();
    assert.ok(await page.locator("#link-dialog").isVisible());
    await page.keyboard.press("Escape");
    assert.equal(errors.length, 0, errors.join("\n"));
    console.log(
      "UI OK: búsquedas, filtros, favoritos persistentes, temas, formularios, errores, sesiones, duplicados y móvil.",
    );
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  server.close();
  process.exitCode = 1;
});
