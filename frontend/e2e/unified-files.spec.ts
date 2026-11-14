import { expect, test } from "@playwright/test";
import { installDocumentsApi } from "./support/documentsMockApi";

for (const width of [1440, 390, 320]) {
    test(`документы: единый каталог, фильтры без q, клавиатура и геометрия ${width}px`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 900 });
        const state = await installDocumentsApi(page);
        await page.goto("/projects/DEMO/docs");
        const row = page.getByRole("button", { name: `Открыть документ ${state.documents[1].title}`, exact: true });
        await expect(row).toBeVisible();
        expect((await row.boundingBox())!.height).toBeLessThanOrEqual(86);
        await row.focus(); await page.keyboard.press("Enter");
        const dialog = page.getByRole("dialog", { name: "Документ", exact: true });
        await expect(dialog.getByText("Согласованный вариант", { exact: true })).toBeVisible();
        await expect.poll(() => dialog.getByRole("img").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
        await expect(dialog.getByRole("link", { name: "Скачать оригинал", exact: true })).toHaveAttribute("href", /\/documents\/opaque-image\/content$/);
        await dialog.getByRole("tab", { name: "Текст", exact: true }).click();
        await expect(dialog.getByLabel("Исходный текст Markdown")).toContainText("Согласовано");
        await page.screenshot({ path: testInfo.outputPath(`documents-view-${width}.png`) });
        await page.keyboard.press("Escape"); await expect(dialog).toHaveCount(0); await expect(row).toBeFocused();
        await page.getByLabel("Принадлежность документа").selectOption("tasks");
        await expect(page.getByRole("button", { name: "Открыть документ Требования задачи" })).toBeVisible();
        await expect(page.getByRole("button", { name: "Открыть документ План проекта" })).toHaveCount(0);
        expect(state.requests.filter(url => url.pathname === "/api/v1/documents").at(-1)?.searchParams.has("q")).toBe(false);
        await page.getByLabel("Задача документа", { exact: true }).selectOption("100");
        expect(state.requests.filter(url => url.pathname === "/api/v1/documents").at(-1)?.searchParams.get("task_id")).toBe("100");
        await page.getByRole("button", { name: "Сбросить фильтры", exact: true }).click();
        await page.getByLabel("Поиск документов проекта").fill("Согласованный вариант");
        await expect(row).toBeVisible();
        await expect(page.getByRole("button", { name: "Открыть документ План проекта" })).toHaveCount(0);
        await page.screenshot({ path: testInfo.outputPath(`documents-list-${width}.png`) });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(state.errors).toEqual([]);
    });
}

