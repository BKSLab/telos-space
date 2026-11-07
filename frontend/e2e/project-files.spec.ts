import { expect, test, type Page } from "@playwright/test";
import { installProjectFilesApi, projectFile } from "./support/projectFilesMockApi";

const row = (page: Page, name = "plan.pdf") => page.getByRole("listitem").filter({ has: page.getByRole("button", { name: `Открыть файл ${name}`, exact: true }) });

test("загрузка → извлечение → знания; Markdown-документы доступны", async ({ page }, testInfo) => {
    const state = await installProjectFilesApi(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/projects/1/files", async (route) => {
        if (route.request().method() === "POST") await gate;
        await route.fallback();
    });
    await page.goto("/projects/DEMO/docs");
    await expect(page.getByText("Файлов пока нет", { exact: true })).toBeVisible();
    await page.getByLabel("Загрузить файл", { exact: true }).setInputFiles({ name: "plan.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF plan") });
    await page.getByLabel("Подпись к plan.pdf").fill("План запуска");
    await page.getByRole("dialog", { name: "Загрузить файлы" }).getByRole("button", { name: "Загрузить", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Загрузить файлы" }).getByRole("button", { name: "Загрузка…", exact: true })).toBeDisabled();
    release();
    await expect(row(page).getByText("Готовим текст", { exact: true })).toBeVisible();
    Object.assign(state.files[0], { extraction_status: "processing" });
    await expect(row(page).getByText("Извлекаем текст", { exact: true })).toBeVisible();
    Object.assign(state.files[0], { extraction_status: "ready", index_status: "processing" });
    await expect(row(page).getByText("Добавляем в знания", { exact: true })).toBeVisible();
    Object.assign(state.files[0], { index_status: "ready" });
    await expect(row(page).getByText("В знаниях Ариса", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: /Решения проекта/ })).toHaveAttribute("href", "/projects/DEMO/docs/decisions");
    const upload = state.requests.find((request) => request.method === "POST");
    expect(upload?.contentType).toContain("multipart/form-data; boundary=");
    expect(upload?.body).toContain('name="file"; filename="plan.pdf"');
    expect(upload?.body).toContain("%PDF plan");
    expect(upload?.body).toContain('name="caption"');
    expect(upload?.body).toContain("План запуска");
    await page.screenshot({ path: testInfo.outputPath("project-files-desktop.png") });
    expect(state.errors).toEqual([]);
});

test("частичная ошибка загрузки сохраняет успешный файл", async ({ page }) => {
    const state = await installProjectFilesApi(page);
    state.rejectedUploads.add("error.pdf");
    await page.goto("/projects/DEMO/docs");
    await page.getByLabel("Загрузить файл", { exact: true }).setInputFiles([
        { name: "plan.pdf", mimeType: "application/pdf", buffer: Buffer.from("plan") },
        { name: "error.pdf", mimeType: "application/pdf", buffer: Buffer.from("error") },
    ]);
    const upload = page.getByRole("dialog", { name: "Загрузить файлы" });
    await upload.getByRole("button", { name: "Загрузить", exact: true }).click();
    await expect(upload.getByText(/«error.pdf»: Хранилище временно недоступно/)).toBeVisible();
    await upload.getByRole("button", { name: "Отмена", exact: true }).click();
    await expect(upload).toHaveCount(0);
    await expect(page.getByText("Загружено файлов: 1", { exact: true })).toBeVisible();
    await expect(page.getByText(/«error.pdf»: Хранилище временно недоступно/)).toBeVisible();
    await expect(row(page)).toBeVisible();
    await expect(row(page, "error.pdf")).toHaveCount(0);
    expect(state.errors).toEqual([]);
});

test("ошибка извлечения и индексации; явный повтор обработки с ошибкой и восстановлением", async ({ page }) => {
    const state = await installProjectFilesApi(page, [projectFile({ extraction_status: "failed", extraction_detail: "Повреждённый PDF", index_status: "failed", index_detail: "Текст недоступен" })]);
    await page.goto("/projects/DEMO/docs");
    await expect(row(page).getByText("Ошибка извлечения", { exact: true })).toBeVisible();
    await expect(row(page).getByText("Не удалось добавить в знания", { exact: true })).toBeVisible();
    state.retryError = true;
    await row(page).getByRole("button", { name: "Повторить обработку plan.pdf" }).click();
    await expect(page.getByText("Обработка временно недоступна", { exact: true })).toBeVisible();
    state.retryError = false;
    await row(page).getByRole("button", { name: "Повторить обработку plan.pdf" }).click();
    await expect(row(page).getByText("Готовим текст", { exact: true })).toBeVisible();
    await expect(row(page).getByRole("button", { name: "Повторить обработку plan.pdf" })).toBeDisabled();
    Object.assign(state.files[0], { extraction_status: "ready", index_status: "ready" });
    await expect(row(page).getByText("В знаниях Ариса", { exact: true })).toBeVisible();
    expect(state.requests.filter((request) => request.url.pathname.endsWith("/retry"))).toHaveLength(2);
    expect(state.errors).toEqual([]);
});

