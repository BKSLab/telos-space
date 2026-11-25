import { expect, test, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";
import type { GitConnection, KnowledgeMap } from "../src/lib/projectKnowledge";

test.beforeEach(async ({ page }) => { await page.clock.setFixedTime(new Date("2026-11-20T12:00:00Z")); });

function historyPage<T extends { id: string; kind: string; created_at: string; title: string; detail?: string | null }>(items: T[], url: URL) {
    const query = url.searchParams;
    const timezone = query.get("timezone") ?? "UTC";
    const localDay = (stamp: string) => {
        const parts = new Intl.DateTimeFormat("en", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(stamp));
        return ["year", "month", "day"].map(type => parts.find(part => part.type === type)!.value).join("-");
    };
    const matching = items.filter(item => (!query.get("kind") || item.kind === query.get("kind"))
        && (!query.get("event_id") || item.id === query.get("event_id"))
        && (!query.get("until") || localDay(item.created_at) <= query.get("until")!)
        && (!query.get("q") || `${item.title} ${item.detail ?? ""}`.includes(query.get("q")!)))
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
    const selected = matching.filter(item => !query.get("since") || localDay(item.created_at) >= query.get("since")!);
    const previous_day = query.get("include_previous") === "true" && query.get("since")
        ? matching.map(item => localDay(item.created_at)).find(day => day < query.get("since")!) ?? null : null;
    const offset = Number(query.get("offset") ?? 0);
    const limit = Number(query.get("limit") ?? 20);
    return { items: selected.slice(offset, offset + limit), next_offset: offset + limit < selected.length ? offset + limit : null, previous_day, revision: "0" };
}

function connection(id: number, name: string, ref: string): GitConnection {
    return { id, project_id: 1, repository_id: 100 + id, provider: "github", owner: "example", repo: name.toLowerCase(), name, purpose: `${name}: исходники и тесты`, ref, enabled: true, is_private: false, share_private_with_project: false, has_credential: false, has_webhook_secret: false, revision: 3, generation: 4, credential_revision: 0, state: id === 2 ? "partial" : "ready", observed_sha: "a".repeat(40), indexed_sha: id === 2 ? "b".repeat(40) : "a".repeat(40), observed_at: "2026-11-16T12:00:00Z", indexed_at: "2026-11-16T12:00:00Z", checked_at: "2026-11-16T12:00:00Z", last_error_code: null, coverage: { total_files: 10, cataloged_files: 10, indexed_files: 5, metadata_files: 5, metadata_counts: { binary_file: 5 }, failed_files: 0, truncated: id === 2 } };
}

for (const width of [320, 1440]) {
    test(`webhook и правила стадий доступны на ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        const state = mockState();
        state.stages.push({ ...state.stages[0], id: 20, name: "Тестирование", order_index: 1 }, { ...state.stages[0], id: 30, name: "Готово", order_index: 2, is_done_stage: true });
        await installMockApi(page, state);
        await sourcesApi(page);
        let item: GitConnection = { ...connection(1, "Frontend", "heads/main"), sync_stage_ids: [], poll_interval_seconds: 3600, last_sync_reason: "manual", last_sync_requested_at: "2026-11-20T11:00:00Z", next_poll_at: "2026-11-20T13:00:00Z", last_webhook_received_at: "2026-11-20T11:30:00Z", last_webhook_event: "ping", last_webhook_processed_at: null, coverage: { total_files: 700, cataloged_files: 700, indexed_files: 689, metadata_files: 11, failed_files: 0, truncated: false, metadata_counts: { binary_file: 6, submodule: 2, lfs_pointer: 3 } } };
        const writes: Record<string, unknown>[] = [];
        await page.route("**/api/v1/projects/1/git/connections**", async route => {
            const request = route.request();
            const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET,POST,PATCH,DELETE,PUT,OPTIONS" };
            if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
            if (new URL(request.url()).pathname.endsWith("/tree")) return route.fulfill({ headers, json: { connection_id: 1, repository_id: 101, commit_sha: "a".repeat(40), total_files: 4, offset: 0, limit: 30, next_offset: null, truncated: false, stale: false, items: [
                { path: ".env.example", sha: "c".repeat(40), mode: "100644", type: "blob", size: 128, index_state: "ready", index_reason: null },
                { path: "dependencies/generated/проверка-длинного-пути/package-lock.json", sha: "d".repeat(40), mode: "100644", type: "blob", size: 1000000, index_state: "ready", index_reason: null },
                { path: "assets/cover.png", sha: "e".repeat(40), mode: "100644", type: "blob", size: 12000, index_state: "metadata", index_reason: "binary_file" },
                { path: "large-file.log", sha: "f".repeat(40), mode: "100644", type: "blob", size: 100000000, index_state: "error", index_reason: "transport_limit" },
            ] } });
            if (request.method() === "GET") {
                const path = new URL(request.url()).pathname;
                if (path.endsWith("/snapshots") || path.endsWith("/symbols")) return route.fulfill({ headers, json: { items: [], next_offset: null } });
                if (path.endsWith("/1")) return route.fulfill({ headers, json: item });
                if (path.endsWith("/file")) return route.fulfill({ headers, json: { path: ".env.example", commit_sha: "a".repeat(40), text: "EXAMPLE=value", line_start: 1, line_end: 1, offset: 0, next_offset: null, total_chars: 13, source_url: "", partial: false, stale: false } });
                return route.fulfill({ headers, json: { items: [item], total: 1, next_offset: null } });
            }
            const body = request.postDataJSON(); writes.push(body);
            if (request.method() === "PATCH") item = { ...item, ...body, revision: item.revision + 1 };
            if (request.method() === "PUT") item = { ...item, has_webhook_secret: Boolean(body.webhook_secret), revision: item.revision + 1 };
            return route.fulfill({ headers, json: item });
        });
        await page.goto("/projects/DEMO/settings");
        await page.locator("summary").getByText("Управлять репозиториями", { exact: true }).click();
        await expect(page.getByText("Раз в час", { exact: true })).toBeVisible();
        await page.getByText("Почему только метаданные", { exact: true }).click();
        await expect(page.getByText("Двоичные файлы", { exact: true })).toBeVisible();
        await page.goto("/projects/DEMO/knowledge?view=code&connection=1");
        if (width < 900) await page.getByRole("button", { name: "Дерево", exact: true }).click();
        await expect(page.getByRole("treeitem", { name: /\.env\.example/ })).toBeVisible();
        await expect(page.getByRole("treeitem", { name: /package-lock.json/ })).toBeVisible();
        await expect(page.getByRole("treeitem", { name: /cover.png/ })).toHaveAccessibleDescription("Двоичные файлы");
        await expect(page.getByRole("treeitem", { name: /large-file.log/ })).toHaveAccessibleDescription("Ошибка чтения");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`git-file-catalog-${width}.png`), animations: "disabled" });
        await page.getByRole("treeitem", { name: /\.env\.example/ }).click();
        await expect(page.getByLabel("Код .env.example")).toContainText("EXAMPLE=value");
        await page.goto("/projects/DEMO/settings");
        await page.locator("summary").getByText("Управлять репозиториями", { exact: true }).click();
        await page.getByRole("button", { name: "Настроить", exact: true }).click();
        await page.getByLabel("Тестирование", { exact: true }).check();
        await page.getByLabel("Готово", { exact: true }).check();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`git-stage-settings-${width}.png`), fullPage: true, animations: "disabled" });
        await page.getByRole("button", { name: "Сохранить источник", exact: true }).click();
        await expect.poll(() => writes[0]?.sync_stage_ids).toEqual([20, 30]);
        await page.locator("summary").filter({ hasText: "Webhook GitHub" }).click();
        await page.getByText("Как подключить webhook в GitHub", { exact: true }).click();
        await expect(page.getByText(/Нужны права владельца или администратора/)).toBeVisible();
        const payloadUrl = page.getByLabel("Payload URL", { exact: true });
        if (await payloadUrl.count()) await expect(payloadUrl).toHaveValue(/\/api\/v1\/projects\/1\/git\/connections\/1\/events$/);
        await expect(page.getByText(/Проверка связи \(ping\)/)).toBeVisible();
        await page.getByLabel("Новый секрет webhook", { exact: true }).fill("synthetic-webhook-secret-for-ui-only");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`git-webhook-settings-${width}.png`), fullPage: true, animations: "disabled" });
        await page.getByRole("button", { name: "Сохранить секрет", exact: true }).click();
        await expect(page.getByLabel("Новый секрет webhook", { exact: true })).toHaveCount(0);
        await expect(page.getByText(/Успешная сверка события ещё не подтверждена/)).toBeVisible();
        expect(state.errors).toEqual([]);
    });
}

const knowledge: KnowledgeMap = {
    project_id: 1, generation: 2, built_generation: 2, status: "partial", updated_at: "2026-11-16T12:00:00Z",
    items: [{ id: "a9d98927-7467-4017-a83e-fc01bf0b58ee", revision: 1, kind: "source_fact", category: "task_results", text: "Участник сообщил о выполнении импорта", state: "current", acceptance: "unreviewed", verification: "participant_report", source_ids: ["task_report:12"], evidence: [{ source_id: "task_report:12", native_revision: "2", source_hash: "a".repeat(64), content_hash: "b".repeat(64), title: "Опубликованный отчёт", quote: "Импорт реализован", start: 0, end: 17, document_ref: null, task_id: 12, checked_at: "2026-11-16T12:00:00Z", coverage: "full" }], conflicts_with: [], updated_at: "2026-11-16T12:00:00Z", reason: null }],
    gaps: [{ code: "no_product_proof", category: "task_results", message: "Нет независимого подтверждения работы продукта" }],
    coverage: { sources_total: 3, sources_current: 2, sources_pending: 1, sources_failed: 0, sources_skipped: 0, complete: false, claims_current: 1, claims_conflicting: 0, claims_stale: 0, chars_processed: 100, chars_total: 200, reasons: ["pending"], method: "grounded_literal_v1" }, total: 1, offset: 0, limit: 20, has_more: false, next_offset: null,
};

async function sourcesApi(page: Page) {
    const connections = [
        connection(1, "Frontend", "main"),
        { ...connection(2, "Auth", "release"), last_error_code: "rate_limited" },
        connection(3, "Gateway", "stable"),
    ];
    let patches = 0;
    await page.route("**/api/v1/projects/1/**", async route => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname.replace("/api/v1/projects/1", "");
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4191", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET,POST,PATCH,DELETE,PUT,OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, headers, json });
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (path === "/knowledge/map/status") return reply(knowledge);
        if (path.startsWith("/knowledge/map/claims/")) return reply(knowledge.items[0]);
        if (path === "/knowledge/map") {
            const q = url.searchParams.get("q");
            const category = url.searchParams.get("category");
            const exclude = url.searchParams.get("exclude_category");
            const claims = [...knowledge.items, { ...knowledge.items[0], id: "raw-css", category: "facts", text: "В источнике указано: line-height: 1.35;" }];
            const items = claims.filter(item => item.category !== exclude && (!category || item.category === category) && (!q || item.text?.includes(q)));
            return reply({ ...knowledge, items, total: items.length });
        }
        if (path === "/history/version") return reply({ revision: "0" });
        if (path === "/history") {
            const items = [
                { id: "task_report:12", kind: "task_report", title: "Отчёт · Импорт · v2", detail: "Импорт реализован", actor_name: "Тестов Тест", task_id: 100, created_at: "2026-11-20T11:00:00Z" },
                { id: "project_context:1:3", kind: "project_context", title: "Изменение паспорта проекта", detail: '["goal"]', actor_name: "Тестов Тест", task_id: null, created_at: "2026-11-20T09:35:00Z" },
                { id: "document:1", kind: "document_created", title: "Создан документ · Решение по импорту", detail: null, actor_name: "Тестов Тест", task_id: null, document_ref: "d1_ZG9jdW1lbnQ6MQ", created_at: "2026-11-19T09:35:00Z" },
            ];
            return reply(historyPage(items, url));
        }
        if (path === "/git/connections" && request.method() === "GET") return reply({ items: connections, total: 3, offset: 0, limit: 20, next_offset: null });
        if (/^\/git\/connections\/\d+$/.test(path) && request.method() === "GET") return reply(connections.find(item => item.id === Number(path.split("/").pop())));
        if (/\/(snapshots|symbols)$/.test(path)) return reply({ items: [], next_offset: null });
        if (path.endsWith("/tree")) return reply({ items: [], commit_sha: "a".repeat(40), next_offset: null, total_files: 0, truncated: false, stale: false });
        if (path === "/git/connections/1" && request.method() === "PATCH") {
            const body = request.postDataJSON();
            patches++;
            if (patches === 1) {
                connections[0] = { ...connections[0], revision: 4, ref: "server-branch" };
                return reply({ detail: "Настройки источника изменены" }, 409);
            }
            expect(body.expected_revision).toBe(4);
            connections[0] = { ...connections[0], ...body, revision: 5 };
            return reply(connections[0]);
        }
        const hit = { connection_id: 1, repository_id: 101, commit_sha: "a".repeat(40), path: "src/main.py", source_url: "https://github.com/example/frontend/blob/" + "a".repeat(40) + "/src/main.py", title: "src/main.py", excerpt: "def main(): return 1", file_id: 17, stale: false, partial: false };
        if (path === "/git/search") return reply({ items: [hit], partial: true });
        if (path === "/git/connections/1/file") return reply({ ...hit, text: "def main():\n    return 1\n", line_start: 1, line_end: 3, offset: 0, next_offset: null, total_chars: 25 });
        if (path === "/git/map") return reply({ project_id: 1, manifest: connections, components: [{ ...hit, kind: "python", symbols: [], imports: [] }], relations: [], total_components: 1, components_truncated: false, partial: true });
        return route.fallback();
    });
    return connections;
}

for (const width of [320, 1440]) {
    test(`история показывает сохранённые удаления и новые категории — ${width}`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        await installMockApi(page, mockState());
        await sourcesApi(page);
        const kinds = ["project_activity", "project_member", "project_stage", "risk_activity", "document_activity", "project_plan", "project_board"];
        const requests: URL[] = [];
        const items = kinds.map((kind, index) => ({ id: `history:${index}`, kind, title: `Сохранённое событие ${index}`, detail: "Название и пояснение сохранены после удаления исходного объекта", created_at: "2026-11-20T10:00:00Z", actor_name: "Иван Петров", task_id: null, comment_id: null, report_id: null, document_ref: null, source_available: false, event_type: "TITLE_CHANGED", from_value: "Прежнее название", to_value: "Новое название" }));
        await page.route("**/api/v1/projects/1/history?**", route => {
            const url = new URL(route.request().url()); requests.push(url);
            return route.fulfill({ json: historyPage(items, url) });
        });
        await page.goto("/projects/DEMO/knowledge?view=history&timezone=Europe%2FSamara");
        const timeline = page.getByLabel("События проекта", { exact: true });
        await expect(timeline.locator(".history-event:visible")).toHaveCount(4);
        await page.getByRole("button", { name: "Ещё 3 события за этот день" }).click();
        await expect(timeline.locator(".history-event:visible")).toHaveCount(7);
        await expect(timeline.getByText("Историческая запись", { exact: true })).toHaveCount(7);
        await expect(timeline.getByRole("button", { name: /^Открыть/ })).toHaveCount(0);
        await expect(timeline.getByRole("link", { name: /^Открыть/ })).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`history-journal-snapshots-${width}.png`), animations: "disabled" });
        await page.getByLabel("Тип события", { exact: true }).selectOption("risk_activity");
        await expect(timeline.locator(".history-event:visible")).toHaveCount(1);
        await expect(timeline.getByText("Изменения рисков", { exact: true })).toBeVisible();
        expect(requests.some(url => url.searchParams.get("kind") === "risk_activity")).toBe(true);
    });

    test(`история с вехами и плотными днями — ${width}`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        await installMockApi(page, mockState());
        await sourcesApi(page);
        const kinds = ["task_activity", "task_report", "task_comment", "project_deadline", "document_created", "risk_created"];
        const titles = ["Смена стадии · Подключение репозитория", "Отчёт · Обновление раздела знаний · v2", "Комментарий · Обсуждение навигации", "Изменение срока проекта", "Создан документ · Решение команды", "Добавлен риск · Срок поставки"];
        const items = Array.from({ length: 16 }, (_, index) => ({
            id: `demo:${index}`, kind: kinds[index % kinds.length], title: titles[index % titles.length],
            detail: index % kinds.length === 0 ? null : "Сохраняем обоснование решения и ссылки на исходные материалы. Подробности доступны в связанной записи проекта.",
            from_value: index % kinds.length === 0 ? "В работе" : null, to_value: index % kinds.length === 0 ? "Тестирование" : null,
            created_at: index < 12 ? "2026-11-20T09:30:00Z" : "2026-11-19T10:00:00Z", actor_name: "Иван Петров", task_id: null,
        }));
        await page.route("**/api/v1/projects/1/history?**", route => route.fulfill({ json: historyPage(items, new URL(route.request().url())) }));
        await page.goto("/projects/DEMO/knowledge?view=history&timezone=Europe%2FSamara");
        await expect(page.locator(".history-event:visible")).toHaveCount(4);
        await expect(page.getByRole("button", { name: /Развернуть 19 ноября/ })).toHaveAttribute("aria-expanded", "false");
        const colors = await page.locator(".history-event-marker").evaluateAll(elements => elements.slice(0, 4).map(element => getComputedStyle(element).color));
        expect(new Set(colors).size).toBe(4);
        await page.screenshot({ path: info.outputPath("history-milestones.png"), animations: "disabled" });
        await page.getByRole("button", { name: "Ещё 8 событий за этот день" }).click();
        await expect(page.locator(".history-event:visible")).toHaveCount(12);
        const day = page.getByRole("button", { name: /Свернуть 20 ноября/ });
        await day.focus();
        await page.keyboard.press("Enter");
        await expect(page.locator(".history-event:visible")).toHaveCount(0);
        await page.getByRole("button", { name: "Свернуть даты" }).click();
        await expect(page.locator(".history-event:visible")).toHaveCount(0);
        await page.screenshot({ path: info.outputPath("history-collapsed.png"), animations: "disabled" });
        await page.getByRole("button", { name: "Развернуть даты" }).click();
        await expect(page.locator(".history-event:visible")).toHaveCount(16);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("история загружает неделю целиком и добавляет старые даты только при прокрутке", async ({ page }, info) => {
    await installMockApi(page, mockState());
    await sourcesApi(page);
    const makeEvent = (id: number, created_at: string) => ({ id: `task_comment:${id}`, kind: "task_comment", title: `Комментарий ${id}`, detail: "Обсуждение решения", created_at, actor_name: "Иван Петров", task_id: null });
    const items = [
        ...Array.from({ length: 4 }, (_, i) => makeEvent(i, "2026-11-20T10:00:00Z")),
        ...Array.from({ length: 105 }, (_, i) => makeEvent(100 + i, "2026-11-19T10:00:00Z")),
        makeEvent(300, "2026-11-14T10:00:00Z"),
        makeEvent(400, "2026-11-13T10:00:00Z"),
        makeEvent(500, "2026-10-30T10:00:00Z"),
    ];
    const requests: URL[] = [];
    await page.route("**/api/v1/projects/1/history?**", route => {
        const url = new URL(route.request().url()); requests.push(url);
        return route.fulfill({ json: historyPage(items, url) });
    });
    await page.goto("/projects/DEMO/knowledge?view=history&timezone=Europe%2FSamara");
    await expect(page.getByRole("button", { name: /Свернуть 20 ноября/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Развернуть 19 ноября/ })).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("button", { name: /Развернуть 14 ноября/ })).toHaveCount(1);
    await expect(page.getByRole("button", { name: /Развернуть 19 ноября/ })).toContainText("105 событий");
    await expect(page.getByRole("button", { name: "Загрузить более ранние события" })).toHaveCount(1);
    const requestedWindows = requests.filter(url => url.searchParams.get("limit") === "100").map(url => `${url.searchParams.get("since")}/${url.searchParams.get("until")}/${url.searchParams.get("offset")}`);
    // React StrictMode may abort/restart the first request; completed windows must stay in the initial week.
    expect([...new Set(requestedWindows)]).toEqual(["2026-11-14/2026-11-20/0", "2026-11-14/2026-11-20/100"]);
    await expect(page.getByRole("heading", { name: /13 ноября/ })).toHaveCount(0);
    await expect(page.getByText(/Страница \d+/)).toHaveCount(0);
    await page.getByRole("button", { name: /Свернуть 20 ноября/ }).click();
    await expect(page.getByRole("button", { name: /Развернуть 20 ноября/ })).toHaveAttribute("aria-expanded", "false");
    const workspace = page.getByRole("region", { name: "Знания проекта", exact: true });
    const box = await workspace.boundingBox();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height - 30);
    await page.mouse.wheel(0, 1800);
    await expect(page.getByRole("button", { name: /Развернуть 13 ноября/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Развернуть 20 ноября/ })).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("button", { name: /Развернуть 13 ноября/ })).toHaveAttribute("aria-expanded", "false");
    // Activate directly: Playwright's action scroll would independently trigger auto-loading.
    await page.getByRole("button", { name: "Загрузить более ранние события" }).dispatchEvent("click");
    await expect(page.getByRole("button", { name: /Развернуть 30 октября/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Загрузить более ранние события" })).toHaveCount(0);
    const ids = await page.locator(".history-event").evaluateAll(elements => elements.map(element => element.id));
    expect(new Set(ids).size).toBe(ids.length);
    await page.screenshot({ path: info.outputPath("history-loaded-weeks.png"), animations: "disabled" });
});

test("история не подгружает события за границами выбранного периода", async ({ page }) => {
    await installMockApi(page, mockState());
    await sourcesApi(page);
    const items = ["2026-11-20", "2026-11-19", "2026-11-13"].map((day, index) => ({ id: `task_comment:${index}`, kind: "task_comment", title: `Комментарий ${day}`, detail: null, created_at: `${day}T10:00:00Z`, actor_name: "Иван Петров", task_id: null }));
    const requests: URL[] = [];
    await page.route("**/api/v1/projects/1/history?**", route => {
        const url = new URL(route.request().url()); requests.push(url);
        return route.fulfill({ json: historyPage(items, url) });
    });
    await page.goto("/projects/DEMO/knowledge?view=history&since=2026-11-19&until=2026-11-20&timezone=Europe%2FSamara");
    await expect(page.getByRole("heading", { name: /19 ноября/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Загрузить более ранние события" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /13 ноября/ })).toHaveCount(0);
    expect(requests.every(url => url.searchParams.get("since") === "2026-11-19" && url.searchParams.get("until") === "2026-11-20")).toBe(true);
});

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 390, height: 844 }]) {
    test(`показывает происхождение знаний и версии трёх repo — ${viewport.name}`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        await installMockApi(page, mockState());
        await sourcesApi(page);
        await page.goto("/projects/DEMO");
        await page.getByRole("link", { name: "Открыть карту" }).click();
        await expect(page.getByText("Участник сообщил о выполнении импорта", { exact: true })).toBeVisible();
        await expect(page.getByText("Отчёт участника", { exact: true })).toBeVisible();
        await page.getByText("Источники и версии (1)", { exact: true }).click();
        await expect(page.getByText("Импорт реализован", { exact: true })).toBeVisible();
        await page.screenshot({ path: info.outputPath(`knowledge-${viewport.name}.png`) });
        await page.getByRole("button", { name: "Закрыть источники" }).click();
        await page.goto("/projects/DEMO/settings");
        const paused = page.getByRole("listitem").filter({ hasText: "Индексация приостановлена" });
        await expect(paused).toContainText("Auth");
        await expect(paused).toContainText("release");
        await page.locator("summary").getByText("Управлять репозиториями", { exact: true }).click();
        await expect(page.getByRole("heading", { name: "Frontend", exact: true })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Auth", exact: true })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Gateway", exact: true })).toBeVisible();
        await page.goto("/projects/DEMO/knowledge?view=code&code_view=search");
        await page.getByLabel("Найти код и документацию", { exact: true }).fill("main");
        await page.getByRole("button", { name: "Найти в репозиториях" }).click();
        await page.getByRole("button", { name: "Открыть", exact: true }).click();
        await expect(page.getByLabel("Код src/main.py")).toContainText("def main():");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
        await page.screenshot({ path: info.outputPath(`git-file-${viewport.name}.png`) });
    });
}

test("конфликт ref сохраняет draft до явной загрузки новой версии", async ({ page }) => {
    await installMockApi(page, mockState());
    await sourcesApi(page);
    await page.goto("/projects/DEMO/settings");
    await page.locator("summary").getByText("Управлять репозиториями", { exact: true }).click();
    await page.getByRole("button", { name: "Настроить", exact: true }).first().click();
    await page.getByLabel("Ветка или ref", { exact: true }).fill("my-draft");
    await page.getByRole("button", { name: "Сохранить источник", exact: true }).click();
    await expect(page.getByText("Настройки источника изменены", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Ветка или ref", { exact: true })).toHaveValue("my-draft");
    await page.getByRole("button", { name: "Загрузить актуальные настройки" }).click();
    await expect(page.getByLabel("Ветка или ref", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Настроить", exact: true }).first().click();
    await expect(page.getByLabel("Ветка или ref", { exact: true })).toHaveValue("server-branch");
    await page.getByLabel("Ветка или ref", { exact: true }).fill("develop");
    await page.getByRole("button", { name: "Сохранить источник", exact: true }).click();
    await expect(page.getByLabel("Ветка или ref", { exact: true })).toHaveCount(0);
});

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 320, height: 844 }]) {
    test(`знания проекта: поиск по карте и лента — ${viewport.name}`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        await installMockApi(page, mockState());
        await sourcesApi(page);
        await page.goto("/projects/DEMO/knowledge");
        await expect(page.getByRole("heading", { name: "Результаты задач" })).toBeVisible();
        await expect(page.getByText(/line-height: 1\.35/)).toHaveCount(0);
        if (viewport.name === "mobile") await page.getByRole("combobox", { name: "Раздел", exact: true }).selectOption("task_results");
        else await page.getByRole("navigation", { name: "Темы знаний" }).getByRole("button", { name: "Результаты задач", exact: true }).click();
        const mapSearch = page.getByRole("searchbox", { name: "Поиск по утверждениям" });
        await mapSearch.fill("неизвестный текст");
        await page.getByRole("button", { name: "Найти", exact: true }).click();
        await expect(page.getByText("По заданным условиям утверждений нет.")).toBeVisible();
        await page.getByRole("button", { name: "Сбросить", exact: true }).click();
        await expect(page.getByRole("heading", { name: "Результаты задач" })).toBeVisible();
        await page.getByRole("link", { name: "Код и репозитории" }).click();
        await expect(page.getByRole("heading", { name: "Frontend" })).toBeVisible();
        await page.getByRole("link", { name: "Карта", exact: true }).click();
        await expect(page.getByRole("heading", { name: "Результаты задач" })).toBeVisible();
        await page.screenshot({ path: info.outputPath("knowledge-map.png") });

        await page.getByRole("link", { name: "История", exact: true }).click();
        await expect(page).toHaveURL(/view=history/);
        await expect(page.getByRole("heading", { name: /20 ноября/ })).toBeVisible();
        await expect(page.getByRole("heading", { name: /19 ноября/ })).toBeVisible();
        await expect(page.getByText("Цель", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: /Развернуть 19 ноября/ }).click();
        await expect(page.getByRole("link", { name: "Открыть документ" })).toHaveAttribute("href", "/projects/DEMO/docs/d1_ZG9jdW1lbnQ6MQ");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath("project-history.png"), animations: "disabled" });
        await page.getByLabel("Тип события").selectOption("task_report");
        await page.getByRole("button", { name: "Найти", exact: true }).click();
        await expect(page).toHaveURL(/kind=task_report/);
        await expect(page.getByText("Отчёт · Импорт · v2")).toBeVisible();
        await expect(page.getByText("Изменение паспорта проекта", { exact: true })).toHaveCount(0);
        await page.reload();
        await expect(page.getByLabel("Тип события")).toHaveValue("task_report");
        await expect(page.getByText("Отчёт · Импорт · v2")).toBeVisible();
        await expect(page.getByText("Изменение паспорта проекта", { exact: true })).toHaveCount(0);
    });
}

for (const width of [320, 1440]) {
    test(`индексация Git: охват, очередь, ошибки и версии — ${width}`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        await installMockApi(page, mockState());
        await sourcesApi(page);
        const ready = { ...connection(1, "Frontend", "main"), coverage: { total_files: 120, cataloged_files: 120, indexed_files: 120, metadata_files: 0, metadata_counts: {}, failed_files: 0, truncated: false } };
        const items = [
            ready,
            { ...ready, id: 2, name: "API · обновление", state: "syncing", observed_sha: "b".repeat(40) },
            { ...ready, id: 3, name: "Документация · очередь", state: "pending", indexed_sha: null, coverage: { total_files: 0, cataloged_files: 0, indexed_files: 0, metadata_files: 0, metadata_counts: {}, failed_files: 0, truncated: false } },
            { ...ready, id: 4, name: "Архив · неполный охват", state: "partial", coverage: { total_files: 100, cataloged_files: 92, indexed_files: 60, metadata_files: 30, metadata_counts: { binary_file: 30 }, failed_files: 2, truncated: true } },
            { ...ready, id: 5, name: "Сервис · доступ потерян", state: "access_lost", last_error_code: "credential_missing" },
        ];
        await page.route("**/api/v1/projects/1/git/connections?**", route => route.fulfill({ json: { items, total: 5, offset: 0, limit: 20, next_offset: null } }));
        await page.goto("/projects/DEMO/knowledge?view=code");
        await page.getByRole("button", { name: "Панель кода", exact: true }).click();
        await expect(page.getByText("Актуальны: 1", { exact: true })).toBeVisible();
        await expect(page.getByText("В работе: 2", { exact: true })).toBeVisible();
        await expect(page.getByText("Требуют внимания: 2", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Закрыть панель кода" }).click();
        await expect(page.getByText("Индекс актуален", { exact: true })).toBeVisible();
        for (const item of items) await page.getByText(`Диагностика ${item.name}`, { exact: true }).click();
        await expect(page.getByRole("list", { name: "Этапы индексации" })).toHaveCount(5);
        await expect(page.getByRole("progressbar")).toHaveCount(0);
        await expect(page.getByText(/100%/)).toHaveCount(0);
        await page.screenshot({ path: info.outputPath("git-index-overview.png"), animations: "disabled" });
        await page.getByText("Индексируется", { exact: true }).scrollIntoViewIfNeeded();
        await expect(page.getByText("Предыдущая версия", { exact: true })).toBeVisible();
        await expect(page.getByText("Это охват файлов, а не процент выполнения текущего обновления.")).toBeVisible();
        await page.screenshot({ path: info.outputPath("git-index-active.png"), animations: "disabled" });
        await page.getByText("Охват ограничен. Данные не отражают весь репозиторий.").scrollIntoViewIfNeeded();
        await expect(page.getByRole("img", { name: "Охват файлов: текст 60, только метаданные 30, ошибок 2, в каталоге 92, всего 100" })).toBeVisible();
        await expect(page.getByLabel("b".repeat(40), { exact: true })).toHaveText("bbbbbbbb");
        await expect(page.getByRole("button", { name: "Настроить", exact: true })).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath("git-index-partial.png"), animations: "disabled" });
        await page.goto("/projects/DEMO/settings");
        await page.locator("summary").getByText("Управлять репозиториями", { exact: true }).click();
        await expect(page.getByRole("link", { name: "Настроить доступ к GitHub" })).toHaveAttribute("href", "/profile?tab=github");
    });

    test(`сохраняет выборку и открывает источник вне страницы — ${width}`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 900 });
        await installMockApi(page, mockState());
        await sourcesApi(page);
        const items = Array.from({ length: 45 }, (_, index) => ({ ...knowledge.items[0], id: `claim-${index}`, text: `Решение ${index + 1}: сохранять источник и историю изменений. ${"Обоснование решения. ".repeat(8)}`, state: index === 2 ? "conflicting" : "current" }));
        await page.route("**/api/v1/projects/1/knowledge/map**", async route => {
            const url = new URL(route.request().url());
            if (url.pathname.includes("/claims/")) {
                const selected = items.find(item => item.id === url.pathname.split("/").at(-1));
                return route.fulfill({ status: selected ? 200 : 404, json: selected ?? { detail: "Недоступно" } });
            }
            const offset = Number(url.searchParams.get("offset") ?? 0);
            const q = url.searchParams.get("q");
            const filtered = items.filter(item => !q || item.text.includes(q));
            return route.fulfill({ json: { ...knowledge, items: filtered.slice(offset, offset + 20), total: filtered.length, offset, has_more: offset + 20 < filtered.length, next_offset: offset + 20 < filtered.length ? offset + 20 : null } });
        });
        await page.goto("/projects/DEMO/knowledge");
        const first = page.getByRole("button", { name: `Источники и версии: ${items[0].text}`, exact: true });
        await expect(first).toBeVisible();
        expect((await first.boundingBox())!.y).toBeLessThan(800);
        await page.screenshot({ path: info.outputPath("dense-map.png") });
        await page.getByRole("searchbox", { name: "Поиск по утверждениям" }).fill("Решение");
        await page.getByRole("button", { name: "Найти", exact: true }).click();
        await expect(page).toHaveURL(/mq=/);
        await page.getByRole("link", { name: "История", exact: true }).click();
        await expect(page.getByLabel("Тип события")).toBeVisible();
        await page.getByLabel("Тип события").selectOption("task_report");
        await page.getByRole("button", { name: "Найти", exact: true }).click();
        await expect(page).toHaveURL(/kind=task_report/);
        await page.getByRole("link", { name: "Карта", exact: true }).click();
        await expect(page.getByRole("searchbox", { name: "Поиск по утверждениям" })).toHaveValue("Решение");
        await page.goto("/projects/DEMO/knowledge?mq=Решение&claim=claim-44");
        const drawer = page.getByRole("dialog", { name: "Источники знания" });
        await expect(drawer.getByText(items[44].text, { exact: true })).toBeVisible();
        await page.screenshot({ path: info.outputPath("source-drawer.png"), animations: "disabled" });
        await page.getByRole("button", { name: "Закрыть источники" }).click();
        await expect(drawer).toHaveCount(0);
        await expect(page.getByRole("searchbox", { name: "Поиск по утверждениям" })).toHaveValue("Решение");
        await page.goto("/projects/DEMO/knowledge?claim=removed");
        await expect(page.getByText("Знание недоступно или было удалено.")).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}
