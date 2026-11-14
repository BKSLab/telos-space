import { expect, test } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";

test.setTimeout(60_000);
test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test("мобильное меню открывает все разделы и возвращает фокус", async ({ page }, info) => {
    const state = mockState();
    await installMockApi(page, state);
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    await page.goto("/");
    await page.setViewportSize({ width: 320, height: 740 });
    const trigger = page.getByRole("button", { name: "Открыть меню", exact: true });
    await trigger.tap();
    const menu = page.getByRole("dialog", { name: "Меню навигации" });
    for (const label of ["Портфель", "Доски", "Мои задачи", "Календарь"]) {
        const link = menu.getByRole("navigation", { name: "Основная навигация" }).getByRole("link", { name: label, exact: true });
        await expect(link).toBeVisible();
        expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    }
    await page.screenshot({ path: info.outputPath("mobile-menu-320.png") });
    await menu.getByRole("button", { name: "Закрыть меню" }).tap();
    await expect(trigger).toBeFocused();
    await trigger.tap();
    await menu.getByRole("link", { name: "Мои задачи", exact: true }).tap();
    await expect(menu).not.toBeVisible();
    await expect(page.getByRole("heading", { name: "Мои задачи", exact: true })).toBeVisible();
    expect(state.errors).toEqual([]);
});

