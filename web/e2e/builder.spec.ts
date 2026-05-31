import { expect, type Locator, type Page, test } from "@playwright/test";

/**
 * The headline E2E: drive the workflow builder in a real Chromium browser through
 * the full author → save → run → succeed loop. This is the proof that the builder
 * works end to end against the live stack (API + worker + Next), not just that its
 * components compile.
 *
 * The ten required steps map to the `test.step` blocks below. Selectors use
 * accessible roles/text and React Flow's own DOM classes (`.react-flow__node`,
 * `.react-flow__handle`) — no product-only test ids, so the suite proves the real
 * UI a user sees.
 *
 * One run, sequential and stateful (workers:1): each step depends on the prior, so
 * splitting into independent tests would just re-do the setup.
 */

const NODE = ".react-flow__node";
const PANE = ".react-flow";
const HANDLE_SOURCE = ".react-flow__handle.source";
const HANDLE_TARGET = ".react-flow__handle.target";

/** Center of a locator's bounding box (throws if not laid out). */
async function center(loc: Locator): Promise<{ x: number; y: number }> {
  const box = await loc.boundingBox();
  if (!box) throw new Error("element has no bounding box (not visible / not laid out)");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/**
 * Drag a React Flow node (by data-id) to an absolute viewport point. Callers
 * compute the target *inside the canvas pane's bounding box* — dragging to a
 * point outside the (overflow-hidden) canvas clips the node out of view and
 * makes its handles unreachable.
 */
async function moveNodeTo(page: Page, dataId: string, toX: number, toY: number): Promise<void> {
  const node = page.locator(`${NODE}[data-id="${dataId}"]`);
  const from = await center(node);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move((from.x + toX) / 2, (from.y + toY) / 2, { steps: 6 });
  await page.mouse.move(toX, toY, { steps: 6 });
  await page.mouse.up();
}

/**
 * Connect two nodes by dragging from the source handle of `fromId` to the target
 * handle of `toId`. Uses real pointer movement (Playwright's mouse API emits the
 * pointer events React Flow listens for), in steps so the connection line tracks,
 * with a hover on the target handle before release so React Flow registers a valid
 * drop target.
 */
async function connect(page: Page, fromId: string, toId: string): Promise<void> {
  const src = page.locator(`${NODE}[data-id="${fromId}"] ${HANDLE_SOURCE}`);
  const tgt = page.locator(`${NODE}[data-id="${toId}"] ${HANDLE_TARGET}`);
  const a = await center(src);
  const b = await center(tgt);
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 8 });
  await page.mouse.move(b.x, b.y, { steps: 8 });
  await page.mouse.move(b.x, b.y); // settle on the target handle before release
  await page.mouse.up();
}

test("builder: author, save, run, and reach SUCCEEDED", async ({ page }) => {
  const workflowName = `E2E builder ${Date.now()}`;

  await test.step("1. open /workflows/new", async () => {
    await page.goto("/workflows/new");
    await expect(page.getByRole("heading", { name: "New workflow." })).toBeVisible();
    await page.getByPlaceholder("Workflow name").fill(workflowName);
  });

  await test.step("2. add an Echo node from the palette", async () => {
    await page.getByRole("button", { name: /Echo/ }).click();
    await expect(page.locator(`${NODE}[data-id="n1"]`)).toBeVisible();
  });

  await test.step("3. add a second node (Set fields)", async () => {
    await page.getByRole("button", { name: /Set fields/ }).click();
    await expect(page.locator(`${NODE}[data-id="n2"]`)).toBeVisible();
    await expect(page.locator(NODE)).toHaveCount(2);
  });

  await test.step("4. arrange the nodes so their handles are reachable", async () => {
    // Spawn positions can overlap (180px-wide nodes only ~60px apart), which would
    // occlude n1's source handle under n2. Spread them out — but compute targets
    // INSIDE the canvas pane's box, since the canvas is overflow-hidden and a drag
    // to an off-canvas point clips the node out of view (its handles unreachable).
    const pane = await center(page.locator(PANE));
    const paneBox = await page.locator(PANE).boundingBox();
    if (!paneBox) throw new Error("canvas pane has no bounding box");
    // n1 upper-left quadrant, n2 lower-right quadrant — both comfortably inside.
    await moveNodeTo(page, "n1", paneBox.x + paneBox.width * 0.3, pane.y - paneBox.height * 0.15);
    await moveNodeTo(page, "n2", paneBox.x + paneBox.width * 0.7, pane.y + paneBox.height * 0.15);
  });

  await test.step("5. connect n1 → n2", async () => {
    await connect(page, "n1", "n2");
    await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  });

  await test.step("6. edit the second node's config", async () => {
    await page.locator(`${NODE}[data-id="n2"]`).click();
    const config = page.locator("aside textarea");
    await expect(config).toBeVisible();
    await config.fill('{"fields":{"e2e":"verified"}}');
    // No "Invalid JSON" warning means the edit was accepted into node state.
    await expect(page.getByText("Invalid JSON")).toHaveCount(0);
  });

  await test.step("7. save the workflow", async () => {
    await page.getByRole("button", { name: "Create workflow" }).click();
    // Save navigates to the detail page on success.
    await page.waitForURL(/\/workflows\/[0-9a-f-]{36}$/, { timeout: 20_000 });
  });

  await test.step("8. verify the workflow appears", async () => {
    await expect(page.getByRole("heading", { name: workflowName })).toBeVisible();
    await expect(page.getByText("Graph (current version)")).toBeVisible();
    // The saved graph round-tripped: two nodes render on the detail canvas.
    await expect(page.locator(NODE)).toHaveCount(2);
  });

  await test.step("9. execute the workflow", async () => {
    await page.getByRole("button", { name: "Run workflow" }).click();
    // Starting a run navigates to the live run viewer.
    await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/, { timeout: 20_000 });
    await expect(page.getByRole("heading", { name: "Run." })).toBeVisible();
  });

  await test.step("10. verify the run reaches SUCCEEDED", async () => {
    // The worker (live in global-setup) claims + runs the nodes; the viewer's
    // WebSocket pushes events until the terminal RunSucceeded, then refreshes the
    // node table. Assert on the unambiguous terminal event + the header status.
    await expect(page.getByText("RunSucceeded")).toBeVisible({ timeout: 30_000 });
    await expect(
      page.locator("span").filter({ hasText: /^succeeded$/ }).first(),
    ).toBeVisible({ timeout: 30_000 });
  });
});
