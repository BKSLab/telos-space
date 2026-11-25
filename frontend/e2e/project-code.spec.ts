import { expect, test, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";
import type { GitConnection, GitGraph, GitSnapshotInfo, GitSymbol } from "../src/lib/projectKnowledge";

const sha = "a".repeat(40), older = "b".repeat(40);
const coverage = { total_files: 1553, cataloged_files: 1553, indexed_files: 1529, metadata_files: 24, failed_files: 0, symbols: 100, relations: 500, unsupported_files: 2, analyzed_files: 1527, reasons: ["unsupported_syntax"] };
function connection(id: number): GitConnection { return { id, project_id: 1, provider: "github", repository_id: 100 + id, owner: "example", repo: `repo${id}`, name: `Repo ${id}`, purpose: "", ref: id === 2 ? "release" : "heads/main", enabled: true, is_private: true, share_private_with_project: true, has_credential: true, has_webhook_secret: false, revision: 1, generation: 1, credential_revision: 1, state: "ready", observed_sha: sha, indexed_sha: sha, observed_at: null, indexed_at: null, checked_at: null, last_error_code: null, coverage: { ...coverage, metadata_counts: { binary_file: 24 }, truncated: false } }; }
function symbol(id: number): GitSymbol { return { id, file_id: 1, path: "src/auth.py", name: `check_${id}`, qualified_name: `auth.check_${id}`, kind: "function", parent_id: null, signature: `def check_${id}(user):`, line_start: id + 10, line_end: id + 12, offset: id * 10, end_offset: id * 10 + 40 }; }
const graph: GitGraph = { connection_id: 1, commit_sha: sha, analyzer_version: "test-v1", nodes: Array.from({ length: 100 }, (_, index) => symbol(index + 1)), edges: Array.from({ length: 500 }, (_, index) => ({ id: index + 1, source_id: index % 90 + 1, target_id: (index + 1) % 90 + 1, kind: "calls", confidence: index % 2 ? "resolved" : "candidate", label: "check", path: "src/auth.py", line_start: index + 10, line_end: index + 10 })), truncated: true, coverage };

async function workspaceApi(page: Page, count = 3, graphData = graph, extraPaths: string[] = [], status: { historical?: boolean; truncated?: boolean; failedFiles?: number } = {}) {
    await installMockApi(page, mockState());
    const reads: string[] = [];
    const verifiedConnection = (id: number) => status.historical ? { ...connection(id), observed_sha: older, indexed_sha: older } : connection(id);
    await page.route("**/api/v1/projects/1/git/**", async route => {
        const url = new URL(route.request().url()); const path = url.pathname; reads.push(url.pathname + url.search);
        const headers = { "access-control-allow-origin": route.request().headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type" };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        const reply = (json: unknown, status = 200) => route.fulfill({ json, headers, status });
        const id = Number(path.match(/connections\/(\d+)/)?.[1] ?? 1);
        if (path.endsWith("/connections")) { const offset = Number(url.searchParams.get("offset")); const size = url.searchParams.has("verified") ? 10 : 20; return reply({ items: Array.from({ length: Math.min(size, Math.max(0, count - offset)) }, (_, index) => verifiedConnection(offset + index + 1)), offset, limit: size, total: offset + size >= count ? count : null, next_offset: offset + size < count ? offset + size : null }); }
        if (/connections\/\d+$/.test(path)) return reply(verifiedConnection(id));
        if (path.endsWith("/snapshots")) return reply({ items: [sha, older].map(commit_sha => ({ connection_id: id, commit_sha, complete: !status.truncated && !status.failedFiles, analysis_state: "published", analyzer_version: "test-v1", coverage: { ...coverage, failed_files: status.failedFiles ?? 0, reasons: status.truncated ? ["tree_limit"] : status.failedFiles ? ["file_errors"] : [] } } satisfies GitSnapshotInfo)), next_offset: null });
        if (path.endsWith("/tree")) {
            const directory = url.searchParams.get("prefix") ?? "";
            const paths = [...new Set([...graphData.nodes.map(node => node.path), ...extraPaths, "cover.png"])].filter(file => !directory || file.startsWith(`${directory}/`));
            const entries = new Map();
            for (const file of paths) {
                const relative = file.slice(directory ? directory.length + 1 : 0), first = relative.split("/")[0];
                const child = directory ? `${directory}/${first}` : first;
                const folder = relative.includes("/");
                entries.set(child, { path: child, sha: folder ? null : sha, type: folder ? "tree" : "blob", mode: folder ? "040000" : "100644", size: folder ? null : 150000, index_state: folder || file === "cover.png" ? "metadata" : "ready", index_reason: file === "cover.png" ? "binary_file" : null });
            }
            const offset = Number(url.searchParams.get("offset") ?? 0), items = [...entries.values()];
            return reply({ connection_id: id, repository_id: 100 + id, commit_sha: sha, items: items.slice(offset, offset + 40), offset, limit: 40, next_offset: offset + 40 < items.length ? offset + 40 : null, total_files: items.length, truncated: Boolean(status.truncated), stale: Boolean(status.historical) });
        }
        if (path.endsWith("/file")) { const start = Number(url.searchParams.get("line_start") ?? 40); return reply({ connection_id: id, repository_id: 100 + id, file_id: 1, path: url.searchParams.get("path"), commit_sha: url.searchParams.get("sha"), text: `# repo${id}: Привет\r\ndef check_1(user):\n    return user is not None\n# <script>window.sourceExecuted=true</script>`, source_url: `https://github.com/example/repo${id}/blob/${sha}/src/auth.py`, line_start: start, line_end: start + 3, offset: Number(url.searchParams.get("offset") ?? 0), next_offset: url.searchParams.has("offset") ? null : 8000, total_chars: 16000, stale: Boolean(status.historical && url.searchParams.get("sha") === sha), partial: Boolean(status.truncated || status.failedFiles) }); }
        if (path.endsWith("/symbols")) return reply({ ...graph, items: [symbol(1), symbol(2)], next_offset: null });
        if (path.endsWith("/graph")) return reply(graphData);
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
        await expect(page.locator(".monaco-editor .view-lines").first()).toContainText("repo1: Привет");
        await expect(page.getByRole("region", { name: "Каталог файлов" })).toHaveCount(0);
        await expect(page.getByRole("complementary", { name: "Сведения об источнике" })).toHaveCount(0);
        await expect(page.locator(".repository-source-language")).toHaveText("Python");
        expect(reads.some(path => path.includes("children=true"))).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`files-${width}.png`), animations: "disabled" });
        for (const label of ["Иерархия", "Слои", "Радиальная"]) {
            await page.getByRole("button", { name: label, exact: true }).click();
            await expect(page.getByRole("button", { name: "Табличная карта" })).toBeVisible();
            await expect(page.locator(".react-flow__node").first()).toBeVisible();
            expect(await page.locator(".react-flow__node").count()).toBeLessThanOrEqual(100);
            await expect(page.getByText(/100 узлов и 500 связей/)).toBeVisible();
            await page.getByRole("button", { name: "Табличная карта" }).click();
            await expect(page.getByRole("table")).toContainText("candidate");
            await page.getByRole("button", { name: "auth.check_1", exact: true }).click();
            await expect(page).toHaveURL(/symbol=1/);
            if (width < 900) await page.getByRole("navigation", { name: "Панели кода" }).getByRole("button", { name: "Сведения" }).click();
            await expect(page.getByText("Явные связи с задачами", { exact: true })).toBeVisible();
            if (width < 900) await page.getByRole("navigation", { name: "Панели кода" }).getByRole("button", { name: "Карта" }).click();
            await page.getByRole("button", { name: "Графическая карта" }).click();
            await expect.poll(async () => new Set(await page.locator(".react-flow__node").evaluateAll(nodes => nodes.map(node => (node as HTMLElement).style.transform))).size).toBeGreaterThan(1);
            await expect.poll(async () => (await page.locator(".repository-graph-card").first().boundingBox())?.width ?? 0).toBeGreaterThan(140);
            await page.screenshot({ path: info.outputPath(`${label}-${width}.png`), animations: "disabled" });
            if (width === 1440 && label === "Иерархия") {
                await page.getByLabel("Акценты карты").selectOption("changes");
                await expect(page.locator(".repository-changed-node").first()).toBeAttached();
                const epoch = Number(await page.locator(".repository-graph").getAttribute("data-layout-ready"));
                await page.getByRole("button", { name: "Сбросить", exact: true }).click();
                await expect.poll(async () => Number(await page.locator(".repository-graph").getAttribute("data-layout-ready"))).toBeGreaterThan(epoch);
                await expect(page.locator(".repository-changed-node").first()).toBeAttached();
                await page.getByLabel("Акценты карты").selectOption("structure");
            }
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
    await expect(page.locator(".monaco-editor .view-lines").first()).toContainText("repo23");
    await page.getByRole("button", { name: "Изменения", exact: true }).click();
    await page.getByLabel("Базовая версия").selectOption(older);
    await expect(page.getByText("Прежний путь: auth.py", { exact: true })).toBeVisible();
    await expect(page.getByText("Patch обрезан.", { exact: true })).toBeVisible();
    await page.goBack(); await expect(page.locator(".monaco-editor .view-lines").first()).toContainText("repo23");
    await expect(page).toHaveURL(/connection=23/);
});

test("Monaco and its workers are local and lazy, with plain source always available", async ({ page }) => {
    await workspaceApi(page); const urls: string[] = []; const workers: string[] = []; const errors: string[] = [];
    page.on("request", request => urls.push(request.url())); page.on("worker", worker => workers.push(worker.url())); page.on("pageerror", error => errors.push(error.message));
    await page.goto("/projects/DEMO/knowledge?view=code");
    await expect(page.getByRole("button", { name: "Открыть Repo 1", exact: true })).toBeVisible();
    expect(urls.some(url => url.includes("editor.api") || url.includes("MonacoSource"))).toBe(false);
    await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&path=src%2Fauth.py`);
    await expect(page.locator(".monaco-editor").first()).toBeVisible();
    expect(urls.some(url => url.includes("MonacoSource"))).toBe(true);
    await expect(page.locator(".monaco-editor .view-lines")).toContainText("def");
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


test("a branching hierarchy stays readable and folders progressively reveal the received slice", async ({ page }, info) => {
    const folders = ["backend/api", "backend/services", "frontend/components", "frontend/pages", "mcp", "docs", "tests", "scripts"];
    const nodes = folders.flatMap((folder, folderIndex) => Array.from({ length: 4 }, (_, fileIndex) => Array.from({ length: 3 }, (_, memberIndex) => ({ ...symbol(folderIndex * 12 + fileIndex * 3 + memberIndex + 1), path: `${folder}/module_${fileIndex}.py` }))).flat());
    await workspaceApi(page, 3, { ...graph, nodes });
    await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&code_view=hierarchy`);
    await expect(page.locator(".repository-graph-card-repository")).toContainText("7 объектов каталога");
    await expect.poll(async () => new Set(await page.locator(".react-flow__node").evaluateAll(elements => elements.map(node => (node as HTMLElement).style.transform))).size).toBeGreaterThan(1);
    await expect.poll(async () => (await page.locator(".repository-graph-card-repository").boundingBox())?.width ?? 0).toBeGreaterThan(140);
    await expect(page.locator(".repository-graph-card-repository")).toBeInViewport();
    await page.screenshot({ path: info.outputPath("hierarchy-branching.png"), animations: "disabled" });
    await page.getByRole("button", { name: "Вписать", exact: true }).click();
    const canvas = page.locator(".repository-graph-canvas");
    await expect.poll(async () => {
        const area = await canvas.boundingBox();
        const cards = await page.locator(".repository-graph-card").evaluateAll(elements => elements.map(element => { const box = element.getBoundingClientRect(); return { left: box.left, right: box.right, top: box.top, bottom: box.bottom }; }));
        return cards.every(card => card.left >= area!.x - 1 && card.right <= area!.x + area!.width + 1 && card.top >= area!.y - 1 && card.bottom <= area!.y + area!.height + 1);
    }).toBe(true);
    // Choose a genuine directory from the lazy tree to focus the same graph response.
    await page.getByRole("treeitem", { name: "backend", exact: true }).click();
    await page.getByRole("treeitem", { name: "services", exact: true }).click();
    await expect(page.locator(".repository-graph-scope")).toContainText("backend/services");
    await expect(page.locator(".repository-graph-card-file")).toHaveCount(4);
    await expect(page.locator(".repository-graph-card-file").first()).toContainText("3 символов");
    await page.getByRole("button", { name: "Раскрыть символы module_0.py" }).click();
    await expect(page.locator(".repository-graph-card-symbol")).toHaveCount(3);
    await page.screenshot({ path: info.outputPath("hierarchy-focused.png"), animations: "disabled" });
});