test("списки задач читаются без боковой прокрутки и открывают общую карточку", async ({ page }, info) => {
    const state = mockState();
    state.tasks[0].title = "Проверить длинное название задачи и согласовать итог с командой проекта";
    state.tasks[0].due_date = "2026-11-20";
    await installMockApi(page, state);
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    for (const path of ["/my-tasks", "/projects/DEMO/tasks"]) {
        await page.goto(path);
        const title = page.getByRole("button", { name: state.tasks[0].title, exact: true });
        await expect(title).toBeVisible();
        const table = page.locator(".mobile-data-table");
        expect(await table.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
        const bounds = (await title.boundingBox())!;
        expect(bounds.width).toBeGreaterThan(220);
        expect(bounds.height).toBeGreaterThanOrEqual(44);
        await page.screenshot({ path: info.outputPath(path.includes("DEMO") ? "project-tasks-mobile.png" : "my-tasks-mobile.png") });
        await title.tap();
        const dialog = page.getByRole("dialog", { name: "Задача DEMO-1", exact: true });
        await expect(dialog).toBeVisible();
        await dialog.getByRole("button", { name: "Закрыть задачу", exact: true }).tap();
    }
    expect(state.errors).toEqual([]);
});

test("установка использует фирменную иконку и одноразовый запрос браузера", async ({ page }) => {
    const state = mockState();
    await installMockApi(page, state);
    await page.route("**/api/v1/auth/me", route => route.fulfill({ json: { ...state.me, created_at: "2026-09-20T10:00:00Z" } }));
    await page.goto("/");
    await page.evaluate(() => {
        const offer = new Event("beforeinstallprompt", { cancelable: true });
        Object.assign(offer, { prompt: async () => { document.documentElement.dataset.installPrompts = String(Number(document.documentElement.dataset.installPrompts ?? 0) + 1); }, userChoice: Promise.resolve({ outcome: "dismissed" }) });
        window.dispatchEvent(offer);
    });
    await page.getByRole("button", { name: "Открыть меню" }).tap();
    await page.getByRole("dialog", { name: "Меню навигации" }).getByRole("link", { name: "На главный экран", exact: true }).tap();
    const install = page.getByRole("button", { name: "Установить приложение", exact: true });
    await expect(install).toBeVisible();
    await install.tap();
    await expect(install).not.toBeVisible();
    expect(await page.evaluate(() => document.documentElement.dataset.installPrompts)).toBe("1");
    await page.evaluate(() => window.dispatchEvent(new Event("appinstalled")));
    await expect(page.getByText("Приложение установлено", { exact: true })).toBeVisible();
    const manifest = await (await page.request.get("/site.webmanifest")).json();
    expect(manifest.display).toBe("standalone");
    expect(manifest.shortcuts.map((item: { url: string }) => item.url)).toEqual(["/my-tasks", "/chat", "/boards"]);
    for (const source of ["/icon-192.png", "/icon-512.png", "/icon-maskable-512.png"]) {
        expect(manifest.icons.some((icon: { src: string }) => icon.src === source)).toBe(true);
        expect((await page.request.get(source)).ok()).toBe(true);
    }
    expect(state.errors).toEqual([]);
});

test("основные экраны доступны в узком и горизонтальном окне", async ({ page }, info) => {
    const state = mockState();
    await installMockApi(page, state);
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    await page.route("**/api/v1/auth/me", route => route.fulfill({ json: { ...state.me, created_at: "2026-09-20T10:00:00Z" } }));
    for (const viewport of [{ width: 320, height: 740 }, { width: 740, height: 360 }]) {
        await page.setViewportSize(viewport);
        for (const [path, heading] of [["/", "Портфель"], ["/projects/new", "Новый проект"], ["/profile", "Профиль"], ["/profile?tab=keys", "Профиль"], ["/help/mcp", "MCP"], ["/projects/DEMO/team", "Команда"], ["/projects/DEMO/settings", "О проекте"], ["/projects/DEMO/docs", "Документы"]]) {
            await page.goto(path);
            const content = page.getByRole("heading", { name: heading, exact: true }).first();
            await expect(content).toBeVisible();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            const screenshotName = path.replace(/[\\/:*?"<>|]/g, "_") || "portfolio";
            await page.screenshot({ path: info.outputPath(`${screenshotName}-${viewport.width}.png`) });
        }
    }
    expect(state.errors).toEqual([]);
});

test("выбор иконки остаётся в видимой области и доступен касанием", async ({ page }, info) => {
    const state = mockState();
    await installMockApi(page, state);
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    await page.setViewportSize({ width: 320, height: 570 });
    await page.goto("/projects/new");
    const trigger = page.getByRole("button", { name: "Иконка проекта", exact: true });
    await trigger.tap();
    const picker = page.getByRole("dialog", { name: "Иконка проекта", exact: true });
    await expect(picker).toBeVisible();
    const bounds = (await picker.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.y).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(320);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(570);
    expect(await picker.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    const icon = picker.getByRole("group", { name: "Иконки набора" }).getByRole("button").first();
    expect((await icon.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await page.screenshot({ path: info.outputPath("icon-picker-mobile.png") });
    await icon.tap();
    await expect(picker).not.toBeVisible();
    await expect(trigger).toBeFocused();
    expect(state.errors).toEqual([]);
});

test("вход и регистрация прокручиваются при открытой клавиатуре", async ({ page }, info) => {
    await page.route("https://fonts.googleapis.com/**", route => route.abort());
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    await page.route("**/api/v1/auth/me", route => route.fulfill({ status: 401, json: { detail: "Требуется вход" } }));
    await page.goto("/login");
    await expect(page.getByLabel("Логин", { exact: true })).not.toBeFocused();
    await page.getByLabel("Логин", { exact: true }).tap();
    // Emulate keyboard geometry; Chromium desktop cannot show the Android keyboard.
    await page.evaluate(() => {
        Object.defineProperty(window.visualViewport!, "height", { configurable: true, value: 360 });
        window.visualViewport!.dispatchEvent(new Event("resize"));
    });
    await expect(page.locator("html")).toHaveAttribute("data-keyboard-open", "true");
    const entry = page.locator(".brand-entry");
    expect(Math.round((await entry.boundingBox())!.height)).toBe(360);
    await page.getByRole("button", { name: "Войти", exact: true }).scrollIntoViewIfNeeded();
    const login = (await page.getByRole("button", { name: "Войти", exact: true }).boundingBox())!;
    expect(login.y + login.height).toBeLessThanOrEqual(360);
    await page.goto("/register");
    await page.setViewportSize({ width: 740, height: 360 });
    await expect(page.getByLabel("Фамилия", { exact: true })).not.toBeFocused();
    const submit = page.getByRole("button", { name: "Зарегистрироваться", exact: true });
    await submit.scrollIntoViewIfNeeded();
    const box = (await submit.boundingBox())!;
    expect(box.y + box.height).toBeLessThanOrEqual(360);
    await page.screenshot({ path: info.outputPath("register-landscape.png") });
});

test("модальное окно сохраняет заголовок и действия над клавиатурой в landscape", async ({ page }, info) => {
    await page.setViewportSize({ width: 844, height: 390 });
    await installMockApi(page, mockState());
    await page.goto("/my-tasks");
    await page.getByRole("button", { name: "Новая задача", exact: true }).tap();
    const dialog = page.getByRole("dialog", { name: "Новая задача", exact: true });
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).not.toBeVisible();
    await dialog.getByLabel("Задача", { exact: true }).focus();
    await page.evaluate(() => {
        Object.defineProperty(window.visualViewport!, "height", { configurable: true, value: 180 });
        window.visualViewport!.dispatchEvent(new Event("resize"));
    });
    await expect(page.locator("html")).toHaveAttribute("data-keyboard-open", "true");
    const header = (await dialog.locator("header").first().boundingBox())!;
    const footer = (await dialog.locator("footer").last().boundingBox())!;
    expect(header.y).toBeGreaterThanOrEqual(0);
    expect(footer.y + footer.height).toBeLessThanOrEqual(180);
    await page.screenshot({ path: info.outputPath("modal-landscape-keyboard.png") });
    await dialog.getByRole("button", { name: "Отмена", exact: true }).tap();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toBeVisible();
});
