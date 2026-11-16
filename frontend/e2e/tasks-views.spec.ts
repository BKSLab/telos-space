import { expect, test } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";

// Нативная вставка не должна попадать в trace браузера.
test.use({ trace: "off" });

for (const width of [1440, 390, 320]) {
    test(`срок и исполнитель занимают разные строки, контуры иконок используют цвета темы при ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        await page.clock.setFixedTime(new Date("2026-11-16T12:00:00+04:00"));
        const state = mockState();
        const executor = { ...state.me, last_name: "Барабанщиков-Кирилловский", first_name: "Кирилл", middle_name: "Александрович" };
        const executorName = `${executor.last_name} ${executor.first_name} ${executor.middle_name}`;
        state.stages.push({ ...state.stages[0], id: 2, name: "Готово", order_index: 1, is_done_stage: true });
        const base = { ...state.tasks[0], description_md: null, last_comment: null };
        state.tasks = [
            { ...base, title: "Согласовать дату с исполнителем", due_date: "2026-11-28", comments_count: 12,
                participants: [{ id: 1, role: "EXECUTOR", user: executor }] },
            { ...base, id: 101, number: 2, key: "DEMO-2", title: "Просроченная задача", due_date: "2026-11-15" },
            { ...base, id: 102, number: 3, key: "DEMO-3", title: "Завершённая задача", stage_id: 2, due_date: "2026-11-15" },
            { ...base, id: 103, number: 4, key: "DEMO-4", title: "Только исполнитель", due_date: null, comments_count: 0,
                participants: [{ id: 2, role: "EXECUTOR", user: executor }] },
        ];
        await installMockApi(page, state);
        await page.goto("/projects/DEMO/tasks");
        const card = page.getByRole("heading", { name: state.tasks[0].title, exact: true }).locator("..");
        const date = card.locator('[title^="Срок:"]');
        const name = card.getByText(executorName, { exact: true });
        const comments = card.locator('[title="Комментариев: 12"]');
        await expect(date).toBeVisible();
        await expect(name).toBeVisible();
        const dateBox = await date.boundingBox();
        const nameBox = await name.boundingBox();
        const commentsBox = await comments.boundingBox();
        expect(nameBox!.y).toBeGreaterThanOrEqual(dateBox!.y + dateBox!.height);
        expect(Math.abs(commentsBox!.y - dateBox!.y)).toBeLessThan(4);
        expect(await name.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
        await expect(name).toHaveCSS("white-space", "normal");
        await expect(name).toHaveCSS("text-overflow", "clip");
        const lineCount = await date.locator(".font-mono").evaluate(element => {
            const range = document.createRange();
            range.selectNodeContents(element);
            return range.getClientRects().length;
        });
        expect(lineCount).toBe(1);
        const themeColor = async (token: string) => card.evaluate((element, token) => {
            const probe = document.createElement("span");
            probe.style.color = `var(${token})`;
            element.append(probe);
            const color = getComputedStyle(probe).color;
            probe.remove();
            return color;
        }, token);
        const accent = await themeColor("--color-accent");
        await expect(date).toHaveCSS("color", accent);
        await expect(date.locator("svg")).toHaveCSS("stroke", accent);
        await expect(comments.locator("svg")).toHaveCSS("stroke", accent);
        await expect(comments.locator("svg")).toHaveCSS("fill", "none");
        const overdue = page.getByRole("heading", { name: "Просроченная задача", exact: true }).locator("..").locator('[title^="Срок:"]');
        await expect(overdue).toHaveCSS("color", await themeColor("--color-danger"));
        await expect(overdue).toHaveAttribute("title", /просрочен/);
        const completed = page.getByRole("heading", { name: "Завершённая задача", exact: true }).locator("..").locator('[title^="Срок:"]');
        await expect(completed).toHaveCSS("color", await themeColor("--color-text-muted"));
        await expect(completed).not.toHaveAttribute("title", /просрочен/);
        const executorOnly = page.getByRole("heading", { name: "Только исполнитель", exact: true }).locator("..");
        await expect(executorOnly.getByText(executorName, { exact: true })).toBeVisible();
        await expect(executorOnly.locator('[title^="Срок:"], [title^="Комментариев:"]')).toHaveCount(0);
        await page.screenshot({ path: info.outputPath(`task-card-footer-${width}.png`) });
        expect(state.errors).toEqual([]);
        expect(state.writes).toEqual([]);
    });
}

for (const width of [1440, 390, 320]) {
    test(`общая вкладка задач: виды, фильтры и ссылки при ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 900 });
        const state = mockState();
        state.tasks = state.tasks.filter(task => task.project_id === 1);
        state.stages.push({ ...state.stages[0], id: 2, name: "Готово", order_index: 1, is_done_stage: true });
        state.tasks[0].priority = "HIGH";
        state.tasks[1].stage_id = 2;
        state.tasks[1].priority = "LOW";
        await installMockApi(page, state);
        await page.goto("/projects/DEMO/tasks#context");
        const projectNav = page.getByRole("navigation", { name: "Разделы проекта" });
        await expect(projectNav.getByRole("link", { name: "Задачи", exact: true })).toHaveAttribute("aria-current", "page");
        await expect(projectNav.getByRole("link", { name: "Канбан", exact: true })).toHaveCount(0);
        const views = page.getByRole("navigation", { name: "Представление задач" });
        await expect(views.getByRole("link", { name: "Канбан", exact: true })).toHaveAttribute("aria-current", "page");
        await expect(page.getByRole("region", { name: `Колонка ${state.stages[0].name}`, exact: true })).toBeVisible();
        await page.getByLabel("Фильтр по приоритету").selectOption("HIGH");
        await views.getByRole("link", { name: "Список", exact: true }).click();
        await expect(page.getByRole("table")).toBeVisible();
        await expect(page.getByRole("button", { name: state.tasks[0].key, exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: state.tasks[1].key, exact: true })).toHaveCount(0);
        expect(new URL(page.url()).searchParams.get("priority")).toBe("HIGH");
        expect(new URL(page.url()).hash).toBe("#context");
        await page.reload();
        await expect(views.getByRole("link", { name: "Список", exact: true })).toHaveAttribute("aria-current", "page");
        await expect(page.getByLabel("Фильтр по приоритету")).toHaveValue("HIGH");
        await views.getByRole("link", { name: "Канбан", exact: true }).click();
        await expect(page.getByRole("heading", { name: state.tasks[0].title, exact: true })).toBeVisible();
        await page.goBack();
        await expect(page.getByRole("table")).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await info.attach(`tasks-list-${width}`, { body: await page.screenshot({ path: info.outputPath(`tasks-list-${width}.png`) }), contentType: "image/png" });
        expect(state.writes).toEqual([]);
        expect(state.errors).toEqual([]);
    });

    test(`градиент закреплён на длинной колонке при ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 900 });
        const state = mockState();
        state.stages = [
            { ...state.stages[0], color: "#58a6ff" },
            { ...state.stages[0], id: 2, name: "Готово", order_index: 1, color: "#d29922" },
        ];
        state.tasks = Array.from({ length: 18 }, (_, index) => ({ ...state.tasks[0], id: 100 + index, key: `DEMO-${index + 1}`, title: `Задача ${index + 1}`, position: (index + 1) * 1000 }));
        await installMockApi(page, state);
        await page.goto("/projects/DEMO/tasks");
        const column = page.getByRole("region", { name: `Колонка ${state.stages[0].name}`, exact: true });
        const surface = column.locator(":scope > div");
        await expect(surface).toBeVisible();
        const before = await surface.evaluate(element => {
            const style = getComputedStyle(element);
            return { image: style.backgroundImage, attachment: style.backgroundAttachment, height: element.clientHeight, scrollHeight: element.scrollHeight };
        });
        expect(before.image).toContain("linear-gradient(135deg");
        expect(before.attachment).toBe("scroll");
        expect(before.scrollHeight).toBeGreaterThan(before.height * 2);
        expect(before.height).toBeLessThan(900);
        await surface.evaluate(element => { element.scrollTop = element.scrollHeight; });
        await expect(surface).toHaveCSS("background-image", before.image);
        expect(await surface.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
        await expect(surface.getByRole("heading", { name: "Задача 18", exact: true })).toBeVisible();
        const empty = page.getByRole("region", { name: "Колонка Готово", exact: true });
        if (width === 1440) await expect(empty.getByText("Пусто", { exact: true })).toBeVisible();
        await info.attach(`kanban-gradient-${width}`, { body: await page.screenshot({ path: info.outputPath(`kanban-gradient-${width}.png`) }), contentType: "image/png" });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.emulateMedia({ forcedColors: "active" });
        await expect(surface).toHaveCSS("background-image", "none");
        expect(state.errors).toEqual([]);
    });
}

for (const viewport of [{ width: 320, height: 568 }, { width: 844, height: 390 }]) {
    test(`низ длинной колонки доступен на коротком экране ${viewport.width}×${viewport.height}`, async ({ page }) => {
        await page.setViewportSize(viewport);
        const state = mockState();
        state.tasks = Array.from({ length: 18 }, (_, index) => ({ ...state.tasks[0], id: 100 + index, key: `DEMO-${index + 1}`, title: `Задача ${index + 1}`, position: (index + 1) * 1000 }));
        await installMockApi(page, state);
        await page.goto("/projects/DEMO/tasks");
        const column = page.getByRole("region", { name: `Колонка ${state.stages[0].name}`, exact: true });
        const surface = column.locator(":scope > div");
        await expect(surface).toBeVisible();
        await surface.evaluate(element => { element.scrollTop = element.scrollHeight; });
        const lastStage = column.getByLabel("Стадия задачи DEMO-18", { exact: true });
        await expect(lastStage).toBeInViewport({ ratio: 1 });
        await lastStage.click({ trial: true });
        const bounds = await surface.boundingBox();
        const last = await lastStage.boundingBox();
        expect(last!.y + last!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height);
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);
        expect(state.writes).toEqual([]);
        expect(state.errors).toEqual([]);
    });
}

test("плотный канбан: прозрачный фон карточек сохраняет контраст, выбор и системные режимы", async ({ page }, info) => {
    await page.setViewportSize({ width: 1616, height: 1000 });
    const state = mockState();
    state.stages = [
        { ...state.stages[0], id: 1, name: "Бэклог", color: "#8b949e", order_index: 0 },
        { ...state.stages[0], id: 2, name: "В работе", color: "#58a6ff", order_index: 1 },
        { ...state.stages[0], id: 3, name: "Тестирование", color: "#d29922", order_index: 2 },
        { ...state.stages[0], id: 4, name: "Готово", color: "#3fb950", order_index: 3, is_done_stage: true },
    ];
    state.tasks = state.stages.flatMap(stage => Array.from({ length: 5 }, (_, index) => ({
        ...state.tasks[0], id: stage.id * 100 + index, stage_id: stage.id, key: `DEMO-${stage.id * 10 + index}`,
        title: `Проверить документы и согласовать результат ${index + 1}`,
        description_md: "Уточнить ожидаемый результат, проверить изменения и подготовить проект к следующему этапу.",
        last_comment: "Результат проверки сохранён; детали доступны в карточке задачи.",
        position: (index + 1) * 1000,
    })));
    await installMockApi(page, state);
    await page.goto("/projects/DEMO/tasks");
    const column = page.getByRole("region", { name: "Колонка В работе", exact: true });
    const card = column.locator(".surface-card").first();
    const heading = card.getByRole("heading", { level: 3 });
    await expect(card).toBeVisible();
    await expect(card).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await expect(card).toHaveCSS("opacity", "1");
    expect(await card.evaluate(element => getComputedStyle(element).backgroundImage)).toContain("linear-gradient(135deg");
    const textColor = await heading.evaluate(element => getComputedStyle(element).color);
    const controlColor = await card.locator("select").evaluate(element => getComputedStyle(element).backgroundColor);
    await info.attach("kanban-dense-transparency", { body: await page.screenshot({ path: info.outputPath("kanban-dense-transparency.png") }), contentType: "image/png" });

    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-transparency", value: "reduce" }] });
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-transparency: reduce)").matches)).toBe(true);
    await expect(card).toHaveCSS("background-image", "none");
    const opaqueBackground = await card.evaluate(element => getComputedStyle(element).backgroundColor);
    expect(opaqueBackground).not.toBe("rgba(0, 0, 0, 0)");
    await expect(heading).toHaveCSS("color", textColor);
    await expect(card.locator("select")).toHaveCSS("background-color", controlColor);
    await cdp.send("Emulation.setEmulatedMedia", { features: [] });

    await heading.click();
    const dialog = page.getByRole("dialog", { name: "Задача DEMO-20", exact: true });
    await expect(dialog).toBeVisible();
    expect(await card.evaluate(element => getComputedStyle(element).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
    await dialog.getByRole("button", { name: "Закрыть задачу", exact: true }).click();
    await expect(card).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await page.emulateMedia({ forcedColors: "active" });
    await expect(card).toHaveCSS("background-image", "none");
    expect(state.errors).toEqual([]);
});

test.describe("нативное копирование задачи на HTTP", () => {
    test.describe.configure({ mode: "serial" });
    test("асинхронный отказ копирования сохраняет фокус для клавиатурного повтора", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto("/my-tasks?task=100");
        await page.evaluate(() => {
            document.documentElement.dataset.copyAttempts = "0";
            Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
                writeText: async () => {
                    document.documentElement.dataset.copyAttempts = String(Number(document.documentElement.dataset.copyAttempts) + 1);
                    await new Promise(resolve => setTimeout(resolve, 50));
                    throw new Error("Clipboard denied");
                },
            } });
            document.execCommand = () => false;
        });
        const card = page.getByRole("dialog", { name: "Задача DEMO-1", exact: true });
        const copy = card.getByRole("button", { name: "Скопировать ключ и название", exact: true });
        await copy.focus();
        await page.keyboard.press("Enter");
        await expect(card.getByRole("alert")).toHaveText("Не удалось скопировать ключ и название. Попробуйте ещё раз.");
        await expect(copy).toBeFocused();
        await page.keyboard.press("Enter");
        await expect.poll(() => page.locator("html").getAttribute("data-copy-attempts")).toBe("2");
        await expect(card.getByRole("alert")).toBeVisible();
        await expect(copy).toBeFocused();
        expect(state.errors).toEqual([]);
    });

    test("ключ и полное название копируются из модального окна без Clipboard API", async ({ page, baseURL }) => {
        const staticOrigin = new URL(baseURL ?? "http://127.0.0.1:4179");
        const origin = "http://task-copy.telos.invalid";
        await page.route(`${origin}/**`, async route => {
            if (new URL(route.request().url()).pathname.startsWith("/api/")) return route.fallback();
            return route.fulfill({ response: await route.fetch({ url: new URL(new URL(route.request().url()).pathname, staticOrigin).href }) });
        });
        const state = mockState();
        state.tasks[0].title = "Подготовить план: ИСР, документы и проверка ✓";
        await installMockApi(page, state);
        await page.goto(`${origin}/my-tasks?task=100`);
        expect(await page.evaluate(() => ({ secure: isSecureContext, clipboard: typeof navigator.clipboard }))).toEqual({ secure: false, clipboard: "undefined" });
        const card = page.getByRole("dialog", { name: "Задача DEMO-1", exact: true });
        const copy = card.getByRole("button", { name: "Скопировать ключ и название", exact: true });
        await expect(copy).toBeVisible();
        await copy.focus();
        await copy.press("Enter");
        await expect(card.getByRole("status").filter({ hasText: "Ключ и название скопированы" })).toHaveText("Ключ и название скопированы");
        await expect(copy).toBeFocused();
        await card.evaluate(element => {
            const probe = document.createElement("textarea");
            probe.setAttribute("aria-label", "Проверка вставки задачи");
            element.append(probe);
            probe.focus();
        });
        const probe = card.getByRole("textbox", { name: "Проверка вставки задачи", exact: true });
        try {
            await page.keyboard.press("Control+V");
            await expect(probe).toHaveValue(`${state.tasks[0].key} ${state.tasks[0].title}`);
        } finally {
            await probe.evaluate(element => element.remove());
        }
        await expect(card.locator("textarea[readonly]")).toHaveCount(0);
        expect(state.errors).toEqual([]);
    });
});
