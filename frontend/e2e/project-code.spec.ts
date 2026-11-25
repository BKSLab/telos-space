import { expect, test, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";
import type { GitConnection, GitGraph, GitSnapshotInfo, GitSymbol } from "../src/lib/projectKnowledge";

const sha = "a".repeat(40), older = "b".repeat(40);
const coverage = { total_files: 1553, cataloged_files: 1553, indexed_files: 1529, metadata_files: 24, failed_files: 0, symbols: 100, relations: 500, unsupported_files: 2, analyzed_files: 1527, reasons: ["unsupported_syntax"] };
function connection(id: number): GitConnection { return { id, project_id: 1, provider: "github", repository_id: 100 + id, owner: "example", repo: `repo${id}`, name: `Repo ${id}`, purpose: "", ref: id === 2 ? "release" : "heads/main", enabled: true, is_private: true, share_private_with_project: true, has_credential: true, has_webhook_secret: false, revision: 1, generation: 1, credential_revision: 1, state: "ready", observed_sha: sha, indexed_sha: sha, observed_at: null, indexed_at: null, checked_at: null, last_error_code: null, coverage: { ...coverage, metadata_counts: { binary_file: 24 }, truncated: false } }; }
function symbol(id: number): GitSymbol { return { id, file_id: 1, path: "src/auth.py", name: `check_${id}`, qualified_name: `auth.check_${id}`, kind: "function", parent_id: null, signature: `def check_${id}(user):`, line_start: id + 10, line_end: id + 12, offset: id * 10, end_offset: id * 10 + 40 }; }
const graph: GitGraph = { connection_id: 1, commit_sha: sha, analyzer_version: "test-v1", nodes: Array.from({ length: 100 }, (_, index) => symbol(index + 1)), edges: Array.from({ length: 500 }, (_, index) => ({ id: index + 1, source_id: index % 90 + 1, target_id: (index + 1) % 90 + 1, kind: "calls", confidence: index % 2 ? "resolved" : "candidate", label: "check", path: "src/auth.py", line_start: index + 10, line_end: index + 10 })), truncated: true, coverage };

async function workspaceApi(page: Page, count = 3) {
    await installMockApi(page, mockState());
    const reads: string[] = [];
    await page.route("**/api/v1/projects/1/git/**", async route => {
        const url = new URL(route.request().url()); const path = url.pathname; reads.push(url.pathname + url.search);
        const headers = { "access-control-allow-origin": route.request().headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type" };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        const reply = (json: unknown, status = 200) => route.fulfill({ json, headers, status });
        const id = Number(path.match(/connections\/(\d+)/)?.[1] ?? 1);
        if (path.endsWith("/connections")) { const offset = Number(url.searchParams.get("offset")); const size = url.searchParams.has("verified") ? 10 : 20; return reply({ items: Array.from({ length: Math.min(size, Math.max(0, count - offset)) }, (_, index) => connection(offset + index + 1)), offset, limit: size, total: offset + size >= count ? count : null, next_offset: offset + size < count ? offset + size : null }); }
        if (/connections\/\d+$/.test(path)) return reply(connection(id));
        if (path.endsWith("/snapshots")) return reply({ items: [sha, older].map(commit_sha => ({ connection_id: id, commit_sha, complete: false, analysis_state: "published", analyzer_version: "test-v1", coverage } satisfies GitSnapshotInfo)), next_offset: null });
        if (path.endsWith("/tree")) { const directory = url.searchParams.get("prefix"); return reply({ connection_id: id, repository_id: 100 + id, commit_sha: sha, items: directory ? [{ path: "src/auth.py", sha, type: "blob", mode: "100644", size: 150000, index_state: "ready", index_reason: null }] : [{ path: "src", sha: null, type: "tree", mode: "040000", size: null, index_state: "metadata", index_reason: null }, { path: "cover.png", sha, type: "blob", mode: "100644", size: 2000, index_state: "metadata", index_reason: "binary_file" }], offset: 0, limit: 40, next_offset: null, total_files: 1553, truncated: false, stale: false }); }
        if (path.endsWith("/file")) { const start = Number(url.searchParams.get("line_start") ?? 40); return reply({ connection_id: id, repository_id: 100 + id, file_id: 1, path: "src/auth.py", commit_sha: url.searchParams.get("sha"), text: `# repo${id}: Привет\r\ndef check_1(user):\n    return user is not None\n# <script>window.sourceExecuted=true</script>`, source_url: `https://github.com/example/repo${id}/blob/${sha}/src/auth.py`, line_start: start, line_end: start + 3, offset: Number(url.searchParams.get("offset") ?? 0), next_offset: url.searchParams.has("offset") ? null : 8000, total_chars: 16000, stale: false, partial: false }); }
        if (path.endsWith("/symbols")) return reply({ ...graph, items: [symbol(1), symbol(2)], next_offset: null });
        if (path.endsWith("/graph")) return reply(graph);
        if (path.endsWith("/impact")) return reply({ ...graph, nodes: graph.nodes.slice(0, 3), edges: graph.edges.slice(0, 3), task_links: [{ task_id: 100, change_id: 1, kind: "commit", provider_id: older, path: "src/auth.py" }] });
        if (path.endsWith("/search")) return reply({ items: [1, 2, 3].map(connection_id => ({ connection_id, repository_id: 100 + connection_id, commit_sha: sha, file_id: 1, path: "src/auth.py", title: "check_1", excerpt: "def check_1(user):", source_url: "", line_start: 11, line_end: 13, symbol_id: 1, qualified_name: "auth.check_1", kind: "function", offset: 10, stale: false, partial: false })), partial: true });
        if (path.endsWith("/compare")) return reply({ base_sha: older, head_sha: sha, files_complete: false, commits_complete: true, incomplete_reasons: ["pagination"], files: [{ filename: "src/auth.py", previous_filename: "auth.py", status: "renamed", patch: "@@ -1 +1 @@\n-before\n+after", patch_truncated: true }] });
        return reply({ detail: "Fixture endpoint absent" }, 404);
    });
    return reads;
}

for (const width of [320, 390, 1280, 1440]) {
    test(`files, three layouts and accessible evidence at ${width}px`, async ({ page }, info) => {
        test.setTimeout(60000);
        await page.setViewportSize({ width, height: 1000 }); const reads = await workspaceApi(page);
        const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
        await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&path=src%2Fauth.py&line=11&end=13`);
        await expect(page.getByLabel("Код src/auth.py")).toContainText("repo1: Привет");
        expect(reads.some(path => path.includes("children=true"))).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`files-${width}.png`), animations: "disabled" });
        for (const label of ["Иерархия", "Слои", "Радиальная"]) {
            await page.getByRole("button", { name: label, exact: true }).click();
            await expect(page.getByRole("button", { name: "Табличная карта" })).toBeVisible();
            await expect(page.locator(".react-flow__node").first()).toBeVisible();
            expect(await page.locator(".react-flow__node").count()).toBeLessThanOrEqual(100);
            await expect(page.getByText(/не более 100 узлов и 500 связей/)).toBeVisible();
            await page.getByRole("button", { name: "Табличная карта" }).click();
            await expect(page.getByRole("table")).toContainText("candidate");
            await page.getByRole("button", { name: "auth.check_1", exact: true }).click();
            await expect(page).toHaveURL(/symbol=1/);
            if (width < 900) await page.getByRole("navigation", { name: "Панели кода" }).getByRole("button", { name: "Сведения" }).click();
            await expect(page.getByText("Явные связи с задачами", { exact: true })).toBeVisible();
            if (width < 900) await page.getByRole("navigation", { name: "Панели кода" }).getByRole("button", { name: "Карта" }).click();
            await page.getByRole("button", { name: "Графическая карта" }).click();
            await page.screenshot({ path: info.outputPath(`${label}-${width}.png`), animations: "disabled" });
        }
        await page.emulateMedia({ reducedMotion: "reduce", forcedColors: "active" });
        await page.getByRole("button", { name: "Табличная карта" }).click();
        await expect(page.getByRole("table")).toBeVisible();
        expect(await page.evaluate(() => (window as unknown as { sourceExecuted?: boolean }).sourceExecuted)).toBeUndefined();
        expect(errors).toEqual([]);
    });
}

test("more than twenty repositories, independent identical paths, URL Back and diff", async ({ page }) => {
    await workspaceApi(page, 23); await page.goto("/projects/DEMO/knowledge?view=code");
    await page.getByRole("navigation", { name: "Страницы репозиториев" }).getByRole("button", { name: "Дальше" }).click();
    await expect(page.getByRole("button", { name: "Открыть Repo 20" })).toBeVisible();
    await page.getByRole("navigation", { name: "Страницы репозиториев" }).getByRole("button", { name: "Дальше" }).click();
    await page.getByRole("button", { name: "Открыть Repo 23" }).click();
    await page.getByRole("treeitem", { name: "src", exact: true }).click(); await page.getByRole("treeitem", { name: /auth.py/ }).click();
    await expect(page.getByLabel("Код src/auth.py")).toContainText("repo23");
    await page.getByRole("button", { name: "Изменения", exact: true }).click();
    await page.getByLabel("Базовая версия").selectOption(older);
    await expect(page.getByText("Прежний путь: auth.py", { exact: true })).toBeVisible();
    await expect(page.getByText("Patch обрезан.", { exact: true })).toBeVisible();
    await page.goBack(); await expect(page.getByLabel("Код src/auth.py")).toContainText("repo23");
    await expect(page).toHaveURL(/connection=23/);
});

test("Monaco and its workers are local and lazy, with plain source always available", async ({ page }) => {
    await workspaceApi(page); const urls: string[] = []; const workers: string[] = []; const errors: string[] = [];
    page.on("request", request => urls.push(request.url())); page.on("worker", worker => workers.push(worker.url())); page.on("pageerror", error => errors.push(error.message));
    await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&path=src%2Fauth.py`);
    await expect(page.getByLabel("Код src/auth.py")).toBeVisible(); expect(urls.some(url => url.includes("editor.api") || url.includes("MonacoSource"))).toBe(false);
    await page.getByRole("button", { name: "Подсветка кода" }).click();
    await expect(page.locator(".monaco-editor").first()).toBeVisible();
    // Read-only syntax highlighting does not itself require a language service worker.
    await page.evaluate(() => {
        const worker = (window as unknown as { MonacoEnvironment: { getWorker: () => Worker } }).MonacoEnvironment.getWorker();
        setTimeout(() => worker.terminate(), 1000);
    });
    await expect.poll(() => workers.length).toBeGreaterThan(0);
    expect(workers.every(url => url.startsWith("http://127.0.0.1:4179/") || url.startsWith("blob:http://127.0.0.1:4179/"))).toBe(true);
    await page.getByRole("button", { name: "Обычный текст" }).click(); await expect(page.getByLabel("Код src/auth.py")).toBeVisible(); expect(errors).toEqual([]);
});

test("settings request only connection management, while all-repository roots expand separately", async ({ page }) => {
    const reads = await workspaceApi(page);
    await page.goto("/projects/DEMO/settings");
    await page.locator("summary").getByText("Управлять репозиториями", { exact: true }).click();
    await expect(page.getByRole("link", { name: "Исследовать код" }).first()).toBeVisible();
    expect(reads.every(path => !/\/(graph|map|tree|search|file)(\?|$)/.test(path))).toBe(true);
    await page.goto("/projects/DEMO/knowledge?view=code&code_view=radial");
    await expect(page.locator(".react-flow__node")).toHaveCount(3);
    await page.locator(".react-flow__node").filter({ hasText: "Repo 2" }).click();
    await expect(page).toHaveURL(/connection=2/);
    await expect(page.getByLabel("Версия репозитория")).toHaveValue(sha);
});