test("hierarchy traverses catalogue directories outside the current symbol slice and opens source", async ({ page }) => {
    const reads = await workspaceApi(page, 3, graph, ["other/api/ping.py"]);
    await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&code_view=hierarchy`);
    const other = page.locator(".repository-graph-card-directory").filter({ hasText: "other" });
    await expect(other).toBeVisible();
    await other.click();
    await expect(page).toHaveURL(/directory=other/);
    await page.locator(".repository-graph-card-directory").filter({ hasText: "api" }).click();
    await expect(page).toHaveURL(/directory=other%2Fapi/);
    const file = page.locator(".repository-graph-card-file").filter({ hasText: "ping.py" });
    await expect(file).toContainText("Символы вне полученного среза");
    await page.getByRole("button", { name: "Табличная карта" }).click();
    await expect(page.getByRole("table")).toContainText("other/api/ping.py");
    await page.getByRole("table").getByRole("button", { name: "ping.py", exact: true }).click();
    await expect(page).toHaveURL(/code_view=files/);
    await expect(page.locator(".repository-source-filename")).toHaveText("ping.py");
    await expect(page.locator(".monaco-editor").first()).toBeVisible();
    expect(reads.some(read => read.includes("/file?") && read.includes("path=other%2Fapi%2Fping.py"))).toBe(true);
});

test("selecting Monaco line numbers updates the permalink without jumping to a new fragment", async ({ page }) => {
    const reads = await workspaceApi(page);
    await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&path=src%2Fauth.py&line=11&end=13`);
    const lines = page.locator(".monaco-editor .line-numbers");
    await expect(lines).toHaveCount(3);
    const before = reads.filter(read => read.includes("/file?")).length;
    await lines.nth(1).click();
    await expect(page).toHaveURL(/line=12/);
    await lines.nth(2).click({ modifiers: ["Shift"] });
    await expect(page).toHaveURL(/end=13/);
    await expect(lines.first()).toHaveText("11");
    expect(reads.filter(read => read.includes("/file?")).length).toBe(before);
    // Multiple syntax token colors are present, not just a single plain text block.
    const colors = await page.locator(".monaco-editor .view-lines span").evaluateAll(elements => [...new Set(elements.filter(element => element.textContent?.trim()).map(element => getComputedStyle(element).color))]);
    expect(colors.length).toBeGreaterThan(2);
});


