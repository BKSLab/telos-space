import { expect, test } from "@playwright/test";
import type { DocumentListItem } from "../src/lib/types";
import { documentFixture } from "../src/components/documents/documentFixtures.test-utils";
import { installDocumentsApi } from "./support/documentsMockApi";

function uploaded(ref: string, title: string, extension: string, contentType: string, preview = false): DocumentListItem {
    return documentFixture({ ref, title, source_kind: "upload", format: extension,
        text: { ...documentFixture().text, rendition: "extracted_text" },
        original: { filename: `${title}.${extension}`, content_type: contentType, size: 10240,
            content_url: `/api/v1/documents/${ref}/content`, preview_url: preview ? `/api/v1/documents/${ref}/preview` : null } });
}

for (const width of [1440, 390]) {
    test(`миниатюры и значки форматов сохраняют компактный список ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        const items = [
            uploaded("image-one", "Схема проекта", "png", "image/png", true),
            uploaded("image-denied", "Прежний эскиз", "jpg", "image/jpeg", true),
            documentFixture({ ref: "markdown", title: "Решения встречи" }),
            uploaded("pdf", "Требования", "pdf", "application/pdf"),
            uploaded("word", "Договор", "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            uploaded("excel", "Бюджет", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            uploaded("csv", "Поставки", "csv", "text/csv"),
            uploaded("text", "Заметки", "txt", "text/plain"),
        ];
        const state = await installDocumentsApi(page, items);
        // A real raster response exercises browser decoding and the protected blob URL path.
        const jpeg = Buffer.from(await page.evaluate(() => {
            const canvas = document.createElement("canvas"); canvas.width = 144; canvas.height = 144;
            const ctx = canvas.getContext("2d")!;
            ctx.fillStyle = "#e2e6ed"; ctx.fillRect(0, 0, 144, 144);
            ctx.strokeStyle = "#526d91"; ctx.lineWidth = 5;
            ctx.beginPath(); ctx.moveTo(72, 42); ctx.lineTo(72, 76); ctx.moveTo(33, 105); ctx.lineTo(33, 76); ctx.lineTo(111, 76); ctx.lineTo(111, 105); ctx.stroke();
            ctx.fillStyle = "#526d91"; ctx.fillRect(45, 20, 54, 30);
            ctx.fillStyle = "#7b8c9d"; ctx.fillRect(12, 96, 42, 28); ctx.fillRect(90, 96, 42, 28);
            return canvas.toDataURL("image/jpeg").split(",")[1];
        }), "base64");
        let releasePreview!: () => void;
        const pendingPreview = new Promise<void>(resolve => { releasePreview = resolve; });
        const previewRequests: string[] = []; const originalRequests: string[] = [];
        page.on("request", request => { if (/\/documents\/[^/]+\/content$/.test(new URL(request.url()).pathname)) originalRequests.push(request.url()); });
        await page.route("**/api/v1/documents/*/preview", async route => {
            const path = new URL(route.request().url()).pathname;
            previewRequests.push(path);
            const headers = { "access-control-allow-origin": route.request().headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true" };
            if (path.endsWith("/image-denied/preview")) return route.fulfill({ status: 403, headers });
            await pendingPreview;
            return route.fulfill({ headers, contentType: "image/jpeg", body: jpeg });
        });
        await page.goto("/projects/DEMO/docs");
        const row = page.getByRole("button", { name: "Открыть документ Схема проекта", exact: true });
        const denied = page.getByRole("button", { name: "Открыть документ Прежний эскиз", exact: true });
        await expect(row).toBeVisible();
        const before = await row.boundingBox();
        expect(before!.height).toBe(56);
        expect(await row.locator("span").first().evaluate(element => ({ width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height }))).toEqual({ width: 36, height: 36 });
        releasePreview();
        await expect(row.locator("img")).toHaveAttribute("src", /^blob:/);
        await expect.poll(() => row.locator("img").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(144);
        expect(await row.boundingBox()).toEqual(before);
        await expect.poll(() => previewRequests.includes("/api/v1/documents/image-denied/preview")).toBe(true);
        await expect(denied.locator("img")).toHaveCount(0);
        await expect(denied.getByText("JPG", { exact: true })).toBeVisible();
        const formats = ["PNG", "JPG", "MD", "PDF", "DOCX", "XLSX", "CSV", "TXT"];
        for (const [index, item] of items.entries()) {
            const documentRow = page.getByRole("button", { name: `Открыть документ ${item.title}`, exact: true });
            const box = await documentRow.boundingBox();
            expect(box!.height).toBe(56);
            expect(box!.x).toBeGreaterThanOrEqual(0);
            expect(box!.x + box!.width).toBeLessThanOrEqual(width);
            await expect(documentRow.locator("span").first().getByText(formats[index], { exact: true })).toHaveCount(1);
            await expect(documentRow.locator("button")).toHaveCount(0);
        }
        expect(originalRequests).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`document-formats-${width}.png`), animations: "disabled" });
        await row.focus(); await page.keyboard.press("Enter");
        const dialog = page.getByRole("dialog", { name: "Документ", exact: true });
        await expect(dialog).toBeVisible();
        await expect(dialog.getByRole("link", { name: "Скачать оригинал", exact: true })).toHaveAttribute("href", /\/documents\/image-one\/content$/);
        await page.keyboard.press("Escape");
        await expect(dialog).toHaveCount(0);
        await expect(row).toBeFocused();
        expect(state.errors).toEqual([]);
    });
}
