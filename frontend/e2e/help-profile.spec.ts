import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { installMockApi, mockState } from "./support/taskMockApi";
import { installMockApi as installCalendarApi, mockState as calendarState } from "./support/calendarMockApi";
import type { ApiToken } from "../src/lib/types";

test.setTimeout(60000);
// Native paste probes must never record the user's clipboard in a trace.
// Playwright requires its worker-scoped trace option at file/config level.
test.use({ trace: "off" });
const scopeCatalog = [
    { code: "projects:read", description: "Читать проекты" },
    { code: "tasks:read", description: "Читать задачи" },
    { code: "tasks:write", description: "Изменять задачи" },
    { code: "agent:ask", description: "Обращаться к Арису" },
];
const browserErrors = new WeakMap<Page, string[]>();
const syntheticApiKey = "telos_key_de000003_00000000000000000000000000000000";
const syntheticAccessToken = "telos_token_synthetic_random_test_token_value_only";

test.afterEach(async ({ page }, info) => {
    const errors = browserErrors.get(page);
    if (!errors) return;
    await mkdir(info.outputDir, { recursive: true });
    const path = info.outputPath("browser-errors.json");
    await writeFile(path, JSON.stringify(errors, null, 2), "utf-8");
    await info.attach("browser-errors", { path, contentType: "application/json" });
    expect(errors, "Нет ошибок приложения в браузере").toEqual([]);
});

async function expectSelectAllState(checkbox: Locator, state: "none" | "mixed" | "all") {
    await expect(checkbox).toHaveJSProperty("checked", state === "all");
    await expect(checkbox).toHaveJSProperty("indeterminate", state === "mixed");
}

async function installAccountApi(page: Page) {
    const state = mockState();
    await installMockApi(page, state);
    const errors: string[] = [];
    browserErrors.set(page, errors);
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", entry => { if (entry.type() === "error") errors.push(entry.text()); });
    await page.route("https://fonts.googleapis.com/**", route => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
    await page.routeWebSocket("**/api/v1/users/me/task-attention/ws", socket => socket.send(JSON.stringify({ type: "task-attention", data: { new_tasks: state.attentionItems.length, overdue_tasks: 0, updated_tasks: 0, total: state.attentionItems.length, items: state.attentionItems } })));
    const user = { ...state.me, email: null, phone: null, telegram: null, created_at: "2026-11-04T06:00:00Z" };
    const key = { id: "k-1", label: "Клиент на ноутбуке", key_prefix: "de000001", owner_user_id: 1, owner_name: "Тестов Тест", scopes: ["projects:read", "tasks:read"], expires_at: null, is_active: true, revoked_at: null, revoked_by_name: null, created_by_name: "Тестов Тест", created_at: user.created_at, updated_at: user.created_at, last_used_at: null };
    const keys = [key, { ...key, id: "k-second", label: "Сверка на сервере", key_prefix: "de000002", scopes: ["projects:read"] }];
    const tokens: ApiToken[] = [{ id: 1, name: "Клиент токена", prefix: "token001", scope: "READ", created_at: user.created_at, expires_at: null, revoked_at: null, last_used_at: null }];
    const writes: { path: string; method: string; body: Record<string, unknown> }[] = [];
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const method = request.method();
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ json, status, headers });
        if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (path === "/api/v1/auth/me") return reply(user);
        if (path === "/api/v1/users/me/api-keys/scopes") return reply(scopeCatalog);
        if (path === "/api/v1/users/me/api-keys" && method === "GET") return reply(keys);
        if (path === "/api/v1/users/me/tokens" && method === "GET") return reply(tokens);
        if (method === "GET") return route.fallback();
        const body = request.postData() ? request.postDataJSON() : {};
        if (path === "/api/v1/users/me/api-keys" && method === "POST") {
            writes.push({ path, method, body });
            const created = { ...key, ...body, id: "k-2", key_prefix: "de000003" };
            keys.push(created);
            return reply({ ...created, raw_key: syntheticApiKey }, 201);
        }
        if (path.startsWith("/api/v1/users/me/api-keys/") && method === "PATCH") {
            writes.push({ path, method, body });
            const item = keys.find(item => path.endsWith(item.id))!;
            Object.assign(item, body, body.is_active === false ? { revoked_at: user.created_at } : {});
            return reply(item);
        }
        if (path === "/api/v1/users/me/tokens" && method === "POST") {
            writes.push({ path, method, body });
            const token = { ...tokens[0], ...body, id: 2, prefix: "token002" };
            tokens.push(token);
            return reply({ token, secret: syntheticAccessToken }, 201);
        }
        if (path.startsWith("/api/v1/users/me/tokens/") && method === "DELETE") {
            writes.push({ path, method, body });
            tokens.find(item => path.endsWith(`/${item.id}`))!.revoked_at = user.created_at;
            return route.fulfill({ status: 204, headers });
        }
        if (path === "/api/v1/users/me" && method === "PATCH") { writes.push({ path, method, body }); Object.assign(user, body); return reply(user); }
        return route.fallback();
    });
    return { state, writes, user, keys, tokens, scopeCatalog };
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