for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 720 }]) {
    test(`source fills its pane without a duplicate catalogue at ${viewport.width}px`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        await workspaceApi(page, 3, graph, Array.from({ length: 26 }, (_, index) => `src/file_${index}.py`));
        await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&directory=src&path=src%2Fauth.py&line=11&end=13`);
        const editor = page.locator(".repository-source-editor");
        await expect(page.locator(".monaco-editor").first()).toBeVisible();
        await expect(page.getByRole("table")).toHaveCount(0);
        await expect(page.locator(".repository-show-inspector")).toHaveCount(0);
        await editor.scrollIntoViewIfNeeded();
        await expect.poll(async () => (await page.locator(".repository-source-viewer").boundingBox())?.height ?? 0).toBeGreaterThan(150);
        await expect(editor).toBeInViewport();
        const more = page.getByRole("button", { name: "Читать дальше", exact: true });
        await more.scrollIntoViewIfNeeded();
        await expect(more).toBeInViewport();
        if (viewport.width >= 900) {
            const geometry = await page.getByRole("region", { name: "Область кода" }).evaluate(element => ({ height: element.clientHeight, scroll: element.scrollHeight, padding: parseFloat(getComputedStyle(element).paddingTop) + parseFloat(getComputedStyle(element).paddingBottom) }));
            if (geometry.height - geometry.padding >= 320) expect(geometry.scroll).toBeLessThanOrEqual(geometry.height + 1);
            const pane = await page.getByRole("region", { name: "Область кода" }).boundingBox(), code = await editor.boundingBox();
            expect(code!.height).toBeGreaterThan(pane!.height / 2);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`long-catalogue-${viewport.width}.png`), animations: "disabled" });
        await page.getByRole("button", { name: "Иерархия", exact: true }).click();
        const warning = page.getByText(/Область ограничена: до 100 узлов/);
        await expect(warning).toBeAttached();
        await page.getByRole("region", { name: "Область кода" }).evaluate(element => { element.scrollTop = element.scrollHeight; });
        await expect(warning).toBeInViewport();
        const graphElement = page.locator(".repository-graph");
        expect(await graphElement.evaluate(element => element.scrollTop)).toBe(0);
        const bounds = await graphElement.boundingBox(), note = await warning.boundingBox();
        expect(note!.y + note!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height + 1);
    });
}


test("historical complete source is labelled once and opens the latest SHA only on request", async ({ page }, info) => {
    const reads = await workspaceApi(page, 3, graph, [], { historical: true });
    await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&path=src%2Fauth.py&directory=src`);
    await expect(page.locator(".monaco-editor").first()).toBeVisible();
    await expect(page.getByText(/Открыта сохранённая версия/)).toHaveCount(1);
    await expect(page.getByText(/Каталог или версия неполны|Версия или охват неполны|Индекс этой версии охватывает не весь/)).toHaveCount(0);
    await expect(page.getByRole("table")).toHaveCount(0);
    await expect(page.getByRole("complementary", { name: "Сведения об источнике" })).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`sha=${sha}`));
    await page.screenshot({ path: info.outputPath("historical-two-pane.png"), animations: "disabled" });
    await page.getByRole("button", { name: "К актуальной версии" }).click();
    await expect(page).toHaveURL(new RegExp(`sha=${older}`));
    await expect(page.getByText(/Открыта сохранённая версия/)).toHaveCount(0);
    await expect.poll(() => reads.some(read => read.includes("/file?") && read.includes(`sha=${older}`))).toBe(true);
    await page.getByRole("button", { name: "Сведения", exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Сведения об источнике" })).toBeVisible();
});

