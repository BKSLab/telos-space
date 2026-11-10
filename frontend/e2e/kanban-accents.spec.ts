import { expect, test, type Locator, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";

async function openKanban(page: Page) {
    const state = mockState();
    const [first, second] = state.tasks;
    state.stages = [
        { ...state.stages[0], name: "Запланировано", color: "#58a6ff" },
        { ...state.stages[0], id: 2, name: "В работе", color: "#3fb950", order_index: 1 },
        { ...state.stages[0], id: 3, name: "Готово", color: "#d29922", order_index: 2, is_done_stage: true },
    ];
    state.tasks = [
        { ...first, stage_id: 1, position: 1000, priority: "HIGH", due_date: "2030-11-20" },
        { ...second, stage_id: 2, position: 1000, title: "Первая задача целевой колонки" },
        { ...second, id: 102, key: "DEMO-3", number: 3, stage_id: 2, position: 5000, title: "Последняя задача целевой колонки" },
    ];
    await installMockApi(page, state);
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    await page.route("**/api/v1/tasks/*/stage-placement", async route => {
        const request = route.request();
        const headers = {
            "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179",
            "access-control-allow-credentials": "true",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "PATCH, OPTIONS",
        };
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (request.method() !== "PATCH") return route.fallback();
        const path = new URL(request.url()).pathname.replace("/api/v1", "");
        const taskId = Number(path.split("/")[2]);
        const body = request.postDataJSON() as { stage_id: number; position: number };
        state.writes.push({ method: "PATCH", path, body });
        const task = state.tasks.find(item => item.id === taskId)!;
        Object.assign(task, body);
        return route.fulfill({ headers, json: task });
    });
    await page.goto("/projects/DEMO/board");
    const sourceColumn = page.getByRole("region", { name: "Колонка Запланировано", exact: true });
    const targetColumn = page.getByRole("region", { name: "Колонка В работе", exact: true });
    const emptyColumn = page.getByRole("region", { name: "Колонка Готово", exact: true });
    const source = sourceColumn.locator('[aria-roledescription="draggable"]');
    await expect(source).toBeVisible();
    await expect(targetColumn.getByRole("heading", { level: 3 })).toHaveCount(2);
    return { state, sourceColumn, targetColumn, emptyColumn, source };
}

function insertionMarker(column: Locator) {
    return column.getByRole("status").filter({ hasText: "В конец колонки" });
}

async function startPointerDrag(page: Page, source: Locator) {
    const box = (await source.boundingBox())!;
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 8, y + 2, { steps: 2 });
    await expect(source).toHaveAttribute("aria-pressed", "true");
}

async function movePointerTo(page: Page, target: Locator) {
    const box = (await target.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 10 });
}

async function expectStageColors(columns: Locator[]) {
    for (const [index, color] of ["rgb(88, 166, 255)", "rgb(63, 185, 80)", "rgb(210, 153, 34)"].entries()) {
        await expect(columns[index].locator("header [aria-hidden=true]").first()).toHaveCSS("background-color", color);
    }
}