test("прямая ссылка, страницы извлечённого текста, скачивание оригинала и закрытие", async ({ page }) => {
    const state = await installProjectFilesApi(page, [projectFile()]);
    await page.goto("/projects/DEMO/docs?file=5&view=files");
    const dialog = page.getByRole("dialog", { name: "plan.pdf", exact: true });
    await expect(dialog.getByLabel("Извлечённый текст", { exact: true })).toContainText("Первый фрагмент");
    await dialog.getByRole("button", { name: "Далее", exact: true }).click();
    await expect(dialog.getByLabel("Извлечённый текст", { exact: true })).toContainText("Второй фрагмент");
    await expect(dialog.getByRole("button", { name: "Далее", exact: true })).toBeDisabled();
    await dialog.getByRole("button", { name: "Назад", exact: true }).click();
    await expect(dialog.getByLabel("Извлечённый текст", { exact: true })).toContainText("Первый фрагмент");
    expect(state.requests.some((request) => request.url.searchParams.get("offset") === "8000" && request.url.searchParams.get("max_chars") === "8000")).toBe(true);
    const download = page.waitForEvent("download");
    await dialog.getByRole("link", { name: "Скачать оригинал" }).click();
    expect((await download).suggestedFilename()).toBe(state.files[0].original_name);
    await dialog.getByRole("button", { name: "Закрыть", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(new URL(page.url()).searchParams.has("file")).toBe(false);
    expect(new URL(page.url()).searchParams.get("view")).toBe("files");
    expect(state.errors).toEqual([]);
});

test("удаление требует подтверждения; ошибка сохраняет диалог и файл до повтора", async ({ page }) => {
    const state = await installProjectFilesApi(page, [projectFile()]);
    await page.goto("/projects/DEMO/docs?file=5");
    const fileDialog = page.getByRole("dialog", { name: "plan.pdf", exact: true });
    await fileDialog.getByRole("button", { name: "Удалить", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "Удалить файл проекта?" });
    await expect(confirmation.getByText(/будет удалён вместе с извлечённым текстом/)).toBeVisible();
    await confirmation.getByRole("button", { name: "Отмена", exact: true }).click();
    expect(state.requests.filter((request) => request.method === "DELETE")).toHaveLength(0);
    await fileDialog.getByRole("button", { name: "Удалить", exact: true }).click();
    state.deleteError = true;
    await confirmation.getByRole("button", { name: "Удалить файл", exact: true }).click();
    await expect(confirmation.getByText("Не удалось удалить оригинал", { exact: true })).toBeVisible();
    expect(state.files).toHaveLength(1);
    state.deleteError = false;
    await confirmation.getByRole("button", { name: "Удалить файл", exact: true }).click();
    await expect(confirmation).toHaveCount(0);
    await expect(fileDialog).toHaveCount(0);
    await expect(page.getByText("Файлов пока нет", { exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.has("file")).toBe(false);
    expect(state.files).toEqual([]);
    expect(state.errors).toEqual([]);
});

test("ошибка polling останавливает запросы; явный повтор восстанавливает список", async ({ page }) => {
    await page.clock.install();
    const state = await installProjectFilesApi(page, [projectFile({ extraction_status: "processing", index_status: "pending" })]);
    await page.goto("/projects/DEMO/docs");
    await expect(row(page).getByText("Извлекаем текст", { exact: true })).toBeVisible();
    state.listError = true;
    await page.clock.fastForward(2100);
    await expect(page.getByText("Ошибка чтения файлов", { exact: true })).toBeVisible();
    const requestCount = state.requests.length;
    await page.clock.fastForward(12_000);
    expect(state.requests).toHaveLength(requestCount);
    await expect(row(page)).toBeVisible();
    state.listError = false;
    Object.assign(state.files[0], { extraction_status: "ready", index_status: "ready" });
    await page.getByRole("button", { name: "Повторить загрузку файлов" }).click();
    await expect(row(page).getByText("В знаниях Ариса", { exact: true })).toBeVisible();
    const readyCount = state.requests.length;
    await page.clock.fastForward(12_000);
    expect(state.requests).toHaveLength(readyCount);
    expect(state.errors).toEqual([]);
});

test("список загружает следующую сотню и ищет файлы на сервере", async ({ page }) => {
    const state = await installProjectFilesApi(page, Array.from({ length: 101 }, (_, index) => projectFile({ id: index + 1, original_name: `file-${index + 1}.txt` })));
    await page.goto("/projects/DEMO/docs");
    await expect(row(page, "file-100.txt")).toHaveCount(1);
    await expect(row(page, "file-101.txt")).toHaveCount(0);
    await page.getByRole("button", { name: "Показать ещё файлы", exact: true }).click();
    await expect(row(page, "file-101.txt")).toBeVisible();
    expect(state.requests.some((request) => request.url.searchParams.get("offset") === "100")).toBe(true);
    await page.getByLabel("Поиск документов и файлов проекта").fill("file-101");
    await expect(page.getByRole("list", { name: "Результаты поиска файлов" }).getByRole("listitem")).toHaveCount(1);
    await expect(page.getByRole("link", { name: "Скачать файл file-101.txt" })).toBeVisible();
    expect(state.requests.at(-1)?.url.searchParams.get("q")).toBe("file-101");
    expect(state.errors).toEqual([]);
});

test("ошибка чтения текста и явный повтор; отсутствие извлечения сохраняет оригинал", async ({ page }) => {
    const state = await installProjectFilesApi(page, [projectFile({ extraction_status: "unsupported", extraction_detail: "Извлечение текста для этого файла недоступно", index_status: "disabled" })]);
    state.detailError = true;
    await page.goto("/projects/DEMO/docs?file=5");
    await expect(page.getByText("Ошибка чтения текста", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Скачать оригинал", exact: true })).toBeVisible();
    state.detailError = false;
    await page.getByRole("button", { name: "Повторить загрузку текста", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "plan.pdf", exact: true });
    await expect(dialog.getByText("Извлечение недоступно", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Извлечение текста для этого файла недоступно", { exact: true })).toBeVisible();
    await expect(dialog.getByText(/Извлечённого текста нет/)).toBeVisible();
    await expect(dialog.getByRole("link", { name: "Скачать оригинал", exact: true })).toHaveAttribute("href", /\/projects\/1\/files\/5\/content$/);
    expect(state.errors).toEqual([]);
});

test("завершённое распознавание и выключенные знания не запускают polling", async ({ page }) => {
    await page.clock.install();
    const state = await installProjectFilesApi(page, [projectFile({ extraction_status: "disabled", fts_status: "ready", index_status: "disabled" })]);
    await page.goto("/projects/DEMO/docs?file=5");
    const dialog = page.getByRole("dialog", { name: "plan.pdf", exact: true });
    await expect(dialog.getByText("Распознавание отключено", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Распознавание изображения отключено. Оригинал, название и подпись доступны.", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("link", { name: "Скачать оригинал" })).toBeVisible();
    const requestCount = state.requests.length;
    await page.clock.fastForward(12_000);
    expect(state.requests).toHaveLength(requestCount);
    expect(state.errors).toEqual([]);
});

test("локальный текст и поиск обновляются при выключенных знаниях", async ({ page }) => {
    await page.clock.install();
    const state = await installProjectFilesApi(page, [projectFile({ extraction_status: "processing", fts_status: "pending", index_status: "disabled" })]);
    await page.goto("/projects/DEMO/docs");
    await expect(row(page).getByText("Извлекаем текст", { exact: true })).toBeVisible();
    Object.assign(state.files[0], { extraction_status: "ready", fts_status: "ready" });
    await page.clock.fastForward(2100);
    await expect(row(page).getByText("В поиске", { exact: true })).toBeVisible();
    const reads = state.requests.length;
    await page.clock.fastForward(10_000);
    expect(state.requests).toHaveLength(reads);
    expect(state.errors).toEqual([]);
});

test("узкий экран: длинное имя, читаемый текст и доступные действия", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 760 });
    const name = "Очень-длинное-имя-файла-с-требованиями-и-решениями-проекта-без-пробелов.pdf";
    const state = await installProjectFilesApi(page, [projectFile({ original_name: name })]);
    await page.goto("/projects/DEMO/docs");
    await expect(page.getByRole("button", { name: /Загрузить файл/ })).toBeVisible();
    await row(page, name).getByRole("button", { name: `Открыть файл ${name}`, exact: true }).click();
    const dialog = page.getByRole("dialog", { name, exact: true });
    await expect(dialog.getByLabel("Извлечённый текст", { exact: true })).toContainText("Первый фрагмент");
    const bounds = await dialog.boundingBox();
    expect(bounds?.x).toBeGreaterThanOrEqual(0);
    expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(390);
    const download = dialog.getByRole("link", { name: "Скачать оригинал" });
    await expect(download).toBeVisible();
    await expect.poll(async () => (await download.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await expect(dialog.getByRole("button", { name: "Закрыть", exact: true })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Удалить", exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("project-files-mobile.png") });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(state.errors).toEqual([]);
});