for (const width of [1440, 390]) {
    test(`документы: создание, единый upload и редактирование ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        const state = await installDocumentsApi(page);
        await page.goto("/projects/DEMO/docs");
        await page.getByRole("button", { name: "Добавить документ", exact: true }).click();
        const composer = page.getByRole("dialog", { name: "Добавить документ", exact: true });
        await composer.getByLabel("Название документа", { exact: true }).fill("Решения встречи");
        await composer.getByLabel("Текст документа · Markdown").fill("# Встреча\n\n**Согласовано** на пятницу");
        await composer.getByRole("button", { name: "Добавить", exact: true }).click();
        const viewer = page.getByRole("dialog", { name: "Документ", exact: true });
        await expect(viewer.locator("strong")).toHaveText("Согласовано");
        await viewer.getByRole("button", { name: "Редактировать", exact: true }).click();
        await viewer.getByLabel("Содержимое документа в Markdown").fill("# Обновлено\n\nНовая версия");
        await viewer.getByRole("button", { name: "Сохранить документ", exact: true }).click();
        await expect(viewer.getByRole("heading", { name: "Обновлено", exact: true })).toBeVisible();
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "Добавить документ", exact: true }).click();
        await composer.getByRole("tab", { name: "Загрузить", exact: true }).click();
        await composer.getByLabel("Выбрать документы", { exact: true }).setInputFiles([{ name: "budget.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("Таблица") }, { name: "error.pdf", mimeType: "application/pdf", buffer: Buffer.from("Ошибка") }]);
        await composer.getByLabel("Подпись к budget.xlsx").fill("Утверждённый бюджет");
        await composer.getByRole("button", { name: "Добавить", exact: true }).click();
        await expect(composer.getByText(/Нет места для документа/)).toBeVisible();
        await expect(composer.getByLabel("Подпись к budget.xlsx")).toHaveCount(0);
        await expect(composer.getByLabel("Подпись к error.pdf")).toBeVisible();
        await composer.getByRole("button", { name: "Отмена", exact: true }).click();
        await expect(page.getByRole("button", { name: "Открыть документ budget.xlsx", exact: true })).toBeVisible();
        expect(state.documents.filter(document => document.title === "budget.xlsx")).toHaveLength(1);
        expect(state.writes.filter(write => write.path === "/api/v1/documents" && write.method === "POST")).toHaveLength(1);
        expect(state.errors).toEqual([]);
    });

    test(`задача: общий документ проекта, комментарий, повтор и сохранение документов ${width}px`, async ({ page }, testInfo) => {
        test.setTimeout(60_000);
        await page.setViewportSize({ width, height: 900 });
        const state = await installDocumentsApi(page); state.failNextComments = 1;
        await page.goto("/projects/DEMO/tasks");
        await page.getByRole("button", { name: "DEMO-1", exact: true }).click();
        const task = page.getByRole("dialog", { name: "Задача DEMO-1", exact: true });
        await task.getByRole("tab", { name: /^Документы/ }).click();
        await task.getByRole("button", { name: "Добавить документ", exact: true }).click();
        const composer = page.getByRole("dialog", { name: "Добавить документ", exact: true });
        await composer.getByRole("tab", { name: "Из проекта", exact: true }).click();
        await composer.getByRole("checkbox", { name: /План проекта/ }).check();
        await composer.getByRole("button", { name: "Добавить", exact: true }).click();
        await expect(task.getByRole("button", { name: "Открыть документ План проекта", exact: true })).toBeVisible();
        await task.getByRole("tab", { name: /^Комментарии/ }).click();
        await task.getByLabel("Текст комментария").fill("Итоги встречи");
        await task.getByText("Из документов задачи", { exact: true }).click();
        await task.getByRole("checkbox", { name: "Прикрепить План проекта", exact: true }).check();
        await task.getByLabel("Документы к комментарию", { exact: true }).setInputFiles({ name: "budget.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("Таблица") });
        await task.getByLabel("Подпись к budget.xlsx").fill("Утверждённые расходы");
        await task.getByRole("button", { name: "Отправить", exact: true }).click();
        await expect(task.getByText(/Связь прервалась/)).toBeVisible();
        await expect(task.getByLabel("Подпись к budget.xlsx")).toHaveValue("Утверждённые расходы");
        await task.getByRole("button", { name: "Отправить", exact: true }).click();
        await expect(task.locator("article").getByText("Утверждённые расходы", { exact: true })).toBeVisible();
        expect(state.commentSubmissions).toHaveLength(2);
        expect(state.commentSubmissions[0]).toContain('name="document_refs"');
        expect(state.commentSubmissions[0]).not.toContain('name="attachment_ids"');
        const id = (body: string) => /name="request_id"\r?\n\r?\n([^\r\n]+)/.exec(body)?.[1];
        expect(id(state.commentSubmissions[0])).toBe(id(state.commentSubmissions[1]));
        await page.screenshot({ path: testInfo.outputPath(`documents-comments-${width}.png`) });
        await task.locator("article").getByRole("button", { name: "Удалить", exact: true }).click();
        await expect(task.locator("article")).toHaveCount(0);
        await task.getByRole("tab", { name: /^Документы/ }).click();
        await expect(task.getByRole("button", { name: "Открыть документ budget.xlsx", exact: true })).toBeVisible();
        expect(state.documents.filter(document => document.title === "budget.xlsx")).toHaveLength(1);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(state.errors).toEqual([]);
    });
}
