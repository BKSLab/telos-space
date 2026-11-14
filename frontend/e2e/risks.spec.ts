import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import type { Task } from "../src/lib/types";

const timestamp = "2026-09-06T10:00:00Z";
const user = { id: 1, username: "vera", first_name: "Вера", last_name: "Иванова", middle_name: null, is_active: true, has_avatar: false };
const project = { id: 1, key: "DEMO", name: "Интеграция CRM", description_md: "Запуск CRM", status: "ACTIVE", color: "#7299cc", icon: null, start_date: null, due_date: null, order_index: 0, created_at: timestamp, updated_at: timestamp };
const task: Task = { revision: 1, id: 7, project_id: 1, stage_id: 1, key: "DEMO-142", number: 142, title: "Согласовать контракт API", description_md: "Ждём поставщика", priority: "HIGH", role: null, assignee: null, position: 1000, start_date: null, due_date: null, baseline_start_date: null, baseline_due_date: null, completed_at: null, participants: [], wbs_node_id: null, created_at: timestamp, updated_at: timestamp, comments_count: 0, last_comment: null, search_match_source: null, search_title: null, search_excerpt: null };
const baseRisk = { id: 12, key: "RISK-12", project_id: 1, title: "Задержка интеграции CRM", description: "Поставщик может задержать документацию.", probability: "HIGH", impact: "HIGH", risk_level: "HIGH", status: "OPEN", response_strategy: "MITIGATE", mitigation_plan: "Согласовать API заранее", response_plan: "Использовать резервный адаптер", owner_user_id: 1 as number | null, task_id: 7 as number | null, review_date: "2026-09-06" as string | null, source: "MANUAL", created_at: timestamp, updated_at: timestamp };
type Risk = typeof baseRisk;
const browserErrors = new WeakMap<Page, { kind: string; text: string; expected: boolean }[]>();
const longDescription = [
    "# Контекст интеграции",
    ...Array.from({ length: 35 }, (_, index) => `Абзац ${index + 1}. Проверяем сроки поставщика, совместимость API и готовность резервного адаптера. ${"Подробное описание риска. ".repeat(8)}`),
    ["| Система | Контракт | Проверка | Ответственный | Зависимость | Решение |", "| --- | --- | --- | --- | --- | --- |", `| CRM | ${"СверхдлинныйКонтракт".repeat(30)} | Тестирование | Команда | Поставщик | Адаптер |`].join("\n"),
    "```json",
    JSON.stringify({ contract: "long-contract-value-".repeat(90) }),
    "```",
    "Последняя строка длинного описания.",
].join("\n\n");

test.afterEach(async ({ page }, info) => {
    const errors = browserErrors.get(page);
    if (!errors) return;
    await mkdir(info.outputDir, { recursive: true });
    const path = info.outputPath("browser-errors.json");
    await writeFile(path, JSON.stringify(errors, null, 2), "utf-8");
    await info.attach("browser-errors", { path, contentType: "application/json" });
    expect(errors.filter(error => !error.expected), "Нет ошибок приложения в браузере").toEqual([]);
});

