import { expect, test } from "@playwright/test";
import { installMockApi, mockState } from "./support/calendarMockApi";

test.setTimeout(60_000);

test("личный календарь, портфель и выпуск с отзывом ICS", async ({ page }) => {
    const state = mockState();
    await installMockApi(page, state);
    await page.goto("/calendar?view=grid&scale=month");
    await expect(page.getByRole("tab", { name: "Мои задачи", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText("Личная подготовка к поездке", { exact: true }).first()).toBeVisible();
    const subscribe = page.getByRole("button", { name: "Подписка на календарь в Outlook", exact: true });
    await subscribe.click();
    const panel = page.getByRole("dialog", { name: "Подписка на календарь в Outlook", exact: true });
    await panel.getByRole("button", { name: "Создать ссылку", exact: true }).click();
    await expect(panel.getByRole("textbox", { name: "Ссылка на календарь" })).toHaveValue("http://tracker.test/api/v1/ics/cal_demo.ics");
    await page.keyboard.press("Escape");
    await subscribe.click();
    page.once("dialog", (dialog) => dialog.accept());
    await panel.getByRole("button", { name: "Отозвать", exact: true }).click();
    await expect(panel.getByRole("button", { name: "Создать ссылку", exact: true })).toBeVisible();
    expect(state.icsIssued).toBe(false);
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: "Проекты", exact: true }).click();
    await expect(page).toHaveURL(/tab=portfolio/);
    await expect(page.getByRole("button", { name: /Запуск портала заказов: выполнено 25%/ })).toBeVisible();
    expect(state.writes.filter((write) => write.path === "/users/me/calendar/ics-token").map((write) => write.method)).toEqual(["POST", "DELETE"]);
    expect(state.errors).toEqual([]);
});
