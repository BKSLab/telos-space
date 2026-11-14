import type { Page } from "@playwright/test";

/** Hold only the upload response; the selected file remains private until explicit Send. */
export async function delayedDraftUpload(page: Page, filename: string) {
    const fileId = "ce0f645b-d705-42da-9872-4d0e1ad8481c";
    const uploaded: string[] = [], removed: string[] = [];
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true" };
        if (request.method() === "POST" && /\/(?:aris\/conversations\/\d+\/files|chat\/attachments)$/.test(path)) {
            uploaded.push(path);
            await held;
            return route.fulfill({ headers, status: 201, json: { id: fileId, original_name: filename, content_type: "text/plain", size: 5, size_bytes: 5, message_id: null, created_at: "2026-11-15T10:00:00Z" } });
        }
        if (request.method() === "DELETE" && path.endsWith(`/${fileId}`)) {
            removed.push(path);
            return route.fulfill({ headers, status: 204 });
        }
        return route.fallback();
    });
    return { fileId, uploaded, removed, release };
}