async function expectInternalScroll(dialog: Locator) {
    const panel = dialog.getByRole("tabpanel", { name: "Основное", exact: true });
    await expect(panel.locator("table")).toBeAttached();
    await expect(panel.locator("pre")).toBeAttached();
    const bounds = await dialog.evaluate(element => ({ header: element.querySelector(":scope > header")!.getBoundingClientRect().y, footer: element.querySelector(":scope > footer")!.getBoundingClientRect().y, pageScroll: document.scrollingElement!.scrollTop }));
    const scroll = await panel.evaluate(element => {
        element.scrollTop = element.scrollHeight;
        return { top: element.scrollTop, width: element.clientWidth, contentWidth: element.scrollWidth };
    });
    expect(scroll.top, "Длинное описание прокручивается внутри окна").toBeGreaterThan(0);
    expect(scroll.contentWidth, "Markdown не расширяет панель по горизонтали").toBeLessThanOrEqual(scroll.width + 1);
    const tableScroll = await panel.locator("table").evaluate(element => {
        element.scrollLeft = 50;
        const result = { width: element.clientWidth, contentWidth: element.scrollWidth, left: element.scrollLeft };
        element.scrollLeft = 0;
        return result;
    });
    expect(tableScroll.contentWidth, "Длинная ячейка сохраняет ширину и прокручивается внутри таблицы").toBeGreaterThan(tableScroll.width);
    expect(tableScroll.left, "Таблицу можно сдвинуть по горизонтали").toBe(50);
    await expect.poll(() => panel.locator("table").evaluate(element => element.scrollLeft)).toBe(0);
    await expect(dialog.locator(":scope > header")).toBeInViewport();
    await expect(dialog.locator(":scope > footer")).toBeInViewport();
    const after = await dialog.evaluate(element => ({ header: element.querySelector(":scope > header")!.getBoundingClientRect().y, footer: element.querySelector(":scope > footer")!.getBoundingClientRect().y, pageScroll: document.scrollingElement!.scrollTop }));
    expect(after).toEqual(bounds);
    expect(await dialog.page().evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

async function mockProject(page: Page, risks: Risk[] = [{ ...baseRisk }]) {
    const state = { risks, fail: false, failedResponses: 0, attentionGets: 0, requests: [] as URL[], writes: [] as { method: string; body: Record<string, unknown> | null }[], errors: [] as string[] };
    const messages: { kind: string; text: string; expected: boolean }[] = [];
    browserErrors.set(page, messages);
    page.on("pageerror", (error) => { state.errors.push(error.message); messages.push({ kind: "pageerror", text: error.message, expected: false }); });
    page.on("console", entry => {
        if (entry.type() === "error") messages.push({ kind: "console", text: entry.text(), expected: state.failedResponses > 0 && /Failed to load resource: the server responded with a status of 500/.test(entry.text()) });
    });
    await page.route("https://fonts.googleapis.com/**", route => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
    await page.route("**/api/v1/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname;
        const method = request.method();
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, json, headers });
        if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (path.endsWith("/auth/me")) return reply(user);
        if (path === "/api/v1/documents") return reply({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
        if (path === "/api/v1/projects") return reply([project]);
        if (path === "/api/v1/projects/1") return reply(project);
        if (path === "/api/v1/users/me/task-attention") return reply({ new_tasks: 0, overdue_tasks: 0, updated_tasks: 0, total: 0, items: [] });
        if (path === "/api/v1/projects/1/chat") return reply({ id: 1, project_id: 1, event_cursor: 0, last_read_seq: 0, unread_count: 0, members: [] });
        if (path === "/api/v1/projects/1/chat/events") return reply({ events: [], cursor: 0, has_more: false });
        if (path.endsWith("/stats")) return reply({ project_id: 1, total_tasks: 1, done_tasks: 0, in_progress_tasks: 1, overdue_tasks: 0, due_soon_tasks: 0, unassigned_tasks: 0, completion_rate: 0, next_due_date: null, stage_breakdown: [] });
        if (path.endsWith("/members")) return reply([{ id: 1, project_id: 1, role: "OWNER", user, created_at: timestamp }]);
        if (path.endsWith("/tasks")) return reply([task]);
        if (path === "/api/v1/tasks/7") return reply(task);
        if (path === "/api/v1/tasks/7/attention") { state.attentionGets += 1; return reply({ revision: 1, deadline_revision: 1, overdue: false }); }
        if (path === "/api/v1/projects/1/stages") return reply([{ id: 1, project_id: 1, name: "В работе", color: "#7299cc", order_index: 0, is_done_stage: false }]);
        if (path.endsWith("/risks/suggestions")) return reply({ suggestions: [{ title: "Зависимость от поставщика", description: "Не согласован контракт API", probability: "HIGH", impact: "MEDIUM", response_strategy: "MITIGATE", mitigation_plan: "Проверить контракт", response_plan: "Резервный адаптер", task_id: 7, evidence: ["DEMO-142: Ждём поставщика"] }] });
        if (path.includes("/risks")) {
            state.requests.push(url);
            const match = path.match(/\/risks\/(\d+)$/);
            if (method !== "GET") {
                const body = method === "DELETE" ? null : request.postDataJSON();
                state.writes.push({ method, body });
                if (method === "DELETE") {
                    state.risks = state.risks.filter((risk) => risk.id !== Number(match?.[1]));
                    return route.fulfill({ status: 204, headers });
                }
                const previous = method === "PATCH" ? state.risks.find((risk) => risk.id === Number(match?.[1]))! : { ...baseRisk, id: 100, key: "RISK-100" };
                const risk = { ...previous, ...body };
                risk.risk_level = risk.probability === "HIGH" && risk.impact !== "LOW" ? "HIGH" : "MEDIUM";
                state.risks = [...state.risks.filter((item) => item.id !== risk.id), risk];
                return reply(risk, method === "POST" ? 201 : 200);
            }
            if (match) {
                const risk = state.risks.find((item) => item.id === Number(match[1]));
                return risk ? reply(risk) : reply({ detail: "Риск не найден" }, 404);
            }
            const filtered = state.risks.filter((risk) => {
                if (url.searchParams.get("active_only") === "true" && risk.status === "CLOSED") return false;
                for (const key of ["probability", "impact", "status", "risk_level", "owner_user_id", "task_id"] as const) {
                    if (url.searchParams.has(key) && String(risk[key]) !== url.searchParams.get(key)) return false;
                }
                return `${risk.key} ${risk.title} ${risk.description}`.toLowerCase().includes((url.searchParams.get("search") ?? "").toLowerCase());
            });
            if (path.endsWith("/summary")) return reply({ total_risks: filtered.length, active_risks: filtered.filter((risk) => risk.status !== "CLOSED").length, high_risks: filtered.filter((risk) => risk.risk_level === "HIGH").length, occurred_risks: 0, risks_due_for_review: 0, signals: [], latest_update: timestamp, matrix: ["LOW", "MEDIUM", "HIGH"].flatMap((probability) => ["LOW", "MEDIUM", "HIGH"].map((impact) => ({ probability, impact, count: filtered.filter((risk) => risk.probability === probability && risk.impact === impact).length }))) });
            if (state.fail) { state.failedResponses += 1; return reply({ detail: "Ошибка чтения реестра" }, 500); }
            const number = Number(url.searchParams.get("page") ?? 1);
            const pageSize = Number(url.searchParams.get("page_size") ?? 25);
            return reply({ total: filtered.length, page: number, page_size: pageSize, items: filtered.slice((number - 1) * pageSize, number * pageSize) });
        }
        return reply([]);
    });
    await page.routeWebSocket("**/users/me/task-attention/ws", socket => socket.send(JSON.stringify({ type: "task-attention", data: { new_tasks: 0, overdue_tasks: 0, updated_tasks: 0, total: 0, items: [] } })));
    await page.routeWebSocket("**/projects/1/chat/ws", socket => socket.send(JSON.stringify({ type: "ready", project_id: 1, data: { heartbeat_seconds: 20, can_write: true, presence: [], typing: [] } })));
    return state;
}

test("реестр показывает загрузку, пустое состояние, ошибку и повтор", async ({ page }) => {
    const state = await mockProject(page, []);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/risks?*", async (route) => { await gate; await route.fallback(); }, { times: 1 });
    await page.goto("/projects/DEMO/risks");
    await expect(page.getByRole("status", { name: "Загрузка реестра рисков" })).toBeVisible();
    release();
    await expect(page.getByText("В проекте пока нет активных рисков", { exact: true })).toBeVisible();
    state.fail = true;
    await page.getByLabel("Поиск рисков").fill("API");
    await expect(page.getByText("Ошибка чтения реестра")).toBeVisible();
    state.fail = false;
    await page.getByRole("button", { name: "Повторить", exact: true }).click();
    await expect(page.getByText("Риски не найдены", { exact: true })).toBeVisible();
    expect(state.errors).toEqual([]);
});

test("создание, отмена и сохранение редактирования и подтверждение удаления в модальном окне", async ({ page }, info) => {
    const state = await mockProject(page, []);
    await page.goto("/projects/DEMO/risks");
    await page.getByRole("button", { name: "Добавить риск", exact: true }).click();
    const modal = page.getByRole("dialog", { name: "Новый риск" });
    await expect(modal.getByLabel("Название *", { exact: true })).toBeFocused();
    await modal.getByRole("button", { name: "Создать риск", exact: true }).click();
    expect(state.writes).toEqual([]);
    await modal.getByLabel("Название *", { exact: true }).fill("Задержка поставки");
    await modal.getByLabel("Описание *", { exact: true }).fill("Риск задержки документации");
    await modal.getByLabel("Вероятность *", { exact: true }).selectOption("HIGH");
    await modal.getByLabel("Влияние *", { exact: true }).selectOption("MEDIUM");
    await expect(modal.getByLabel("Уровень риска: Высокий")).toBeVisible();
    await modal.getByLabel("Ответственный", { exact: true }).selectOption("1");
    await modal.getByLabel("Поиск задачи проекта").fill("DEMO-142");
    await modal.getByLabel("Связанная задача", { exact: true }).selectOption("7");
    await modal.getByLabel("План митигации", { exact: true }).fill("Согласовать заранее");
    await modal.getByLabel("План реагирования", { exact: true }).fill("Резервный адаптер");
    await modal.getByRole("button", { name: "Создать риск", exact: true }).click();
    const riskDialog = page.getByRole("dialog", { name: "Риск RISK-100", exact: true });
    await expect(riskDialog).toBeVisible();
    expect(state.writes[0].body).toMatchObject({ task_id: 7, owner_user_id: 1, probability: "HIGH", impact: "MEDIUM" });
    expect(state.writes[0].body).not.toHaveProperty("risk_level");
    await riskDialog.getByRole("button", { name: "Редактировать", exact: true }).click();
    await riskDialog.getByLabel("Название *", { exact: true }).fill("Несохранённое название");
    await riskDialog.getByLabel("Описание *", { exact: true }).fill("Несохранённое описание");
    await riskDialog.getByRole("button", { name: "Отмена", exact: true }).click();
    await expect(riskDialog.getByRole("heading", { name: "Задержка поставки", exact: true })).toBeVisible();
    await expect(riskDialog.getByText("Риск задержки документации", { exact: true })).toBeVisible();
    expect(state.writes).toHaveLength(1);
    await riskDialog.getByRole("button", { name: "Редактировать", exact: true }).click();
    await expect(riskDialog.getByLabel("Название *", { exact: true })).toHaveValue("Задержка поставки");
    await riskDialog.getByLabel("Влияние *", { exact: true }).selectOption("LOW");
    await riskDialog.getByLabel("Связанная задача", { exact: true }).selectOption("");
    await expect(riskDialog.getByRole("button", { name: "Сохранить", exact: true })).toBeInViewport();
    await page.screenshot({ path: info.outputPath("risk-edit-modal.png"), animations: "disabled" });
    await riskDialog.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(riskDialog.getByLabel("Уровень риска: Средний")).toBeVisible();
    expect(state.writes[1].body).toEqual({ impact: "LOW", task_id: null });
    await riskDialog.getByRole("button", { name: "Удалить", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "Удалить RISK-100?" });
    await confirmation.getByRole("button", { name: "Отмена", exact: true }).click();
    expect(state.writes).toHaveLength(2);
    await expect(riskDialog.getByRole("button", { name: "Удалить", exact: true })).toBeFocused();
    await riskDialog.getByRole("button", { name: "Удалить", exact: true }).click();
    await page.screenshot({ path: info.outputPath("risk-delete-confirmation.png"), animations: "disabled" });
    await confirmation.getByRole("button", { name: "Удалить риск", exact: true }).click();
    await expect(riskDialog).not.toBeVisible();
    expect(state.writes[2].method).toBe("DELETE");
    expect(state.errors).toEqual([]);
});

test("матрица считает весь набор, фильтрует с клавиатуры и сбрасывает страницу", async ({ page }, info) => {
    const state = await mockProject(page, Array.from({ length: 30 }, (_, index) => ({ ...baseRisk, id: index + 1, key: `RISK-${index + 1}` })));
    await page.goto("/projects/DEMO/risks");
    const cell = page.getByRole("button", { name: "Высокая вероятность, высокое влияние: 30" });
    await expect(cell).toBeVisible();
    await expect(page.getByRole("list", { name: "Реестр рисков" }).getByRole("listitem")).toHaveCount(25);
    await page.getByRole("button", { name: "Следующая страница рисков" }).click();
    await expect(page.getByRole("list", { name: "Реестр рисков" }).getByRole("listitem")).toHaveCount(5);
    await cell.focus();
    await page.keyboard.press("Enter");
    await expect(cell).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => state.requests.some((url) => url.searchParams.get("probability") === "HIGH" && url.searchParams.get("impact") === "HIGH" && url.searchParams.get("page") === "1")).toBe(true);
    await cell.press("Space");
    await expect(cell).toHaveAttribute("aria-pressed", "false");
    await page.screenshot({ path: info.outputPath("risks-desktop.png"), fullPage: true });
    expect(state.errors).toEqual([]);
});

test("AI-предложение можно отклонить или отредактировать перед отдельным созданием", async ({ page }) => {
    const state = await mockProject(page, []);
    await page.goto("/projects/DEMO/risks");
    await page.getByRole("button", { name: "Предложить риски", exact: true }).click();
    await expect(page.getByText("DEMO-142: Ждём поставщика")).toBeVisible();
    expect(state.writes).toEqual([]);
    await page.getByRole("button", { name: "Отклонить", exact: true }).click();
    expect(state.writes).toEqual([]);
    await page.getByRole("button", { name: "Предложить риски", exact: true }).click();
    await page.getByRole("button", { name: "Проверить и создать", exact: true }).click();
    const modal = page.getByRole("dialog", { name: "Новый риск" });
    await modal.getByLabel("Название *", { exact: true }).fill("Проверенный риск поставщика");
    expect(state.writes).toEqual([]);
    await modal.getByRole("button", { name: "Создать риск", exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(1);
    expect(state.writes[0].body).toMatchObject({ source: "AI_SUGGESTED", title: "Проверенный риск поставщика" });
    expect(state.errors).toEqual([]);
});

test("рабочее окно риска по центру: Markdown прокручивается внутри, вкладки доступны с клавиатуры, задача заменяет риск", async ({ page }, info) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const state = await mockProject(page, [{ ...baseRisk, description: longDescription }]);
    await page.goto("/projects/DEMO/risks");
    const row = page.getByRole("button", { name: "Открыть RISK-12: Задержка интеграции CRM" });
    await row.click();
    const dialog = page.getByRole("dialog", { name: "Риск RISK-12", exact: true });
    await expect(dialog).toBeVisible();
    const box = (await dialog.boundingBox())!;
    expect(box.width, "Карточка риска использует рабочую ширину TaskDialog").toBeGreaterThan(1000);
    expect(box.width).toBeLessThanOrEqual(1152);
    expect(Math.abs(box.x + box.width / 2 - 720)).toBeLessThanOrEqual(2);
    expect(Math.abs(box.y + box.height / 2 - 500)).toBeLessThanOrEqual(2);
    const tabs = dialog.getByRole("tablist", { name: "Разделы риска", exact: true });
    await expect(tabs).toHaveAttribute("aria-orientation", "vertical");
    await expect(tabs.getByRole("tab")).toHaveText(["Основное", "Планы", "Контроль", "Связи"]);
    const overview = tabs.getByRole("tab", { name: "Основное", exact: true });
    const plans = tabs.getByRole("tab", { name: "Планы", exact: true });
    const control = tabs.getByRole("tab", { name: "Контроль", exact: true });
    const links = tabs.getByRole("tab", { name: "Связи", exact: true });
    await expect(overview).toHaveAttribute("aria-selected", "true");
    await expect(dialog.getByRole("tabpanel")).toHaveCount(1);
    await page.screenshot({ path: info.outputPath("risk-dialog-desktop-1440.png"), animations: "disabled" });
    await expectInternalScroll(dialog);
    await page.screenshot({ path: info.outputPath("risk-dialog-long-markdown-desktop.png"), animations: "disabled" });

    await overview.focus();
    await overview.press("ArrowDown");
    await expect(plans).toBeFocused();
    await expect(plans).toHaveAttribute("aria-selected", "true");
    const plansPanel = dialog.getByRole("tabpanel", { name: "Планы", exact: true });
    await expect(plansPanel.getByText(baseRisk.mitigation_plan, { exact: true })).toBeVisible();
    await expect(plansPanel.getByText(baseRisk.response_plan, { exact: true })).toBeVisible();
    const activePanelId = await plansPanel.getAttribute("id");
    for (let index = 0; index < 8; index += 1) {
        await page.keyboard.press("Tab");
        const focusedPanel = await page.evaluate(() => document.activeElement?.closest('[role="tabpanel"]')?.id ?? null);
        expect(focusedPanel === null || focusedPanel === activePanelId, "Фокус не попадает в неактивные разделы").toBe(true);
    }
    await plans.press("ArrowDown");
    await expect(control).toHaveAttribute("aria-selected", "true");
    await expect(dialog.getByRole("tabpanel", { name: "Контроль", exact: true }).getByText("Следующий пересмотр", { exact: true })).toBeVisible();
    await control.press("ArrowDown");
    await expect(links).toHaveAttribute("aria-selected", "true");
    const linkedTask = dialog.getByRole("button", { name: `${task.key} · ${task.title}`, exact: true });
    await expect(linkedTask).toBeVisible();
    expect(state.attentionGets, "Чтение связи не отмечает задачу просмотренной").toBe(0);
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(row).toBeFocused();

    await row.click();
    await dialog.getByRole("tab", { name: "Связи", exact: true }).click();
    await linkedTask.click();
    const taskDialog = page.getByRole("dialog", { name: `Задача ${task.key}`, exact: true });
    await expect(taskDialog).toBeVisible();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await expect(taskDialog.getByRole("tablist", { name: "Разделы задачи", exact: true })).toBeVisible();
    await expect.poll(() => state.attentionGets).toBe(1);
    await taskDialog.getByRole("button", { name: "Закрыть задачу", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page).toHaveURL(/\/projects\/DEMO\/risks$/);
    await expect(row).toBeVisible();
    expect(state.errors).toEqual([]);
});

test("на экранах 390 и 320 модальное окно риска не переполняется, Escape возвращает фокус", async ({ page }, info) => {
    const state = await mockProject(page, [{ ...baseRisk, description: longDescription }]);
    for (const width of [390, 320]) {
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/projects/DEMO/risks");
        const row = page.getByRole("button", { name: "Открыть RISK-12: Задержка интеграции CRM" });
        await expect(row).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        await row.click();
        const dialog = page.getByRole("dialog", { name: "Риск RISK-12", exact: true });
        await expect(dialog).toBeVisible();
        await expect(dialog.locator("..")).toHaveCSS("transform", "none");
        const box = (await dialog.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
        expect(box.width).toBeGreaterThanOrEqual(width - 2);
        const tabs = dialog.getByRole("tablist", { name: "Разделы риска", exact: true });
        await expect(tabs).toHaveAttribute("aria-orientation", "horizontal");
        await expectInternalScroll(dialog);
        await page.screenshot({ path: info.outputPath(`risk-dialog-mobile-${width}.png`), animations: "disabled" });
        const overview = tabs.getByRole("tab", { name: "Основное", exact: true });
        await overview.focus();
        await overview.press("ArrowRight");
        await expect(tabs.getByRole("tab", { name: "Планы", exact: true })).toHaveAttribute("aria-selected", "true");
        await tabs.getByRole("tab", { name: "Планы", exact: true }).press("End");
        const links = tabs.getByRole("tab", { name: "Связи", exact: true });
        await expect(links).toHaveAttribute("aria-selected", "true");
        await expect(links).toBeInViewport();
        await expect(dialog.getByRole("button", { name: `${task.key} · ${task.title}`, exact: true })).toBeVisible();
        await expect(dialog.getByRole("button", { name: "Закрыть риск", exact: true })).toBeInViewport();
        await page.keyboard.press("Escape");
        await expect(dialog).not.toBeVisible();
        await expect(row).toBeFocused();
    }
    expect(state.errors).toEqual([]);
});
