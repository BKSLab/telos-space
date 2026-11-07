import { expect, test, type Page } from "@playwright/test";
import type { FileSearchItem, TaskAttachment, TaskComment } from "../src/lib/types";
import { installProjectFilesApi, projectFile } from "./support/projectFilesMockApi";
import { installMockApi, mockState, stamp } from "./support/mockApi";

for (const width of [1440, 390]) {
    test(`общие карточки, превью и поиск по источникам ${width}px`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 900 });
        const state = await installProjectFilesApi(page, [projectFile({ original_name: "Схема.png", content_type: "image/png", caption: "Согласованный вариант", previewable: true, preview_url: "/api/v1/projects/1/files/5/preview" })]);
        const sources: FileSearchItem[] = [
            { source_type: "task_attachment", source_id: 9, project_id: 1, task_id: 100, task_key: "DEMO-1", task_title: "Согласовать макеты личного кабинета", document_slug: null, title: "Договор.docx", caption: "Условия пилота", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", preview_url: null, excerpt: "Согласован __FTS_START__пилот__FTS_END__ на ноябрь.", rank: 2 },
            { source_type: "project_file", source_id: 5, project_id: 1, task_id: null, task_key: null, task_title: null, document_slug: null, title: "Схема.png", caption: "Согласованный вариант", content_type: "image/png", preview_url: "/api/v1/projects/1/files/5/preview", excerpt: "Архитектура пилота", rank: 1 },
        ];
        state.searchResults = sources;
        await page.goto("/projects/DEMO/docs");
        await expect(page.getByText("Согласованный вариант", { exact: true })).toBeVisible();
        const preview = page.getByRole("button", { name: "Открыть изображение Схема.png" });
        await expect.poll(() => preview.locator("img").evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
        await preview.click();
        const modal = page.getByRole("dialog", { name: "Схема.png", exact: true });
        await expect(modal.getByRole("link", { name: "Скачать", exact: true })).toHaveAttribute("href", /\/files\/5\/content$/);
        await page.keyboard.press("Escape");
        await expect(modal).toHaveCount(0);
        await expect(preview).toBeFocused();
        await page.getByLabel("Поиск документов и файлов проекта").fill("пилот");
        const results = page.getByRole("list", { name: "Результаты поиска файлов" });
        await expect(results.getByRole("listitem")).toHaveCount(2);
        await expect(results.getByText("DOCX", { exact: true })).toBeVisible();
        await expect(results.getByRole("button", { name: /DEMO-1 · Согласовать макеты/ })).toBeVisible();
        await expect(results.locator("mark")).toHaveText("пилот");
        await page.getByLabel("Источники поиска").selectOption("task_files");
        await expect(results.getByRole("listitem")).toHaveCount(1);
        await page.getByLabel("Задача — источник файла").selectOption("100");
        await expect.poll(() => state.requests.at(-1)?.url.searchParams.get("task_id")).toBe("100");
        await page.screenshot({ path: testInfo.outputPath(`unified-files-search-${width}.png`) });
        await page.getByLabel("Поиск документов и файлов проекта").fill("");
        await page.getByLabel("Задача — источник файла").selectOption("");
        await page.getByLabel("Источники поиска").selectOption("all");
        await expect(page.getByRole("button", { name: "Открыть файл Схема.png" })).toBeVisible();
        await expect(page.getByRole("link", { name: "Скачать файл Договор.docx" })).toHaveCount(0);
        expect(state.files).toHaveLength(1);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(state.errors).toEqual([]);
    });

    test(`комментарий с файлом: ошибка, повтор, общие вложения и удаление ${width}px`, async ({ page }, testInfo) => {
        test.setTimeout(60_000);
        await page.setViewportSize({ width, height: 900 });
        const state = await taskFilesApi(page);
        await page.goto("/projects/DEMO/tasks");
        await page.getByRole("button", { name: "DEMO-1", exact: true }).click();
        const task = page.getByRole("dialog", { name: "Задача DEMO-1", exact: true });
        await task.getByRole("tab", { name: /^Комментарии/ }).click();
        const commentText = width === 390 ? "" : "Итоги встречи";
        await task.getByLabel("Текст комментария").fill(commentText);
        await task.getByLabel("Файлы к комментарию", { exact: true }).setInputFiles({ name: "Бюджет.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("Таблица") });
        await task.getByLabel("Подпись к Бюджет.xlsx").fill("Утверждённые расходы");
        await task.getByRole("button", { name: "Отправить", exact: true }).click();
        await expect(task.getByText(/Связь прервалась/)).toBeVisible();
        await expect(task.getByLabel("Текст комментария")).toHaveValue(commentText);
        await expect(task.getByLabel("Подпись к Бюджет.xlsx")).toHaveValue("Утверждённые расходы");
        await page.screenshot({ path: testInfo.outputPath(`unified-files-draft-${width}.png`) });
        await task.getByRole("button", { name: "Отправить", exact: true }).click();
        await expect(task.locator("article").getByText("Утверждённые расходы", { exact: true })).toBeVisible();
        expect(state.submissions).toHaveLength(2);
        const requestId = (body: string) => /name="request_id"\r?\n\r?\n([^\r\n]+)/.exec(body)?.[1];
        expect(requestId(state.submissions[0])).toBeTruthy();
        expect(requestId(state.submissions[1])).toBe(requestId(state.submissions[0]));
        expect(state.submissions[0]).toContain('name="captions"');
        expect(state.submissions[0]).not.toContain('name="author');
        await task.getByRole("tab", { name: /^Файлы/ }).click();
        const files = task.getByRole("tabpanel", { name: /^Файлы/ });
        await expect(files.getByText("Утверждённые расходы", { exact: true })).toBeVisible();
        await files.getByLabel("Поиск в файлах задачи").fill("расходы");
        await expect(files.getByRole("list", { name: "Результаты поиска файлов" }).getByText("Бюджет.xlsx")).toBeVisible();
        await files.getByLabel("Поиск в файлах задачи").fill("");
        await task.getByRole("tab", { name: /^Комментарии/ }).click();
        await page.screenshot({ path: testInfo.outputPath(`unified-files-comment-${width}.png`) });
        await task.locator("article").getByRole("button", { name: "Удалить", exact: true }).click();
        await expect(task.locator("article")).toHaveCount(0);
        expect(state.attachments).toHaveLength(1);
        await task.getByRole("tab", { name: /^Файлы/ }).click();
        await expect(files.getByText("Бюджет.xlsx", { exact: true })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(state.errors).toEqual([]);
    });
}

test("ошибка обработки файла задачи исправляется повтором без новой загрузки при выключенных знаниях", async ({ page }) => {
    await page.clock.install();
    const state = await taskFilesApi(page);
    state.attachments = [{ id: 9, task_id: 100, original_name: "Договор.pdf", content_type: "application/pdf", size: 100, created_at: stamp,
        content_url: "/api/v1/tasks/100/attachments/9/content", previewable: false, extraction_status: "failed", fts_status: "failed", index_status: "disabled" }];
    await page.goto("/projects/DEMO/tasks");
    await page.getByRole("button", { name: "DEMO-1", exact: true }).click();
    const task = page.getByRole("dialog", { name: "Задача DEMO-1", exact: true });
    await task.getByRole("tab", { name: /^Файлы/ }).click();
    const files = task.getByRole("tabpanel", { name: /^Файлы/ });
    await expect(files.getByText("Ошибка извлечения", { exact: true })).toBeVisible();
    await files.getByRole("button", { name: "Повторить обработку Договор.pdf" }).click();
    await expect(files.getByText("Извлекаем текст", { exact: true })).toBeVisible();
    expect(state.retries).toBe(1);
    expect(state.submissions).toEqual([]);
    Object.assign(state.attachments[0], { extraction_status: "ready", fts_status: "ready" });
    await page.clock.fastForward(2100);
    await expect(files.getByText("Текст доступен для поиска", { exact: true })).toBeVisible();
    const reads = state.attachmentReads;
    await page.clock.fastForward(10_000);
    expect(state.attachmentReads).toBe(reads);
    expect(state.errors).toEqual([]);
});

async function taskFilesApi(page: Page) {
    const base = mockState();
    const state = { attachments: [] as TaskAttachment[], comments: [] as TaskComment[], submissions: [] as string[], retries: 0, attachmentReads: 0, errors: base.errors };
    await installMockApi(page, base);
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const url = new URL(request.url());
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, headers, ...(status === 204 ? {} : { json }) });
        if (request.method() === "OPTIONS") return route.fallback();
        if (url.pathname === "/api/v1/tasks/100/attachments") { state.attachmentReads += 1; return reply(state.attachments); }
        if (url.pathname === "/api/v1/tasks/100/attachments/9/retry") { state.retries += 1; Object.assign(state.attachments[0], { extraction_status: "processing", fts_status: "pending" }); return reply(state.attachments[0], 202); }
        if (url.pathname === "/api/v1/tasks/100/comments") return reply(state.comments);
        if (url.pathname === "/api/v1/tasks/100/comments/with-files") {
            const body = request.postData() ?? "";
            state.submissions.push(body);
            if (state.submissions.length === 1) return reply({ detail: "Связь прервалась" }, 503);
            const file: TaskAttachment = { id: 9, task_id: 100, original_name: "Бюджет.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", size: 100, created_at: stamp,
                content_url: "/api/v1/tasks/100/attachments/9/content", caption: "Утверждённые расходы", previewable: false, preview_url: null, uploaded_by_user_id: 1 };
            state.attachments = [file];
            const bodyMd = /name="body_md"\r?\n\r?\n([^\r\n]*)/.exec(body)?.[1] ?? "";
            const comment: TaskComment = { id: 11, task_id: 100, author: base.me, author_name: null, body_md: bodyMd, attachments: [file], created_at: stamp };
            state.comments = [comment];
            return reply(comment, 201);
        }
        if (url.pathname === "/api/v1/comments/11" && request.method() === "DELETE") { state.comments = []; return reply(null, 204); }
        if (url.pathname === "/api/v1/tasks/100/files/search") return reply({ items: state.attachments.map(file => ({ source_type: "task_attachment", source_id: file.id, project_id: 1, task_id: 100, task_key: "DEMO-1", task_title: base.tasks[0].title, document_slug: null,
            title: file.original_name, caption: file.caption, content_type: file.content_type, preview_url: null, excerpt: "Расходы на пилот", rank: 1 })), total: state.attachments.length, limit: 20, offset: 0 });
        return route.fallback();
    });
    return state;
}