test("drag показывает вставку в конец, а drop сохраняет этот порядок и цвета стадий", async ({ page }, info) => {
    const { state, sourceColumn, targetColumn, emptyColumn, source } = await openKanban(page);
    const columns = [sourceColumn, targetColumn, emptyColumn];
    const marker = insertionMarker(targetColumn);
    const dropzone = targetColumn.locator(":scope > div");
    const neutralBackground = await dropzone.evaluate(element => getComputedStyle(element).backgroundImage);
    await expect(page.getByText("В конец колонки", { exact: true })).toHaveCount(0);
    await expectStageColors(columns);
    await startPointerDrag(page, source);
    await expect(insertionMarker(sourceColumn)).toHaveCount(0);

    // Even over the first card, this board appends to the target stage.
    await movePointerTo(page, targetColumn.getByRole("heading", { name: "Первая задача целевой колонки", exact: true }));
    await expect(marker).toBeVisible();
    const markerBox = (await marker.boundingBox())!;
    const lastCardBox = (await targetColumn.locator('[aria-roledescription="draggable"]').last().boundingBox())!;
    expect(markerBox.y).toBeGreaterThan(lastCardBox.y + lastCardBox.height);
    expect(await marker.evaluate(element => getComputedStyle(element, "::before").backgroundImage)).toContain("linear-gradient");
    expect(await dropzone.evaluate(element => getComputedStyle(element).backgroundImage)).toContain("linear-gradient");
    await expectStageColors(columns);
    await expect(page.getByRole("heading", { name: state.tasks[0].title, exact: true })).toHaveCount(2);
    await expect(page.getByText("Высокий", { exact: true })).toHaveCount(2);
    await expect(page.getByTitle(/^Срок: .*2030/)).toHaveCount(2);
    expect(state.writes).toEqual([]);
    await info.attach("kanban-drag-target", {
        body: await page.screenshot({ path: info.outputPath("kanban-drag-target.png") }),
        contentType: "image/png",
    });

    await page.mouse.up();
    await expect.poll(() => state.writes).toEqual([
        { method: "PATCH", path: "/tasks/100/stage-placement", body: { stage_id: 2, position: 6000 } },
    ]);
    await expect(targetColumn.getByRole("heading", { level: 3 })).toHaveText([
        "Первая задача целевой колонки", "Последняя задача целевой колонки", state.tasks[0].title,
    ]);
    await expect(page.getByText("В конец колонки", { exact: true })).toHaveCount(0);
    await expect(dropzone).toHaveCSS("background-image", neutralBackground);
    await expectStageColors(columns);
    expect(state.errors).toEqual([]);
});

test("своя колонка не предлагает перестановку; Escape убирает цель без записи", async ({ page }) => {
    const { state, sourceColumn, targetColumn, emptyColumn, source } = await openKanban(page);
    const dropzone = emptyColumn.locator(":scope > div");
    const neutralBackground = await dropzone.evaluate(element => getComputedStyle(element).backgroundImage);
    await startPointerDrag(page, source);
    await expect(page.getByText("В конец колонки", { exact: true })).toHaveCount(0);
    await page.mouse.up();
    await expect(source).not.toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("heading", { name: state.tasks[0].title, exact: true })).toHaveCount(1);
    expect(state.writes).toEqual([]);

    await startPointerDrag(page, source);
    await movePointerTo(page, emptyColumn.getByText("Пусто", { exact: true }));
    await expect(insertionMarker(emptyColumn)).toBeVisible();
    await expect(emptyColumn.getByText("Пусто", { exact: true })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await page.mouse.up();
    await expect(page.getByText("В конец колонки", { exact: true })).toHaveCount(0);
    await expect(emptyColumn.getByText("Пусто", { exact: true })).toBeVisible();
    await expect(dropzone).toHaveCSS("background-image", neutralBackground);
    await expect(sourceColumn.getByRole("heading", { level: 3 })).toHaveCount(1);
    await expect(targetColumn.getByRole("heading", { level: 3 })).toHaveCount(2);
    await expect(source).not.toHaveAttribute("aria-pressed", "true");
    await expectStageColors([sourceColumn, targetColumn, emptyColumn]);
    expect(state.writes).toEqual([]);
    expect(state.errors).toEqual([]);
});

test("перенос с клавиатуры показывает ту же позицию и добавляет задачу в конец", async ({ page }) => {
    const { state, sourceColumn, targetColumn, source } = await openKanban(page);
    await source.focus();
    await expect(source).toBeFocused();
    await page.keyboard.press("Space");
    await expect(source).toHaveAttribute("aria-pressed", "true");
    await expect(insertionMarker(sourceColumn)).toHaveCount(0);
    const sourceBox = (await source.boundingBox())!;
    const targetBox = (await targetColumn.boundingBox())!;
    const steps = Math.round((targetBox.x - sourceBox.x) / 25);
    for (let step = 0; step < steps; step += 1) await page.keyboard.press("ArrowRight");
    await expect(insertionMarker(targetColumn)).toBeVisible();
    expect(state.writes).toEqual([]);
    await page.keyboard.press("Space");
    await expect.poll(() => state.writes).toEqual([
        { method: "PATCH", path: "/tasks/100/stage-placement", body: { stage_id: 2, position: 6000 } },
    ]);
    await expect(targetColumn.getByRole("heading", { level: 3 }).last()).toHaveText(state.tasks[0].title);
    await expect(page.getByText("В конец колонки", { exact: true })).toHaveCount(0);
    expect(state.errors).toEqual([]);
});