test("an incomplete snapshot explains missing catalogue paths instead of blaming a version", async ({ page }) => {
    await workspaceApi(page, 3, graph, [], { truncated: true });
    await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&path=src%2Fauth.py&directory=src`);
    await expect(page.getByText("GitHub вернул неполный каталог этой версии. Некоторые пути могут отсутствовать.")).toBeVisible();
    await expect(page.getByText(/Каталог этой версии загружен не полностью/)).toHaveCount(1);
    await expect(page.getByText(/Версия или охват неполны|Каталог или версия неполны/)).toHaveCount(0);
});


test("path navigation and a direct link open a folder beyond the root's first page", async ({ page }) => {
    await workspaceApi(page, 3, graph, Array.from({ length: 41 }, (_, index) => `folder${index}/main.py`));
    await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}`);
    await expect(page.getByRole("treeitem", { name: "folder40", exact: true })).toHaveCount(0);
    await page.getByRole("textbox", { name: "Файл или папка", exact: true }).fill("folder40");
    await page.getByRole("button", { name: "Перейти", exact: true }).click();
    await page.getByRole("treeitem", { name: "main.py", exact: true }).click();
    await expect(page).toHaveURL(/path=folder40%2Fmain.py/);
    await expect(page.locator(".monaco-editor").first()).toBeVisible();
    await page.reload();
    await expect(page.getByRole("treeitem", { name: "main.py", exact: true })).toBeVisible();
    await expect(page.locator(".repository-source-filename")).toHaveText("main.py");
});

