import { expect, test } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";

test.setTimeout(60_000);

test("вход и регистрация представляют TELOS SPACE на desktop и узком экране", async ({ page }, info) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("https://fonts.googleapis.com/**", route => route.abort());
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    await page.route("**/api/v1/auth/me", route => route.fulfill({ status: 401, json: { detail: "Требуется вход" } }));
    for (const width of [1440, 390, 320]) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/login");
        await expect(page).toHaveTitle("TELOS SPACE — Пространство смыслов");
        await expect(page.getByRole("heading", { name: "Вход в TELOS SPACE" })).toBeAttached();
        const logo = page.getByRole("img", { name: "TELOS SPACE — Пространство смыслов" });
        await expect(logo).toBeVisible();
        await expect(logo).toHaveJSProperty("naturalWidth", 900);
        await expect(page.getByText("Цели, знания, задачи и результаты — в одном пространстве с помощником Арисом.")).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await info.attach(`login-${width}`, { body: await page.screenshot({ path: info.outputPath(`login-${width}.png`) }), contentType: "image/png" });
        await page.getByRole("link", { name: "Зарегистрироваться" }).click();
        await expect(page.getByRole("heading", { name: "Регистрация", exact: true })).toBeVisible();
        await expect(logo).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    expect(errors).toEqual([]);
});

test("логотип доступен в полной, свёрнутой и мобильной навигации", async ({ page }, info) => {
    await installMockApi(page, mockState());
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    await page.goto("/projects");
    const logo = page.getByRole("img", { name: "TELOS SPACE", exact: true });
    await expect(logo).toBeVisible();
    await expect(logo).toHaveAttribute("src", "/brand/telos-logo.svg");
    await info.attach("sidebar-expanded", { body: await page.screenshot({ path: info.outputPath("sidebar-expanded.png") }), contentType: "image/png" });
    await page.getByRole("button", { name: "Свернуть панель", exact: true }).click();
    await expect(logo).toHaveAttribute("src", "/brand/telos-symbol.svg");
    await expect(page.getByRole("button", { name: "Развернуть панель", exact: true })).toBeVisible();
    await info.attach("sidebar-collapsed", { body: await page.screenshot({ path: info.outputPath("sidebar-collapsed.png") }), contentType: "image/png" });
    await page.setViewportSize({ width: 320, height: 740 });
    await expect(logo).toBeVisible();
    await expect(logo).toHaveAttribute("src", "/brand/telos-symbol.svg");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await info.attach("navigation-mobile", { body: await page.screenshot({ path: info.outputPath("navigation-mobile.png") }), contentType: "image/png" });
});

test("фавиконы, manifest и материалы бренда отдаются настоящими файлами", async ({ page, request }) => {
    const manifestResponse = await request.get("/site.webmanifest");
    expect(manifestResponse.ok()).toBe(true);
    const manifest = await manifestResponse.json();
    expect(manifest.name).toBe("TELOS SPACE");
    expect(manifest.icons.some((icon: { purpose: string }) => icon.purpose === "maskable")).toBe(true);
    const ico = await request.get("/favicon.ico");
    expect(ico.ok()).toBe(true);
    const icoBytes = await ico.body();
    expect(icoBytes.readUInt16LE(2)).toBe(1);
    expect(icoBytes.readUInt16LE(4)).toBe(3);
    for (const filename of ["telos-symbol.svg", "telos-symbol-mono.svg", "telos-logo.svg", "telos-logo-tagline.svg", "telos-logo-mono.svg", "presentation-cover.svg"]) {
        const response = await request.get(`/brand/${filename}`);
        expect(response.ok()).toBe(true);
        expect(response.headers()["content-type"]).toContain("image/svg+xml");
        expect(await response.text()).not.toContain("<image");
    }
    await page.goto("/login");
    const images = [
        ["/favicon-16.png", 16, 16], ["/favicon-32.png", 32, 32],
        ["/apple-touch-icon.png", 180, 180], ["/icon-192.png", 192, 192],
        ["/icon-512.png", 512, 512], ["/icon-maskable-512.png", 512, 512],
        ["/brand/aris-avatar-64.webp", 64, 64], ["/brand/aris-avatar-128.webp", 128, 128],
        ["/brand/aris-avatar-256.webp", 256, 256], ["/brand/aris-avatar-512.webp", 512, 512],
        ["/brand/aris-portrait.webp", 768, 768], ["/brand/share-cover.png", 1200, 630],
    ] as const;
    const dimensions = await page.evaluate(async paths => Promise.all(paths.map(async src => {
        const image = new Image();
        image.src = src;
        await image.decode();
        return [image.naturalWidth, image.naturalHeight];
    })), images.map(([src]) => src));
    expect(dimensions).toEqual(images.map(([, width, height]) => [width, height]));
    const preview = await page.locator('meta[property="og:image"]').getAttribute("content");
    expect(preview).toMatch(/\/brand\/share-cover\.png$/);
    expect((await request.get(preview!)).ok()).toBe(true);
    await expect(page.locator('link[rel="icon"][type="image/svg+xml"]')).toHaveAttribute("href", /favicon\.svg\?v=telos-/);
});

test("акцент следует за активной вкладкой, AI-кнопка сохраняет видимый фокус", async ({ page }, info) => {
    const state = mockState();
    await installMockApi(page, state);
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    await page.goto("/projects/DEMO/board");
    const board = page.getByRole("link", { name: "Канбан", exact: true });
    const tasks = page.getByRole("link", { name: "Задачи", exact: true });
    await expect(board).toHaveAttribute("aria-current", "page");
    expect(await board.evaluate(element => getComputedStyle(element, "::after").backgroundImage)).toContain("linear-gradient");
    await tasks.click();
    await expect(tasks).toHaveAttribute("aria-current", "page");
    await expect(board).not.toHaveAttribute("aria-current", "page");
    expect(await tasks.evaluate(element => getComputedStyle(element, "::after").backgroundImage)).toContain("linear-gradient");
    expect(await board.evaluate(element => getComputedStyle(element, "::after").content)).toBe("none");
    await page.goto("/chat");
    const question = page.getByRole("textbox", { name: "Вопрос Арису", exact: true });
    await question.fill("Помоги определить следующий шаг");
    const send = page.getByRole("button", { name: "Отправить Арису (Enter)", exact: true });
    await expect(send).toBeEnabled();
    await send.focus();
    await expect(send).toBeFocused();
    const appearance = await send.evaluate(element => {
        const style = getComputedStyle(element);
        return { outline: style.outlineStyle, width: parseFloat(style.outlineWidth), image: style.backgroundImage };
    });
    expect(appearance.outline).toBe("solid");
    expect(appearance.width).toBeGreaterThanOrEqual(2);
    expect(appearance.image).toContain("linear-gradient");
    await info.attach("aris-accent-desktop", { body: await page.screenshot({ path: info.outputPath("aris-accent-desktop.png") }), contentType: "image/png" });
    await page.setViewportSize({ width: 320, height: 740 });
    await expect(send).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await info.attach("aris-accent-mobile", { body: await page.screenshot({ path: info.outputPath("aris-accent-mobile.png") }), contentType: "image/png" });
    expect(state.writes).toEqual([]);
});
