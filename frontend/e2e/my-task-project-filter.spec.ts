import { expect, test } from "@playwright/test";
import type { MyTask } from "../src/lib/types";
import { installMockApi, mockState } from "./support/taskMockApi";

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    test(`фильтр моих задач по проекту: выбор, сочетание и сброс на ${viewport.width}px`, async ({ page }, testInfo) => {
        await page.setViewportSize(viewport);
        const state = mockState();
        const template = state.tasks[0];
        const personal = state.tasks.find(task => task.project_id === null)!;
        state.tasks = [
            ...Array.from({ length: 52 }, (_, index) => ({ ...template, id: 1000 + index, number: index + 1,
                key: `DEMO-${index + 1}`, title: `Проверить проектный пункт ${index + 1}` })),
            { ...template, id: 2000, project_id: 2, key: "SAMPLE-1", title: "Согласовать результат второго проекта" },
            { ...template, id: 2001, project_id: 2, key: "SAMPLE-2", title: "Опубликовать готовый результат", status: "DONE" },
            { ...personal, id: 3000, title: "Разобрать личные документы" },
        ];
        await installMockApi(page, state);
        const requests: URLSearchParams[] = [];
        await page.route("**/api/v1/users/me/tasks?*", async route => {
            const request = route.request();
            if (request.method() !== "GET") return route.fallback();
            const params = new URL(request.url()).searchParams;
            requests.push(params);
            const projectId = params.get("project_id");
            const tasks = state.tasks.filter(task => (!projectId || task.project_id === Number(projectId))
                && (params.get("include_closed") === "true" || task.status !== "DONE"));
            const rows = tasks.map((task): MyTask => {
                const project = state.projects.find(item => item.id === task.project_id);
                return { ...task, context: project ? { kind: "PROJECT", id: project.id, key: project.key, name: project.name, color: project.color } : null,
                    state: task.status ?? "IN_PROGRESS", state_label: task.status === "DONE" ? "Готово" : "В работе", my_roles: ["REPORTER", "EXECUTOR"] };
            });
            return route.fulfill({ json: rows, headers: {
                "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179",
                "access-control-allow-credentials": "true",
            } });
        });

        await page.goto("/my-tasks");
        const filter = page.getByRole("combobox", { name: "Проект", exact: true });
        const pages = page.getByRole("navigation", { name: "Страницы задач" });
        await expect(filter).toBeEnabled();
        await expect(filter.locator("option")).toHaveText(["Все проекты", ...state.projects.map(project => `${project.key} · ${project.name}`)]);
        await page.getByRole("button", { name: "Дальше", exact: true }).click();
        await expect(pages).toContainText("2 / 2");
        await expect(page.getByRole("button", { name: "Разобрать личные документы", exact: true })).toBeVisible();

        await filter.selectOption("2");
        await expect(page.getByRole("button", { name: "Согласовать результат второго проекта", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Разобрать личные документы", exact: true })).toHaveCount(0);
        await expect(pages).toHaveCount(0);
        await expect.poll(() => requests.at(-1)?.get("project_id")).toBe("2");
        await page.getByLabel("Показывать закрытые", { exact: true }).check();
        await expect(page.getByRole("button", { name: "Опубликовать готовый результат", exact: true })).toBeVisible();
        await page.getByRole("group", { name: "Моя роль в задаче" }).getByRole("button", { name: "Делаю", exact: true }).click();
        await expect.poll(() => requests.at(-1)?.get("role")).toBe("doing");
        expect(requests.at(-1)?.get("project_id")).toBe("2");
        const search = page.getByRole("textbox", { name: "Поиск по моим задачам" });
        await search.fill("готовый результат");
        await expect(page.getByRole("button", { name: "Опубликовать готовый результат", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Согласовать результат второго проекта", exact: true })).toHaveCount(0);
        await search.clear();
        await page.getByLabel("Показывать закрытые", { exact: true }).uncheck();
        await expect(page.getByRole("button", { name: "Опубликовать готовый результат", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Согласовать результат второго проекта", exact: true })).toBeVisible();
        await page.getByRole("heading", { name: "Мои задачи", exact: true }).scrollIntoViewIfNeeded();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        await page.screenshot({ path: testInfo.outputPath(`project-filter-${viewport.width}.png`) });

        await filter.selectOption("3");
        await expect(page.getByText("Задач пока нет", { exact: true })).toBeVisible();
        await filter.selectOption("");
        await expect(pages).toContainText("1 / 2");
        await page.getByRole("button", { name: "Дальше", exact: true }).click();
        await expect(page.getByRole("button", { name: "Разобрать личные документы", exact: true })).toBeVisible();
        await expect(filter).toHaveValue("");
        expect(state.errors).toEqual([]);
    });
}
