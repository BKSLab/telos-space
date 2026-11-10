import { expect, test, type Locator, type Page } from "@playwright/test";
import { installMockApi, mockState, stamp } from "./support/mockApi";

test.use({ hasTouch: true });

async function pointer(target: Locator, type: string, id: number, x: number, y: number) {
    await target.dispatchEvent(type, { pointerId: id, pointerType: "touch", button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: x, clientY: y, bubbles: true });
}
async function frame(page: Page) {
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

for (const viewport of [{ width: 320, height: 640 }, { width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 844, height: 390 }]) {
    test(`доска ${viewport.width}×${viewport.height}: панели освобождают холст, подпись меняется касанием`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        const state = mockState();
        await installMockApi(page, state);
        await page.route("https://fonts.gstatic.com/**", route => route.abort());
        await page.goto("/projects/DEMO/whiteboard?board=2");
        const canvas = page.getByRole("region", { name: /Холст доски/ });
        await expect(canvas).toBeVisible();
        const toolbar = page.getByRole("toolbar", { name: "Инструменты доски", exact: true });
        await expect(toolbar).toHaveAttribute("aria-orientation", "horizontal");
        const toolsBox = (await toolbar.boundingBox())!;
        const canvasBox = (await canvas.boundingBox())!;
        expect(toolsBox.y + toolsBox.height).toBeLessThanOrEqual(canvasBox.y + 1);
        expect(canvasBox.height).toBeGreaterThan(70);
        const zoomBox = (await page.getByRole("group", { name: "Масштаб холста" }).boundingBox())!;
        expect(zoomBox.y).toBeGreaterThanOrEqual(canvasBox.y + canvasBox.height - 1);

        await page.getByRole("button", { name: "Прямоугольник (R)", exact: true }).tap();
        await page.touchscreen.tap(canvasBox.x + canvasBox.width / 2, canvasBox.y + canvasBox.height / 2);
        await expect(page.locator("[data-kind=rect]")).toHaveCount(1);
        await expect(page.getByRole("toolbar", { name: "Свойства выделенного", exact: true })).toBeHidden();
        await page.getByRole("button", { name: "Свойства выделенного", exact: true }).tap();
        const props = page.getByRole("toolbar", { name: "Свойства выделенного", exact: true });
        await expect(props).toBeVisible();
        const reducedCanvas = (await canvas.boundingBox())!;
        expect((await props.boundingBox())!.y).toBeGreaterThanOrEqual(reducedCanvas.y + reducedCanvas.height - 1);
        await props.getByRole("button", { name: "Редактировать подпись", exact: true }).tap();
        const dialog = page.getByRole("dialog", { name: "Редактировать подпись", exact: true });
        await dialog.getByRole("textbox", { name: "Подпись фигуры" }).fill("Мобильная схема");
        await dialog.getByRole("button", { name: "Сохранить", exact: true }).tap();
        await expect(page.locator("[data-kind=rect]")).toContainText("Мобильная схема");
        await expect(page.getByRole("status", { name: "Сохранение доски" })).toHaveText("Сохранено");
        await expect.poll(() => state.shapes.some(shape => shape.label === "Мобильная схема")).toBe(true);
        await page.getByRole("button", { name: "Свойства выделенного", exact: true }).tap();
        await info.attach(`board-${viewport.width}`, { body: await page.screenshot({ path: info.outputPath(`board-${viewport.width}.png`) }), contentType: "image/png" });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        expect(state.errors).toEqual([]);
    });
}

