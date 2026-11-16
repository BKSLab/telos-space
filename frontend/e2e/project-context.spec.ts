import { expect, test } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";
import { EMPTY_PROJECT_DESCRIPTION } from "../src/lib/projectContext";
import type { ProjectUpdate } from "../src/lib/types";

const headers = {
    "access-control-allow-origin": "http://127.0.0.1:4179",
    "access-control-allow-credentials": "true",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET, PATCH, OPTIONS",
};

for (const viewport of [
    { name: "desktop", width: 1440, height: 1000 },
    { name: "mobile", width: 390, height: 844 },
]) {
    test(`сохраняет черновик и явно разрешает конфликт паспорта — ${viewport.name}`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        const state = mockState();
        const project = state.projects[0];
        project.context_revision = 1;
        project.description_sections = { ...EMPTY_PROJECT_DESCRIPTION, goal: "Исходная цель" };
        project.description_md = "## Цель\n\nИсходная цель";
        await installMockApi(page, state);
        const patches: ProjectUpdate[] = [];
        await page.route("**/api/v1/projects/1", async route => {
            const responseHeaders = { ...headers, "access-control-allow-origin": route.request().headers()["origin"] ?? headers["access-control-allow-origin"] };
            const method = route.request().method();
            if (method === "OPTIONS") return route.fallback();
            if (method === "GET") return route.fulfill({ json: project, headers: responseHeaders });
            if (method !== "PATCH") return route.fallback();
            const patch = route.request().postDataJSON() as ProjectUpdate;
            patches.push(patch);
            if (patches.length === 1) {
                project.context_revision = 2;
                project.name = "Актуальное название";
                project.description_sections = { ...project.description_sections!, capabilities: "Каталог уже работает" };
                project.description_md += "\n\n## Текущие возможности\n\nКаталог уже работает";
                return route.fulfill({ status: 409, json: { detail: "Паспорт уже изменён. Обновите данные." }, headers: responseHeaders });
            }
            expect(patch.expected_context_revision).toBe(2);
            project.description_sections = { ...project.description_sections!, ...patch.description_sections };
            project.description_md = `## Цель\n\n${project.description_sections.goal}`;
            project.context_revision = 3;
            return route.fulfill({ json: project, headers: responseHeaders });
        });
        await page.goto("/projects/DEMO/settings");
        const goal = page.getByLabel("Цель", { exact: true });
        await expect(goal).toHaveValue("Исходная цель");
        await page.getByLabel("Название", { exact: true }).fill(`  ${project.name}  `);
        await goal.fill("Моя новая цель");
        await page.getByText("Контекст проекта", { exact: true }).click();
        await page.getByLabel("Ограничения", { exact: true }).fill("Данные хранятся локально");
        await page.getByRole("button", { name: "Сохранить", exact: true }).click();
        await expect(page.getByText(/Ваш черновик сохранён/)).toBeVisible();
        await expect(goal).toHaveValue("Моя новая цель");
        await expect(page.getByRole("button", { name: "Сохранить", exact: true })).toBeDisabled();
        await page.getByRole("button", { name: "Продолжить с моими правками" }).click();
        await expect(page.getByLabel("Название", { exact: true })).toHaveValue("Актуальное название");
        await expect(page.getByLabel("Текущие возможности", { exact: true })).toHaveValue("Каталог уже работает");
        await page.getByRole("button", { name: "Сохранить", exact: true }).click();
        await expect.poll(() => patches.length).toBe(2);
        expect(patches[0]).not.toHaveProperty("name");
        expect(patches[1]).not.toHaveProperty("name");
        expect(patches[1].description_sections).toMatchObject({
            goal: "Моя новая цель", constraints: "Данные хранятся локально",
        });
        expect(patches[1].description_sections).not.toHaveProperty("capabilities");
        expect(project.description_sections.capabilities).toBe("Каталог уже работает");
        expect(patches[1]).not.toHaveProperty("due_date");
        await expect(page.getByRole("button", { name: "Сохранить", exact: true })).toBeDisabled();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
        expect(state.errors).toEqual([]);
        await page.getByLabel("Ограничения", { exact: true }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: info.outputPath(`project-context-${viewport.name}.png`) });
    });
}

test("участник читает паспорт, редактирование доступно владельцу", async ({ page }) => {
    const state = mockState();
    state.projects[0].owner_id = 2;
    await installMockApi(page, state);
    await page.goto("/projects/DEMO/settings");
    await expect(page.getByText("Паспорт и настройки редактирует владелец проекта.")).toBeVisible();
    await expect(page.getByLabel("Цель", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Сохранить", exact: true })).toBeDisabled();
    expect(state.writes).toEqual([]);
    expect(state.errors).toEqual([]);
});
