import { expect, test, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";
import type { GitConnection, KnowledgeMap } from "../src/lib/projectKnowledge";

function connection(id: number, name: string, ref: string): GitConnection {
    return { id, project_id: 1, repository_id: 100 + id, provider: "github", owner: "example", repo: name.toLowerCase(), name, purpose: `${name}: исходники и тесты`, ref, include_paths: [""], exclude_patterns: [], enabled: true, is_private: false, share_private_with_project: false, has_credential: false, has_webhook_secret: false, revision: 3, generation: 4, credential_revision: 0, state: id === 2 ? "partial" : "ready", observed_sha: "a".repeat(40), indexed_sha: id === 2 ? "b".repeat(40) : "a".repeat(40), observed_at: "2026-11-16T12:00:00Z", indexed_at: "2026-11-16T12:00:00Z", checked_at: "2026-11-16T12:00:00Z", last_error_code: null, coverage: { total_files: 10, indexed_files: 5, excluded_files: 5, failed_files: 0, truncated: id === 2 } };
}

const knowledge: KnowledgeMap = {
    project_id: 1, generation: 2, built_generation: 2, status: "partial", updated_at: "2026-11-16T12:00:00Z",
    items: [{ id: "a9d98927-7467-4017-a83e-fc01bf0b58ee", revision: 1, kind: "source_fact", category: "task_results", text: "Участник сообщил о выполнении импорта", state: "current", acceptance: "unreviewed", verification: "participant_report", source_ids: ["task_report:12"], evidence: [{ source_id: "task_report:12", native_revision: "2", source_hash: "a".repeat(64), content_hash: "b".repeat(64), title: "Опубликованный отчёт", quote: "Импорт реализован", start: 0, end: 17, document_ref: null, task_id: 12, checked_at: "2026-11-16T12:00:00Z", coverage: "full" }], conflicts_with: [], updated_at: "2026-11-16T12:00:00Z", reason: null }],
    gaps: [{ code: "no_product_proof", category: "task_results", message: "Нет независимого подтверждения работы продукта" }],
    coverage: { sources_total: 3, sources_current: 2, sources_pending: 1, sources_failed: 0, sources_skipped: 0, complete: false, claims_current: 1, claims_conflicting: 0, claims_stale: 0, chars_processed: 100, chars_total: 200, reasons: ["pending"], method: "grounded_literal_v1" }, total: 1, offset: 0, limit: 20, has_more: false, next_offset: null,
};

async function sourcesApi(page: Page) {
    const connections = [connection(1, "Frontend", "main"), connection(2, "Auth", "release"), connection(3, "Gateway", "stable")];
    let patches = 0;
    await page.route("**/api/v1/projects/1/**", async route => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname.replace("/api/v1/projects/1", "");
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4191", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET,POST,PATCH,DELETE,PUT,OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, headers, json });
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (path === "/knowledge/map") return reply(knowledge);
        if (path === "/git/connections" && request.method() === "GET") return reply({ items: connections, total: 3, offset: 0, limit: 20, next_offset: null });
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

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 390, height: 844 }]) {
    test(`показывает происхождение знаний и версии трёх repo — ${viewport.name}`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        await installMockApi(page, mockState());
        await sourcesApi(page);
        await page.goto("/projects/DEMO");
        await expect(page.getByText("Участник сообщил о выполнении импорта", { exact: true })).toBeVisible();
        await expect(page.getByText("Отчёт участника", { exact: true })).toBeVisible();
        await page.getByText("Источники и версии (1)", { exact: true }).click();
        await expect(page.getByText("Импорт реализован", { exact: true })).toBeVisible();
        await page.screenshot({ path: info.outputPath(`knowledge-${viewport.name}.png`) });
        await page.goto("/projects/DEMO/settings");
        await page.locator("summary").getByText("Репозитории проекта", { exact: true }).click();
        await expect(page.getByRole("heading", { name: "Frontend", exact: true })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Auth", exact: true })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Gateway", exact: true })).toBeVisible();
        await page.getByLabel("Найти код и документацию", { exact: true }).fill("main");
        await page.getByRole("button", { name: "Найти в репозиториях" }).click();
        await page.getByRole("button", { name: "Frontend · src/main.py", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "src/main.py", exact: true });
        await expect(dialog.getByText(/def main\(\):/)).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
        await page.screenshot({ path: info.outputPath(`git-file-${viewport.name}.png`) });
    });
}

test("конфликт ref сохраняет draft до явной загрузки новой версии", async ({ page }) => {
    await installMockApi(page, mockState());
    await sourcesApi(page);
    await page.goto("/projects/DEMO/settings");
    await page.locator("summary").getByText("Репозитории проекта", { exact: true }).click();
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
