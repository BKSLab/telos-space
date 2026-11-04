import { expect, test, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";
import type { ApiToken } from "../src/lib/types";

test.setTimeout(60000);
async function installAccountApi(page: Page) {
    const state = mockState();
    await installMockApi(page, state);
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    const user = { ...state.me, email: null, phone: null, telegram: null, created_at: "2026-11-04T06:00:00Z" };
    const key = { id: "k-1", label: "Клиент на ноутбуке", key_prefix: "ttk_demo", owner_user_id: 1, owner_name: "Тестов Тест", kind: "personal", scopes: ["projects:read", "tasks:read"], expires_at: null, is_active: true, revoked_at: null, revoked_by_name: null, created_by_name: "Тестов Тест", created_at: user.created_at, updated_at: user.created_at, last_used_at: null };
    const keys = [key];
    const tokens: ApiToken[] = [{ id: 1, name: "Прежний клиент", prefix: "tt_demo", scope: "READ", created_at: user.created_at, expires_at: null, revoked_at: null, last_used_at: null }];
    const writes: { path: string; method: string; body: Record<string, unknown> }[] = [];
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const method = request.method();
        const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
        if (path === "/api/v1/auth/me") return reply(user);
        if (path === "/api/v1/users/me/api-keys/scopes") return reply([
            { code: "projects:read", description: "Читать проекты" }, { code: "tasks:read", description: "Читать задачи" },
            { code: "tasks:write", description: "Изменять задачи" }, { code: "agent:ask", description: "Обращаться к Арису" },
        ]);
        if (path === "/api/v1/users/me/api-keys" && method === "GET") return reply(keys);
        if (path === "/api/v1/users/me/tokens" && method === "GET") return reply(tokens);
        if (method === "GET" || method === "OPTIONS") return route.fallback();
        const body = request.postData() ? request.postDataJSON() : {};
        if (path === "/api/v1/users/me/api-keys" && method === "POST") {
            writes.push({ path, method, body });
            const created = { ...key, ...body, id: "k-2", key_prefix: "ttk_new" };
            keys.push(created);
            return reply({ ...created, raw_key: "SYNTHETIC_API_KEY_ONCE" }, 201);
        }
        if (path.startsWith("/api/v1/users/me/api-keys/") && method === "PATCH") {
            writes.push({ path, method, body });
            const item = keys.find(item => path.endsWith(item.id))!;
            Object.assign(item, body, body.is_active === false ? { revoked_at: user.created_at } : {});
            return reply(item);
        }
        if (path === "/api/v1/users/me/tokens" && method === "POST") {
            writes.push({ path, method, body });
            const token = { ...tokens[0], ...body, id: 2, prefix: "tt_new" };
            tokens.push(token);
            return reply({ token, secret: "SYNTHETIC_LEGACY_TOKEN_ONCE" }, 201);
        }
        if (path.startsWith("/api/v1/users/me/tokens/") && method === "DELETE") {
            writes.push({ path, method, body });
            tokens.find(item => path.endsWith(`/${item.id}`))!.revoked_at = user.created_at;
            return route.fulfill({ status: 204 });
        }
        if (path === "/api/v1/users/me" && method === "PATCH") { writes.push({ path, method, body }); Object.assign(user, body); return reply(user); }
        return route.fallback();
    });
    return { state, writes, user, keys, tokens };
}