test("ключи выпускаются в профиле с выбранными правами и секретом на один показ", async ({ page }) => {
    const server = await installAccountApi(page);
    await page.goto("/profile?tab=keys");
    await expect(page).toHaveURL(/\/profile\?tab=keys$/);
    await page.getByRole("button", { name: "Выпустить ключ", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("Название", { exact: true }).fill("Новая интеграция");
    await expect(dialog.getByRole("checkbox", { name: /tasks:write/ })).not.toBeChecked();
    await dialog.getByRole("button", { name: "Выпустить", exact: true }).click();
    await expect(page.getByText("telos_key_de000003_00000000000000000000000000000000", { exact: true })).toBeVisible();
    expect(server.writes[0].body).toEqual({ label: "Новая интеграция", scopes: ["projects:read", "tasks:read"], expires_at: null });
    await page.getByRole("button", { name: "Я сохранил ключ", exact: true }).click();
    await expect(page.getByText("telos_key_de000003_00000000000000000000000000000000", { exact: true })).toHaveCount(0);
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

for (const width of [1440, 320]) {
    test(`единые API-ключи: три состояния выбора прав и редактирование ключа на ${width}px`, async ({ page }, info) => {
        const server = await installAccountApi(page);
        const expectedScopes = server.scopeCatalog.map(scope => scope.code);
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/profile?tab=keys");
        const table = page.getByRole("table", { name: "Мои API-ключи" });
        await expect(table.getByText("Сверка на сервере", { exact: true })).toBeVisible();
        await expect(table.getByText("Личный", { exact: true })).toHaveCount(0);
        await expect(table.getByText("Сервисный", { exact: true })).toHaveCount(0);
        await page.getByRole("button", { name: "Выпустить ключ", exact: true }).click();
        const createDialog = page.getByRole("dialog", { name: "Выпустить API-ключ", exact: true });
        await expect(createDialog.getByText("Один ключ для REST API и MCP. Работает от вашего имени в пределах выбранных прав.", { exact: true })).toBeVisible();
        await expect(createDialog.getByRole("combobox", { name: "Тип ключа", exact: true })).toHaveCount(0);
        await createDialog.getByLabel("Название", { exact: true }).fill("Клиент со всеми правами");
        const all = createDialog.getByRole("checkbox", { name: "Выбрать все", exact: true });
        await expectSelectAllState(all, "mixed");
        await expect(createDialog.getByRole("checkbox", { name: /^projects:read/ })).toBeChecked();
        await expect(createDialog.getByRole("checkbox", { name: /^tasks:write/ })).not.toBeChecked();
        await page.screenshot({ path: info.outputPath(`api-key-create-mixed-${width}.png`), animations: "disabled" });
        await all.focus();
        await all.press("Space");
        await expectSelectAllState(all, "all");
        for (const code of expectedScopes) await expect(createDialog.getByRole("checkbox", { name: new RegExp(`^${code}`) })).toBeChecked();
        await createDialog.getByRole("checkbox", { name: /^tasks:write/ }).uncheck();
        await expectSelectAllState(all, "mixed");
        await all.check();
        await expectSelectAllState(all, "all");
        await all.uncheck();
        await expectSelectAllState(all, "none");
        for (const code of expectedScopes) await expect(createDialog.getByRole("checkbox", { name: new RegExp(`^${code}`) })).not.toBeChecked();
        await createDialog.getByRole("button", { name: "Выпустить", exact: true }).click();
        await expect(createDialog.getByRole("alert")).toContainText("Выберите хотя бы одно право");
        expect(server.writes, "Пустые права отклоняются до POST").toEqual([]);
        await all.check();
        await expectSelectAllState(all, "all");
        await expect(createDialog.getByRole("alert"), "Повторный выбор прав убирает ошибку пустого набора").toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`api-key-create-all-${width}.png`), animations: "disabled" });

        let release!: () => void;
        const pending = new Promise<void>(resolve => { release = resolve; });
        await page.route("**/api/v1/users/me/api-keys", async route => {
            if (route.request().method() === "POST") await pending;
            await route.fallback();
        });
        try {
            await createDialog.getByRole("button", { name: "Выпустить", exact: true }).click();
            await expect(all).toBeDisabled();
            await expect(createDialog.getByRole("checkbox", { name: /^tasks:write/ })).toBeDisabled();
        } finally {
            release();
        }
        await expect(page.getByText("telos_key_de000003_00000000000000000000000000000000", { exact: true })).toBeVisible();
        expect(server.writes).toHaveLength(1);
        expect(server.writes[0]).toEqual({ path: "/api/v1/users/me/api-keys", method: "POST", body: { label: "Клиент со всеми правами", scopes: expectedScopes, expires_at: null } });
        await page.getByRole("button", { name: "Я сохранил ключ", exact: true }).click();
        await expect(page.getByText("telos_key_de000003_00000000000000000000000000000000", { exact: true })).toHaveCount(0);

        const secondRow = page.getByTestId("api-key-k-second");
        await secondRow.getByRole("button", { name: "Изменить", exact: true }).click();
        const editDialog = page.getByRole("dialog", { name: "Изменить API-ключ", exact: true });
        await expect(editDialog.getByRole("combobox", { name: "Тип ключа", exact: true })).toHaveCount(0);
        const editAll = editDialog.getByRole("checkbox", { name: "Выбрать все", exact: true });
        await expectSelectAllState(editAll, "mixed");
        await editAll.focus();
        await editAll.press("Space");
        await expectSelectAllState(editAll, "all");
        await editDialog.getByRole("checkbox", { name: /^agent:ask/ }).uncheck();
        await expectSelectAllState(editAll, "mixed");
        await editAll.check();
        await expectSelectAllState(editAll, "all");
        await editDialog.getByLabel("Название", { exact: true }).fill("Сверка обновлена");
        await page.screenshot({ path: info.outputPath(`api-key-edit-all-${width}.png`), animations: "disabled" });
        await editDialog.getByRole("button", { name: "Сохранить", exact: true }).click();
        await expect(editDialog).not.toBeVisible();
        expect(server.writes).toHaveLength(2);
        expect(server.writes[1]).toEqual({ path: "/api/v1/users/me/api-keys/k-second", method: "PATCH", body: { label: "Сверка обновлена", scopes: expectedScopes, expires_at: null } });
        await expect(secondRow.getByText("Сверка обновлена", { exact: true })).toBeVisible();
        await expect(table.getByText("Личный", { exact: true })).toHaveCount(0);
        await expect(table.getByText("Сервисный", { exact: true })).toHaveCount(0);
        await page.goto("/help/api#api-auth");
        await expect(page.getByRole("heading", { name: "Авторизация и ключи доступа", exact: true })).toBeVisible();
        await expect(page.locator('section[aria-labelledby="api-auth"]').locator("p").filter({ hasText: "Один и тот же API-ключ" })).toContainText("для REST API (/api/v1) и MCP (/mcp)");
        await page.goto("/help/mcp#mcp-connect");
        await expect(page.getByRole("heading", { name: "Подключение клиента", exact: true })).toBeVisible();
        await expect(page.locator('section[aria-labelledby="mcp-connect"]').locator("p").filter({ hasText: "тот же API-ключ" })).toContainText("что и для REST API, с теми же выбранными правами");
        expect(server.writes).toHaveLength(2);
        expect(server.state.errors).toEqual([]);
    });
}

test("READ/WRITE-токены доступны в профиле и отзываются с подтверждением", async ({ page }) => {
    const server = await installAccountApi(page);
    await page.goto("/profile?tab=keys");
    await page.getByText("READ/WRITE-токены", { exact: true }).click();
    await page.getByLabel("Название токена", { exact: true }).fill("Интеграция с токеном");
    await page.getByRole("button", { name: "Выпустить токен", exact: true }).click();
    await expect(page.getByText("telos_token_synthetic_random_test_token_value_only", { exact: true })).toBeVisible();
    expect(server.writes[0].body).toEqual({ name: "Интеграция с токеном", scope: "READ", ttl_days: 90 });
    await page.getByRole("button", { name: "Я сохранил токен", exact: true }).click();
    await expect(page.getByText("telos_token_synthetic_random_test_token_value_only", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Отозвать токен Клиент токена", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Отмена", exact: true }).click();
    expect(server.writes).toHaveLength(1);
    await page.getByRole("button", { name: "Отозвать токен Клиент токена", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Отозвать", exact: true }).click();
    await expect(page.getByRole("button", { name: "Отозвать токен Клиент токена", exact: true })).toHaveCount(0);
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

async function installHttpClipboardApi(page: Page, baseURL: string | undefined) {
    const staticOrigin = new URL(baseURL ?? "http://127.0.0.1:4179");
    const local = ["127.0.0.1", "localhost"].includes(staticOrigin.hostname);
    const origin = local ? "http://clipboard.telos.invalid" : staticOrigin.origin;
    if (local) {
        await page.route(`${origin}/**`, async route => {
            const url = new URL(route.request().url());
            if (url.pathname.startsWith("/api/") || route.request().method() !== "GET") {
                return route.fulfill({ status: 503, json: { detail: "HTTP clipboard smoke requires a managed API fixture" } });
            }
            const response = await route.fetch({ url: new URL(`${url.pathname}${url.search}`, staticOrigin).href });
            return route.fulfill({ response });
        });
    }
    const server = await installAccountApi(page);
    return { ...server, origin };
}

async function expectNativeHttpClipboard(page: Page) {
    expect(await page.evaluate(() => ({ secure: window.isSecureContext, writeTextAvailable: typeof navigator.clipboard?.writeText === "function" }))).toEqual({ secure: false, writeTextAvailable: false });
}

async function issueSyntheticKey(page: Page, origin: string) {
    await page.goto(new URL("/profile?tab=keys", origin).href);
    await page.getByRole("button", { name: "Выпустить ключ", exact: true }).click();
    const form = page.getByRole("dialog", { name: "Выпустить API-ключ", exact: true });
    await form.getByLabel("Название", { exact: true }).fill("HTTP clipboard fixture");
    await form.getByRole("button", { name: "Выпустить", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Ключ выпущен", exact: true });
    await expect(dialog.getByTestId("api-key-secret")).toBeVisible();
    return { dialog, copy: dialog.getByRole("button", { name: /^(Копировать|Скопировано)$/ }) };
}

async function expectNativePaste(page: Page, container: Locator, restoreFocus: Locator, expected: string) {
    // The probe never leaves the browser as text: failed assertions contain only
    // booleans, and cleanup runs before Playwright can take a failure screenshot.
    await container.evaluate(element => {
        const probe = document.createElement("textarea");
        probe.dataset.clipboardProbe = "true";
        probe.setAttribute("aria-label", "Проверка вставки синтетического значения");
        probe.style.cssText = "position:fixed;left:20px;bottom:20px;width:180px;height:32px;z-index:100";
        probe.addEventListener("paste", () => { probe.dataset.pasteObserved = "true"; });
        element.append(probe);
        probe.focus();
    });
    try {
        await page.keyboard.press("Control+V");
        await expect.poll(() => page.evaluate(() => document.querySelector<HTMLTextAreaElement>("[data-clipboard-probe]")?.dataset.pasteObserved === "true"), { timeout: 3000, message: "Браузер обработал нативный Control+V" }).toBe(true);
        const matches = await page.evaluate(expectedValue => {
            const probe = document.querySelector<HTMLTextAreaElement>("[data-clipboard-probe]");
            const matches = probe?.value === expectedValue;
            if (probe) { probe.value = ""; probe.remove(); }
            return matches;
        }, expected);
        expect(matches, "Нативная вставка содержит точное синтетическое значение").toBe(true);
    } finally {
        await page.evaluate(() => {
            const probe = document.querySelector<HTMLTextAreaElement>("[data-clipboard-probe]");
            if (probe) { probe.value = ""; probe.remove(); }
        });
        await restoreFocus.focus();
    }
}

test.describe("буфер HTTP", () => {
    test.describe.configure({ mode: "serial" });

    for (const width of [1440, 320]) {
        test(`нативный copy ключа внутри RAC Modal и точная вставка на ${width}px`, async ({ page, baseURL }, info) => {
            const server = await installHttpClipboardApi(page, baseURL);
            await page.setViewportSize({ width, height: 900 });
            const { dialog, copy } = await issueSyntheticKey(page, server.origin);
            await expectNativeHttpClipboard(page);
            const textareasBefore = await page.locator("textarea").count();
            await page.bringToFront();
            await copy.click();
            await expect(copy).toHaveText("Скопировано");
            await expect(dialog.getByText(/Буфер обмена недоступен/)).toHaveCount(0);
            await expect(copy).toBeFocused();
            await expect(page.locator("textarea")).toHaveCount(textareasBefore);
            await expectNativePaste(page, dialog, copy, syntheticApiKey);
            await expect(copy).toBeFocused();
            await expectNativeHttpClipboard(page);
            await page.screenshot({ path: info.outputPath(`clipboard-key-copied-${width}.png`), animations: "disabled" });
            expect(server.writes).toHaveLength(1);
            expect(server.state.errors).toEqual([]);
        });
    }

    test("нативный copy READ/WRITE-токена, примеров MCP/API и ICS", async ({ page, baseURL }) => {
        const server = await installHttpClipboardApi(page, baseURL);
        await page.goto(new URL("/profile?tab=keys", server.origin).href);
        await expectNativeHttpClipboard(page);
        await page.getByText("READ/WRITE-токены", { exact: true }).click();
        await page.getByLabel("Название токена", { exact: true }).fill("HTTP token fixture");
        await page.getByRole("button", { name: "Выпустить токен", exact: true }).click();
        const tokenDialog = page.getByRole("dialog", { name: "Токен выпущен", exact: true });
        const tokenCopy = tokenDialog.getByRole("button", { name: "Копировать токен", exact: true });
        await tokenCopy.click();
        await expect(page.getByText("Токен скопирован", { exact: true })).toBeVisible();
        await expect(tokenCopy).toBeFocused();
        await expect(page.locator("textarea")).toHaveCount(0);
        await expectNativePaste(page, tokenDialog, tokenCopy, syntheticAccessToken);
        await tokenDialog.getByRole("button", { name: "Я сохранил токен", exact: true }).click();

        for (const example of [
            { path: "/help/mcp#mcp-connect", section: "mcp-connect", label: "Копировать конфигурацию MCP", kind: "mcp" },
            { path: "/help/api#api-example", section: "api-example", label: "Копировать пример API", kind: "api" },
        ]) {
            await page.goto(new URL(example.path, server.origin).href);
            const article = page.locator(`section[aria-labelledby="${example.section}"]`);
            const copy = article.getByRole("button", { name: example.label, exact: true });
            const code = await article.locator("pre code").textContent();
            expect(code === null, "У примера есть синтетический текст").toBe(false);
            const usesCurrentOrigin = example.kind === "mcp"
                ? JSON.parse(code!).mcpServers["telos-space"].url === new URL("/mcp", server.origin).href
                : code!.includes(new URL("/api/v1/projects", server.origin).href);
            expect(usesCurrentOrigin).toBe(true);
            await copy.click();
            await expect(copy).toHaveText("Скопировано");
            await expect(article.getByRole("alert")).toHaveCount(0);
            await expect(copy).toBeFocused();
            await expect(page.locator("textarea")).toHaveCount(0);
            await expectNativePaste(page, article, copy, code!);
            await expectNativeHttpClipboard(page);
        }
        expect(server.writes).toHaveLength(1);
        expect(server.state.errors).toEqual([]);

        const calendar = calendarState();
        await installCalendarApi(page, calendar);
        await page.route("https://fonts.googleapis.com/**", route => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
        await page.goto(new URL("/calendar?view=grid&scale=month", server.origin).href);
        await page.getByRole("button", { name: "Подписка на календарь в Outlook", exact: true }).click();
        const icsDialog = page.getByRole("dialog", { name: "Подписка на календарь в Outlook", exact: true });
        await icsDialog.getByRole("button", { name: "Создать ссылку", exact: true }).click();
        const icsCopy = icsDialog.getByRole("button", { name: "Скопировать", exact: true });
        await icsCopy.click();
        await expect(page.getByText("Ссылка скопирована.", { exact: true })).toBeVisible();
        await expect(icsCopy).toBeFocused();
        await expect(page.locator("textarea")).toHaveCount(0);
        await expectNativePaste(page, icsDialog, icsCopy, "http://tracker.test/api/v1/ics/cal_demo.ics");
        await expectNativeHttpClipboard(page);
        expect(calendar.errors).toEqual([]);
        expect(calendar.writes.map(write => ({ method: write.method, path: write.path }))).toEqual([{ method: "POST", path: "/users/me/calendar/ics-token" }]);
    });

    test("отказ modern writeText использует нативный fallback внутри модального окна", async ({ page, baseURL }) => {
        const server = await installHttpClipboardApi(page, baseURL);
        const { dialog, copy } = await issueSyntheticKey(page, server.origin);
        await expectNativeHttpClipboard(page);
        await page.evaluate(() => {
            Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => {
                document.documentElement.dataset.modernCopyAttempts = String(Number(document.documentElement.dataset.modernCopyAttempts ?? 0) + 1);
                throw new DOMException("Clipboard fixture denial", "NotAllowedError");
            } } });
        });
        await copy.click();
        await expect(copy).toHaveText("Скопировано");
        await expect(page.locator("html")).toHaveAttribute("data-modern-copy-attempts", "1");
        await expect(copy).toBeFocused();
        await expect(page.locator("textarea")).toHaveCount(0);
        await expectNativePaste(page, dialog, copy, syntheticApiKey);
        expect(server.writes).toHaveLength(1);
        expect(server.state.errors).toEqual([]);
    });

    test("блокировка обоих способов показывает ошибку и разрешает повторное копирование", async ({ page, baseURL }) => {
        const server = await installHttpClipboardApi(page, baseURL);
        const { dialog, copy } = await issueSyntheticKey(page, server.origin);
        await expectNativeHttpClipboard(page);
        await page.evaluate(() => {
            const execCommand = document.execCommand;
            Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => { throw new DOMException("Clipboard fixture denial", "NotAllowedError"); } } });
            document.execCommand = () => false;
            window.addEventListener("restore-native-copy", () => {
                document.execCommand = execCommand;
                Reflect.deleteProperty(navigator, "clipboard");
            }, { once: true });
        });
        await copy.click();
        const error = dialog.getByText("Буфер обмена недоступен — выделите ключ и скопируйте вручную", { exact: true });
        await expect(error).toBeVisible();
        await expect(copy).toHaveText("Копировать");
        await expect(copy).toBeFocused();
        await expect(page.locator("textarea")).toHaveCount(0);
        await page.evaluate(() => { window.dispatchEvent(new Event("restore-native-copy")); });
        await expectNativeHttpClipboard(page);
        await copy.click();
        await expect(copy).toHaveText("Скопировано");
        await expect(error).toHaveCount(0);
        await expect(copy).toBeFocused();
        await expect(page.locator("textarea")).toHaveCount(0);
        await expectNativePaste(page, dialog, copy, syntheticApiKey);
        expect(server.writes).toHaveLength(1);
        expect(server.state.errors).toEqual([]);
    });
});