for (const viewport of [{ width: 900, height: 720 }, { width: 1280, height: 640 }]) {
    test(`source paging survives historical and failed-index notices at ${viewport.width}x${viewport.height}`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        await workspaceApi(page, 3, graph, [], { historical: true, failedFiles: 2 });
        await page.goto(`/projects/DEMO/knowledge?view=code&connection=1&sha=${sha}&path=src%2Fauth.py&directory=src`);
        await expect(page.locator(".monaco-editor").first()).toBeVisible();
        await expect(page.getByText(/Не удалось прочитать файлов: 2/)).toBeVisible();
        const pane = page.getByRole("region", { name: "Область кода" });
        await pane.evaluate(element => { element.scrollTop = element.scrollHeight; });
        const more = page.getByRole("button", { name: "Читать дальше", exact: true });
        await expect(more).toBeInViewport();
        expect(await page.locator(".repository-source-viewer").evaluate(element => element.scrollTop)).toBe(0);
        const viewer = await page.locator(".repository-source-viewer").boundingBox(), button = await more.boundingBox();
        expect(button!.y + button!.height).toBeLessThanOrEqual(viewer!.y + viewer!.height + 1);
        await page.screenshot({ path: info.outputPath(`short-window-${viewport.width}.png`), animations: "disabled" });
    });
}
