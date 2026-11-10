import { expect, test } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";
import catalog from "../src/content/integrations.generated.json" with { type: "json" };

for (const width of [1440, 390, 320]) {
    test(`каталог API/MCP: поиск, схема и прямая ссылка при ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 950 });
        const state = mockState();
        await installMockApi(page, state);
        await page.route("https://fonts.gstatic.com/**", route => route.abort());
        const errors: string[] = [];
        page.on("pageerror", error => errors.push(error.message));

        await page.goto("/help/about");
        await page.getByRole("searchbox", { name: "Поиск по справке" }).fill("telos_create_task");
        const tool = catalog.mcp.find(entry => entry.name === "telos_create_task")!;
        await page.getByRole("link", { name: `MCP · ${tool.name} · ${tool.summary}`, exact: true }).click();
        await expect(page.locator(`[id="${tool.id}"]`)).toBeFocused();
        const access = page.getByRole("region", { name: `Доступ ${tool.name}`, exact: true });
        await expect(access).toContainText("tasks:write");
        const operation = access.locator("..");
        await expect(operation.getByRole("heading", { name: "Аргументы", exact: true })).toBeVisible();
        await expect(operation.getByText("project_key", { exact: true })).toBeVisible();
        await page.screenshot({ path: info.outputPath(`mcp-tool-${width}.png`) });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.reload();
        await expect(page.locator(`[id="${tool.id}"]`)).toBeFocused();
        await expect(page.getByRole("region", { name: `Доступ ${tool.name}`, exact: true })).toBeVisible();

        await page.goto("/help/api#api-catalog");
        await page.getByRole("combobox", { name: "Фильтр каталога", exact: true }).selectOption("keys");
        await page.getByRole("searchbox", { name: "Поиск методов API", exact: true }).fill("wbs suggestion");
        const method = catalog.api.find(entry => entry.method === "POST" && entry.path?.endsWith("/wbs/suggestion"))!;
        await page.locator(`[id="${method.id}"]`).click();
        await expect(page.getByRole("region", { name: `Доступ ${method.name}`, exact: true })).toContainText("structure:read");
        await page.screenshot({ path: info.outputPath(`api-method-${width}.png`) });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

        await page.getByRole("combobox", { name: "Фильтр каталога", exact: true }).selectOption("session");
        await page.getByRole("searchbox", { name: "Поиск методов API", exact: true }).fill("api-keys");
        const session = catalog.api.find(entry => entry.method === "GET" && entry.path === "/api/v1/users/me/api-keys")!;
        await page.locator(`[id="${session.id}"]`).click();
        await expect(page.getByRole("region", { name: `Доступ ${session.name}`, exact: true })).toContainText("Только сессия интерфейса");
        expect(errors).toEqual([]);
        expect(state.errors).toEqual([]);
    });
}

test("MCP публикует единые имена и действия общего диспетчера", async ({ page }) => {
    await installMockApi(page, mockState());
    await page.goto("/help/mcp#mcp-catalog");
    const filter = page.getByRole("combobox", { name: "Фильтр каталога", exact: true });
    await expect(filter.locator('option[value="aliases"]')).toHaveCount(0);
    await filter.selectOption("primary");
    await page.getByRole("searchbox", { name: "Поиск инструментов MCP", exact: true }).fill("telos_add_comment");
    const tool = catalog.mcp.find(entry => entry.name === "telos_add_comment")!;
    await page.locator(`[id="${tool.id}"]`).click();
    await expect(page.getByRole("region", { name: `Доступ ${tool.name}`, exact: true })).toContainText("comments:write");
    await page.getByRole("combobox", { name: "Фильтр каталога", exact: true }).selectOption("actions");
    await page.getByRole("searchbox", { name: "Поиск инструментов MCP", exact: true }).fill("telos_delete_task");
    const action = catalog.actions.find(entry => entry.name === "telos_delete_task")!;
    await page.locator(`[id="${action.id}"]`).click();
    await expect(page.getByRole("region", { name: `Доступ ${action.name}`, exact: true })).toContainText("подтверждение пользователя");
    await expect(page.getByText("Через telos_execute_project_tool", { exact: true })).toBeVisible();
});
