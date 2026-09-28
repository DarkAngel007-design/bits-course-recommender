import { expect, test } from "@playwright/test";

test("profile -> progress -> recommendation -> evidence -> plan", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /entering year III \(sem 5\)/ }).click();
  await expect(page.getByText(/Loaded synthetic demo profile/)).toBeVisible();

  // progress is calculated live from the profile
  await page.getByRole("tab", { name: "Progress" }).click();
  await expect(page.getByText("Curriculum resolved.")).toBeVisible();
  const row = page.getByRole("row", { name: /Discipline core \(CDC\)/ });
  await expect(row).toContainText("14 · 48 U");
  await expect(row).toContainText("7 · 26 U");

  // natural-language query with interpreted, editable intent
  await page.getByRole("tab", { name: "Recommendations" }).click();
  await page.getByLabel("Your request").fill("Suggest DELs related to AI.");
  await page.getByRole("button", { name: "Recommend", exact: true }).click();
  await expect(page.getByText(/verified recommendation/)).toBeVisible();
  await expect(page.locator(".intent-editor select").first()).toHaveValue("DEL");
  const first = page.locator("article.course.match").first();
  await expect(first).toContainText("Counts as DEL");
  await expect(first).toContainText("Eligible");

  // Repeating the same request must also succeed when the calculation is cached.
  const repeated = page.waitForResponse((r) => r.url().endsWith("/recommendations") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Recommend", exact: true }).click();
  expect((await repeated).status()).toBe(200);
  await expect(first).toContainText("Eligible");

  // evidence drawer resolves to a document page
  await first.getByRole("button", { name: /Evidence/ }).click();
  const drawer = page.getByRole("dialog", { name: "Source evidence" });
  await expect(drawer).toContainText("PDF page");
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();

  // plan changes are revalidated
  await first.getByRole("button", { name: "Add to plan" }).click();
  await page.getByRole("tab", { name: /Semester plan/ }).click();
  await expect(page.getByText("individual eligibility")).toBeVisible();
  await expect(page.getByText(/Total load \d+ units/)).toBeVisible();
  await page.getByRole("button", { name: "Save plan" }).click();
  await expect(page.getByRole("cell", { name: "Semester plan" })).toBeVisible();
});

test("honest empty result names the binding filters", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /entering year III \(sem 5\)/ }).click();
  await page.getByRole("tab", { name: "Recommendations" }).click();
  await page.getByLabel("Your request").fill("A HUEL about quantum physics with no midsem and no assignments");
  await page.getByRole("button", { name: "Recommend", exact: true }).click();
  await expect(page.getByText("Why nothing matched")).toBeVisible();
});

test("unsupported cohort is explained, not guessed", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /2026 admission/ }).click();
  await page.getByRole("tab", { name: "Progress" }).click();
  await expect(page.getByText("Outside supported scope.")).toBeVisible();
  await expect(page.getByText(/credit-hour framework/).first()).toBeVisible();
});
