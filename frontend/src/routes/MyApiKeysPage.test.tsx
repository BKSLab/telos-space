// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiKey } from "@/lib/apiKeys";
import { ApiKeysPanel } from "@/components/profile/ApiKeysPanel";

const SCOPES = [
    { code: "projects:read", description: "Читать проекты" },
    { code: "tasks:read", description: "Читать задачи" },
    { code: "tasks:write", description: "Создавать и менять задачи" },
    { code: "agent:ask", description: "Обращаться к помощнику" },
];

function key(overrides: Partial<ApiKey> = {}): ApiKey {
    return {
        id: "k-1",
        label: "Мой скрипт",
        key_prefix: "ttk_ab12",
        owner_user_id: 1,
        owner_name: "Тестов Тест",
        kind: "personal",
        scopes: ["projects:read", "tasks:read"],
        expires_at: null,
        is_active: true,
        revoked_at: null,
        revoked_by_name: null,
        created_by_name: "Тестов Тест",
        created_at: "2026-10-01T10:00:00Z",
        updated_at: "2026-10-01T10:00:00Z",
        last_used_at: null,
        ...overrides,
    };
}

type Call = { url: string; method: string; body: unknown };

function mockApi(keys: ApiKey[]) {
    const calls: Call[] = [];
    let list = [...keys];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input), "http://localhost").pathname;
        const method = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ url, method, body });
        const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
        if (url === "/api/v1/users/me/api-keys/scopes") return json(SCOPES);
        if (url === "/api/v1/users/me/api-keys" && method === "GET") return json(list);
        if (url === "/api/v1/users/me/api-keys" && method === "POST") {
            const created = key({ kind: (body as {kind:"personal"|"service"}).kind, id: "k-new", label: (body as { label: string }).label, key_prefix: "ttk_new0", scopes: (body as { scopes: string[] }).scopes });
            list = [...list, created];
            return json({ ...created, raw_key: "ttk_new0_SECRET_ONCE" }, 201); // gitleaks:allow - synthetic test key
        }
        if (url.startsWith("/api/v1/users/me/api-keys/") && method === "PATCH") {
            list = list.map((item) => (url.endsWith(item.id) ? { ...item, ...(body as object), revoked_at: "2026-10-05T08:00:00Z" } : item));
            return json(list.find((item) => url.endsWith(item.id)));
        }
        return json({ detail: "не найдено" }, 404);
    });
    return calls;
}

function renderPage() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <MemoryRouter>
                <ApiKeysPanel />
            </MemoryRouter>
        </QueryClientProvider>,
    );
}

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

describe("страница «Мои API-ключи»", () => {
    it("список: метка, префикс, права чипами, статус", async () => {
        mockApi([key(), key({ id: "k-2", label: "Старый", is_active: false, revoked_at: "2026-10-02T10:00:00Z" })]);
        renderPage();
        const row = await screen.findByTestId("api-key-k-1");
        expect(within(row).getByText("Мой скрипт")).toBeTruthy();
        expect(within(row).getByText("ttk_ab12…")).toBeTruthy();
        expect(within(row).getByText("tasks:read")).toBeTruthy();
        expect(within(row).getByText("Действует")).toBeTruthy();
        const revoked = screen.getByTestId("api-key-k-2");
        expect(within(revoked).getByText("Отозван")).toBeTruthy();
        expect(within(revoked).queryByRole("button", { name: "Отозвать" })).toBeNull();
    });

    it("выпуск: без владельца в запросе, права помощника доступны, секрет показан один раз", async () => {
        const user = userEvent.setup();
        const calls = mockApi([]);
        renderPage();
        await screen.findByText("API-ключей пока нет");
        await user.click(screen.getByRole("button", { name: /Выпустить ключ/ }));

        const dialog = await screen.findByRole("dialog");
        expect(within(dialog).queryByRole("checkbox", { name: /act_as/ })).toBeNull();
        expect(within(dialog).getByRole("checkbox", { name: /agent:ask/ })).toBeTruthy();
        await user.click(within(dialog).getByLabelText("Название"));
        await user.paste("Скрипт сверки");
        await user.selectOptions(within(dialog).getByLabelText("Тип ключа"), "service");
        await user.click(within(dialog).getByRole("checkbox", {name: /agent:ask/}));
        await user.click(within(dialog).getByRole("checkbox", { name: /tasks:write/ }));
        await user.click(within(dialog).getByRole("button", { name: "Выпустить" }));

        const secret = await screen.findByTestId("api-key-secret");
        expect(secret.textContent).toBe("ttk_new0_SECRET_ONCE");
        const post = calls.find((call) => call.method === "POST");
        expect(post?.body).toEqual({kind: "service", label: "Скрипт сверки", scopes: ["projects:read", "tasks:read", "agent:ask", "tasks:write"], expires_at: null});

        await user.click(screen.getByRole("button", { name: "Я сохранил ключ" }));
        await waitFor(() => expect(screen.queryByTestId("api-key-secret")).toBeNull());
        expect(await screen.findByText("ttk_new0…")).toBeTruthy();
        expect(document.body.textContent).not.toContain("SECRET_ONCE");
    }, 15_000);

    it("отзыв — после подтверждения, PATCH is_active=false", async () => {
        const user = userEvent.setup();
        const calls = mockApi([key()]);
        renderPage();
        const row = await screen.findByTestId("api-key-k-1");
        await user.click(within(row).getByRole("button", { name: "Отозвать" }));
        const dialog = await screen.findByRole("dialog");
        expect(calls.some((call) => call.method === "PATCH")).toBe(false);
        await user.click(within(dialog).getByRole("button", { name: "Отозвать" }));
        await waitFor(() => expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({ is_active: false }));
        expect(calls.find((call) => call.method === "PATCH")?.url).toBe("/api/v1/users/me/api-keys/k-1");
        expect(await within(await screen.findByTestId("api-key-k-1")).findByText("Отозван")).toBeTruthy();
    });
});