for (const width of [1440, 1024, 768, 390, 320]) {
    test(`справка, глобальный поиск и профиль доступны на ${width}px`, async ({ page }, info) => {
        const server = await installAccountApi(page);
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/help");
        await expect(page.getByRole("heading", { name: "Общие сведения", exact: true })).toBeVisible();
        if (width >= 1024) {
            const nav = page.locator("aside").getByRole("navigation", { name: "Основная навигация" });
            await expect(nav.getByRole("link")).toHaveCount(4);
            await expect(page.locator("aside").getByRole("link", { name: "Справка", exact: true })).toBeVisible();
            await page.getByRole("navigation", { name: "Разделы справки" }).getByRole("link", { name: "Проекты", exact: true }).click();
        } else await page.getByRole("combobox", { name: "Раздел справки" }).selectOption("projects");
        await expect(page.getByRole("heading", { name: "Команда и доступ", exact: true })).toBeVisible();
        await page.screenshot({ path: info.outputPath(`help-projects-${width}.png`) });
        await page.getByRole("searchbox", { name: "Поиск по справке" }).fill("итоговый отчет");
        const result = page.getByRole("link", { name: "Задачи · Завершение и итоговый отчёт", exact: true });
        await expect(result).toBeVisible();
        await expect(result.locator("mark")).not.toHaveCount(0);
        await result.click();
        await expect(page).toHaveURL(/\/help\/tasks#task-completion$/);
        await expect(page.getByRole("heading", { name: "Завершение и итоговый отчёт", exact: true })).toBeFocused();
        await page.getByRole("searchbox").fill("несуществующийтермин");
        await expect(page.getByText("Ничего не найдено", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Сбросить поиск" }).click();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

        await page.goto("/profile");
        await expect(page.getByRole("heading", { name: "Профиль", exact: true })).toBeVisible();
        await page.screenshot({ path: info.outputPath(`profile-${width}.png`) });
        const tabs = page.getByRole("navigation", { name: "Разделы профиля" });
        await tabs.getByRole("link", { name: "Безопасность", exact: true }).click();
        await expect(page.getByLabel("Текущий пароль", { exact: true })).toBeVisible();
        await tabs.getByRole("link", { name: "Ключи доступа", exact: true }).click();
        await expect(page.getByText("Клиент на ноутбуке", { exact: true })).toBeVisible();
        const table = page.getByRole("table", { name: "Мои API-ключи" });
        expect(await table.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
        expect(await table.evaluate(element => element.parentElement!.scrollWidth <= element.parentElement!.clientWidth + 1)).toBe(true);
        await page.screenshot({ path: info.outputPath(`profile-keys-${width}.png`) });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(server.state.errors).toEqual([]);
        expect(server.writes).toEqual([]);
    });
}

test("ключи выпускаются в профиле с прежними правами и секретом на один показ", async ({ page }) => {
    const server = await installAccountApi(page);
    await page.goto("/my-api-keys");
    await expect(page).toHaveURL(/\/profile\?tab=keys$/);
    await page.getByRole("button", { name: "Выпустить ключ", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("Название", { exact: true }).fill("Новая интеграция");
    await expect(dialog.getByRole("checkbox", { name: /tasks:write/ })).not.toBeChecked();
    await dialog.getByRole("button", { name: "Выпустить", exact: true }).click();
    await expect(page.getByText("SYNTHETIC_API_KEY_ONCE", { exact: true })).toBeVisible();
    expect(server.writes[0].body).toEqual({ kind: "personal", label: "Новая интеграция", scopes: ["projects:read", "tasks:read"], expires_at: null });
    await page.getByRole("button", { name: "Я сохранил ключ", exact: true }).click();
    await expect(page.getByText("SYNTHETIC_API_KEY_ONCE", { exact: true })).toHaveCount(0);
    const row = page.getByTestId("api-key-k-2");
    await row.getByRole("button", { name: "Отозвать", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
    expect(server.writes).toHaveLength(1);
    await row.getByRole("button", { name: "Отозвать", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Отозвать", exact: true }).click();
    await expect(row.getByText("Отозван", { exact: true })).toBeVisible();
    expect(server.writes[1].body).toEqual({ is_active: false });
    expect(server.state.errors).toEqual([]);
});

test("прежние READ/WRITE-токены доступны в профиле и отзываются с подтверждением", async ({ page }) => {
    const server = await installAccountApi(page);
    await page.goto("/profile?tab=keys");
    await page.getByText("Прежние токены", { exact: true }).click();
    await page.getByLabel("Название токена", { exact: true }).fill("Прежняя интеграция");
    await page.getByRole("button", { name: "Выпустить токен", exact: true }).click();
    await expect(page.getByText("SYNTHETIC_LEGACY_TOKEN_ONCE", { exact: true })).toBeVisible();
    expect(server.writes[0].body).toEqual({ name: "Прежняя интеграция", scope: "READ", ttl_days: 90 });
    await page.getByRole("button", { name: "Я сохранил токен", exact: true }).click();
    await expect(page.getByText("SYNTHETIC_LEGACY_TOKEN_ONCE", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Отозвать токен Прежний клиент", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Отмена", exact: true }).click();
    expect(server.writes).toHaveLength(1);
    await page.getByRole("button", { name: "Отозвать токен Прежний клиент", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Отозвать", exact: true }).click();
    await expect(page.getByRole("button", { name: "Отозвать токен Прежний клиент", exact: true })).toHaveCount(0);
    expect(server.writes[1].method).toBe("DELETE");
    expect(server.state.errors).toEqual([]);
});

test("справка по API и MCP копирует примеры с текущим адресом, не выпуская ключ", async ({ page }) => {
    const server = await installAccountApi(page);
    await page.goto("/help/mcp#mcp-connect");
    await page.evaluate(() => { Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (text: string) => { document.documentElement.dataset.copiedExample = text; } } }); });
    await page.getByRole("button", { name: "Копировать конфигурацию MCP", exact: true }).click();
    const config = JSON.parse((await page.locator("html").getAttribute("data-copied-example"))!);
    expect(config.mcpServers["telos-space"].url).toBe(new URL("/mcp", page.url()).href);
    expect(config.mcpServers["telos-space"].headers.Authorization).toBe("Bearer YOUR_API_KEY");
    await page.getByRole("navigation", { name: "Разделы справки" }).getByRole("link", { name: "API", exact: true }).click();
    await page.getByRole("button", { name: "Копировать пример API", exact: true }).click();
    expect(await page.locator("html").getAttribute("data-copied-example")).toContain("/api/v1/projects");
    expect(server.writes).toEqual([]);
});