test("два пальца масштабируют доску, оставшийся палец продолжает панораму без записи объектов", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const state = mockState();
    await installMockApi(page, state);
    await page.goto("/projects/DEMO/whiteboard?board=2");
    const canvas = page.getByRole("region", { name: /Холст доски/ });
    await expect(page.getByRole("button", { name: "Рука (H)", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Рука (H)", exact: true }).tap();
    const box = (await canvas.boundingBox())!;
    const x = box.x + 80;
    const y = box.y + box.height / 2;
    const world = page.locator(".board-world");
    const original = await world.evaluate(element => getComputedStyle(element).transform);
    await pointer(canvas, "pointerdown", 10, x, y);
    await pointer(canvas, "pointerdown", 11, x + 100, y);
    await pointer(canvas, "pointermove", 11, x + 160, y);
    await frame(page);
    const pinched = await world.evaluate(element => getComputedStyle(element).transform);
    expect(pinched).not.toBe(original);
    const released = await canvas.evaluate(async (element, point) => {
        const read = () => {
            const matrix = new DOMMatrix(getComputedStyle(element.querySelector(".board-world")!).transform);
            return { scale: matrix.a, x: matrix.e, y: matrix.f };
        };
        const fire = (type: string) => element.dispatchEvent(new PointerEvent(type, {
            pointerId: 11, pointerType: "touch", button: 0,
            buttons: type === "pointerup" ? 0 : 1,
            clientX: point.x + 180, clientY: point.y, bubbles: true,
        }));
        const before = read();
        fire("pointermove");
        fire("pointerup");
        const onRelease = read();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return { before, onRelease, afterFrame: read() };
    }, { x, y });
    expect(released.onRelease.scale).toBeCloseTo(released.before.scale * 180 / 160, 5);
    expect(released.afterFrame).toEqual(released.onRelease);
    await pointer(canvas, "pointermove", 10, x + 30, y + 20);
    await frame(page);
    expect(await world.evaluate(element => getComputedStyle(element).transform)).not.toBe(pinched);
    await pointer(canvas, "pointerup", 10, x + 30, y + 20);
    expect(state.writes.filter(write => write.path.endsWith("/batch"))).toEqual([]);
    expect(state.errors).toEqual([]);
});

test("мобильный канбан: выбор стадии и перенос без drag сохраняют вставку в конец", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const state = mockState();
    state.stages.push({ ...state.stages[0], id: 2, name: "Готово", order_index: 1, is_done_stage: true });
    state.tasks = [{ ...state.tasks[0], position: 1000 }, { ...state.tasks[1], stage_id: 2, position: 5000 }];
    await installMockApi(page, state);
    await page.route("**/api/v1/tasks/*/stage-placement", async route => {
        const request = route.request();
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "PATCH, OPTIONS" };
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        const task = state.tasks.find(item => request.url().includes(`/tasks/${item.id}/stage-placement`))!;
        const body = request.postDataJSON();
        state.writes.push({ method: "PATCH", path: `/tasks/${task.id}/stage-placement`, body });
        Object.assign(task, body);
        return route.fulfill({ headers, json: task });
    });
    await page.goto("/projects/DEMO/board");
    await page.getByLabel("Стадия задачи DEMO-1", { exact: true }).selectOption("2");
    await expect.poll(() => state.writes.some(write => write.path === "/tasks/100/stage-placement" && JSON.stringify(write.body) === JSON.stringify({ stage_id: 2, position: 6000 }))).toBe(true);
    await page.getByRole("button", { name: "Следующая стадия" }).tap();
    const target = page.getByRole("region", { name: "Колонка Готово", exact: true });
    await expect(target.getByRole("heading", { level: 3 })).toHaveText([state.tasks[1].title, state.tasks[0].title]);
    await expect(page.getByLabel("Показать стадию")).toHaveValue("2");
    await expect(page.getByRole("button", { name: "Следующая стадия" })).toBeDisabled();
    expect((await target.boundingBox())!.x).toBeGreaterThanOrEqual(0);
    expect(state.errors).toEqual([]);
});

test("мобильная ИСР: список освобождает холст, задача переносится через видимые действия", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    const state = mockState();
    await installMockApi(page, state);
    const tasks = state.tasks.slice(0, 2).map(task => ({ ...task, is_done: false, wbs_node_id: null as number | null, wbs_position: null, canvas_x: null, canvas_y: null }));
    const nodes = [{ id: 1, project_id: 1, parent_id: null, title: "Подготовка", position: 1000, created_at: stamp, updated_at: stamp }];
    await page.route("**/api/v1/projects/1/wbs**", async route => {
        const request = route.request();
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, OPTIONS" };
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (request.url().endsWith("/placement")) {
            const body = request.postDataJSON();
            state.writes.push({ method: "POST", path: "/projects/1/wbs/tasks/100/placement", body });
            Object.assign(tasks[0], body, { wbs_position: 1000 });
            return route.fulfill({ headers, json: tasks[0] });
        }
        return route.fulfill({ headers, json: { nodes, tasks, stats: { total_nodes: 1, total_tasks: 2, assigned_tasks: tasks.filter(task => task.wbs_node_id !== null).length, unassigned_tasks: tasks.filter(task => task.wbs_node_id === null).length, done_tasks: 0, overdue_tasks: 0 } } });
    });
    await page.goto("/projects/DEMO/structure");
    const list = page.getByRole("complementary", { name: "Задачи проекта", exact: true });
    await expect(list).toBeHidden();
    await page.getByRole("button", { name: "Задачи · 2", exact: true }).tap();
    await expect(list).toBeVisible();
    await list.getByRole("button", { name: "Переместить в раздел…", exact: true }).first().tap();
    const dialog = page.getByRole("dialog", { name: "Переместить DEMO-1", exact: true });
    await dialog.getByLabel("Раздел", { exact: true }).selectOption("1");
    await dialog.getByRole("button", { name: "Переместить", exact: true }).tap();
    await expect.poll(() => tasks[0].wbs_node_id).toBe(1);
    await page.getByRole("button", { name: "Холст", exact: true }).tap();
    await expect(list).toBeHidden();
    const action = page.getByRole("button", { name: "Действия задачи DEMO-1", exact: true });
    await expect(action).toBeVisible();
    await page.getByRole("button", { name: "Вся структура", exact: true }).tap();
    await expect(action).toBeInViewport({ ratio: 0.99 });
    await action.tap();
    await expect(page.getByRole("menu", { name: "Действия задачи", exact: true })).toBeVisible();
    await page.getByRole("menuitem", { name: "Закрыть меню" }).tap();
    await expect(page.getByRole("menu")).toHaveCount(0);
    expect(state.errors).toEqual([]);
});
