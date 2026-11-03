import { expect, test } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";

test.setTimeout(60_000);

test("разовая задача: создание, название, статус и итоговый отчёт", async ({ page }) => {
    const state = mockState();
    await installMockApi(page, state);
    await page.goto("/my-tasks");
    await page.getByRole("button", { name: "Новая задача", exact: true }).click();
    const create = page.getByRole("dialog", { name: "Новая задача", exact: true });
    await create.getByLabel("Задача", { exact: true }).fill("Подготовить личный план");
    await create.getByRole("button", { name: "Создать задачу", exact: true }).click();
    const card = page.getByRole("dialog", { name: "Задача T-2", exact: true });
    await expect(card).toBeVisible();
    const created = state.writes.find((item) => item.path === "/tasks");
    expect(created?.body).toMatchObject({ title: "Подготовить личный план" });
    expect(created?.body).not.toHaveProperty("project_id");
    await card.getByRole("button", { name: "Переименовать задачу" }).click();
    await card.getByRole("textbox", { name: "Название задачи" }).fill("Личный план на неделю");
    await card.getByRole("textbox", { name: "Название задачи" }).press("Enter");
    await expect(card.getByRole("heading", { name: "Личный план на неделю" })).toBeVisible();
    await card.getByLabel("Статус", { exact: true }).selectOption("IN_PROGRESS");
    await expect.poll(() => state.tasks.find((task) => task.id === 201)?.status).toBe("IN_PROGRESS");
    await card.getByRole("tab", { name: "Отчёт", exact: true }).click();
    await card.getByRole("button", { name: "Добавить отчёт" }).click();
    await card.getByRole("textbox", { name: "Отчёт по задаче" }).fill("План составлен, сроки проверены.");
    await card.getByRole("button", { name: "Опубликовать отчёт", exact: true }).click();
    await expect(card.getByText("План составлен, сроки проверены.", { exact: true })).toBeVisible();
    await expect(card.getByText(/Версия 1/)).toBeVisible();
    await card.getByText("История публикаций", { exact: true }).click();
    await expect.poll(() => state.reportHistory.length).toBe(1);
    expect(state.errors).toEqual([]);
});

test("перенос разовой задачи показывает предпросмотр и требует решения об участниках", async ({ page }) => {
    const state = mockState(); state.outsider = true;
    state.report = { ...state.report, revision: 1, body_md: "Сохранённый итог" };
    await installMockApi(page, state);
    await page.goto("/my-tasks?task=200");
    const card = page.getByRole("dialog", { name: "Задача T-1", exact: true });
    await card.getByRole("button", { name: "Переместить…" }).click();
    const dialog = page.getByRole("dialog", { name: "Переместить задачу", exact: true });
    await dialog.getByLabel("Проект", { exact: true }).selectOption("1");
    await expect(dialog.getByRole("region", { name: "Что изменится" })).toContainText("T-1 станет DEMO");
    await expect(dialog.getByRole("button", { name: "Перенести", exact: true })).toBeDisabled();
    await dialog.getByLabel("Убрать их из задачи при переносе").check();
    await dialog.getByRole("button", { name: "Перенести", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Задача DEMO-20", exact: true })).toBeVisible();
    expect(state.writes.find((item) => item.path === "/tasks/200/move-to")?.body).toEqual({ project_id: 1, remove_outsiders: true });
    await page.getByRole("dialog", { name: "Задача DEMO-20", exact: true }).getByRole("tab", { name: "Отчёт", exact: true }).click();
    await expect(page.getByText("Сохранённый итог", { exact: true })).toBeVisible();
    expect(state.errors).toEqual([]);
});

test("проектная задача: предупреждение о снятии ИСР, отмена и перенос в разовые", async ({ page }) => {
    const state = mockState(); state.losses.wbs_placements = 1; state.losses.dependencies = 2;
    await installMockApi(page, state);
    await page.goto("/my-tasks?task=100");
    const card = page.getByRole("dialog", { name: "Задача DEMO-1", exact: true });
    await card.getByRole("tab", { name: "Связи", exact: true }).click();
    await card.getByRole("button", { name: "Переместить…" }).click();
    let dialog = page.getByRole("dialog", { name: "Переместить задачу", exact: true });
    await expect(dialog.getByRole("note")).toContainText("1 размещение в ИСР");
    await expect(dialog.getByRole("note")).toContainText("2 зависимости");
    await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
    expect(state.writes.filter((item) => item.path.endsWith("/move-to"))).toHaveLength(0);
    await card.getByRole("button", { name: "Переместить…" }).click();
    dialog = page.getByRole("dialog", { name: "Переместить задачу", exact: true });
    await dialog.getByRole("button", { name: "Перенести", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Задача T-20", exact: true })).toBeVisible();
    const personalCard = page.getByRole("dialog", { name: "Задача T-20", exact: true });
    await expect(personalCard.getByRole("tab", { name: "Связи", exact: true })).toHaveCount(0);
    await expect(personalCard.getByRole("tab", { name: "Основное", exact: true })).toHaveAttribute("aria-selected", "true");
    expect(state.writes.find((item) => item.path === "/tasks/100/move-to")?.body).toEqual({ personal: true });
    expect(state.errors).toEqual([]);
});
