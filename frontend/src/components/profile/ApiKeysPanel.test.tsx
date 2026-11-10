// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { personalApiKeyQueryKeys, type ApiKey, type ApiScope } from "@/lib/apiKeys";
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
        key_prefix: "ab12cd34",
        owner_user_id: 1,
        owner_name: "Тестов Тест",
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

function mockApi(keys: ApiKey[], options: { scopes?: ApiScope[]; createGate?: Promise<void> } = {}) {
    const calls: Call[] = [];
    let list = [...keys];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input), "http://localhost").pathname;
        const method = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ url, method, body });
        const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
        if (url === "/api/v1/users/me/api-keys/scopes") return json(options.scopes ?? SCOPES);
        if (url === "/api/v1/users/me/api-keys" && method === "GET") return json(list);
        if (url === "/api/v1/users/me/api-keys" && method === "POST") {
            if (options.createGate) await options.createGate;
            const created = key({ id: "k-new", label: (body as { label: string }).label, key_prefix: "0123abcd", scopes: (body as { scopes: string[] }).scopes });
            list = [...list, created];
            return json({ ...created, raw_key: "telos_key_0123abcd_00000000000000000000000000000000" }, 201); // gitleaks:allow - synthetic test key
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
    const view = render(
        <QueryClientProvider client={client}>
            <MemoryRouter>
                <ApiKeysPanel />
            </MemoryRouter>
        </QueryClientProvider>,
    );
    return { ...view, client };
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
        expect(within(row).getByText("ab12cd34…")).toBeTruthy();
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
        expect(within(dialog).getByText(/Один ключ для REST API и MCP/)).toBeTruthy();
        expect(within(dialog).queryByRole("checkbox", { name: /act_as/ })).toBeNull();
        expect(within(dialog).getByRole("checkbox", { name: /agent:ask/ })).toBeTruthy();
        await user.click(within(dialog).getByLabelText("Название"));
        await user.paste("Скрипт сверки");
        expect(within(dialog).queryByLabelText("Тип ключа")).toBeNull();
        await user.click(within(dialog).getByRole("checkbox", {name: /agent:ask/}));
        await user.click(within(dialog).getByRole("checkbox", { name: /tasks:write/ }));
        await user.click(within(dialog).getByRole("button", { name: "Выпустить" }));

        const secret = await screen.findByTestId("api-key-secret");
        expect(secret.textContent).toBe("telos_key_0123abcd_00000000000000000000000000000000");
        const post = calls.find((call) => call.method === "POST");
        expect(post?.body).toEqual({label: "Скрипт сверки", scopes: ["projects:read", "tasks:read", "agent:ask", "tasks:write"], expires_at: null});

        await user.click(screen.getByRole("button", { name: "Я сохранил ключ" }));
        await waitFor(() => expect(screen.queryByTestId("api-key-secret")).toBeNull());
        expect(await screen.findByText("0123abcd…")).toBeTruthy();
        expect(document.body.textContent).not.toContain("SECRET_ONCE");
    }, 15_000);

    it("выбирает все права, показывает частичный выбор и снимает все", async () => {
        const user = userEvent.setup();
        const calls = mockApi([]);
        renderPage();
        await screen.findByText("API-ключей пока нет");
        await user.click(screen.getByRole("button", { name: "Выпустить ключ" }));
        const dialog = within(screen.getByRole("dialog"));
        const all = dialog.getByRole("checkbox", { name: "Выбрать все" }) as HTMLInputElement;
        expect(all.checked).toBe(false);
        expect(all.indeterminate).toBe(true);
        await user.click(all);
        expect(all.checked).toBe(true);
        expect(all.indeterminate).toBe(false);
        for (const scope of SCOPES) expect((dialog.getByRole("checkbox", { name: new RegExp(scope.code) }) as HTMLInputElement).checked).toBe(true);
        await user.click(dialog.getByRole("checkbox", { name: /tasks:write/ }));
        expect(all.checked).toBe(false);
        expect(all.indeterminate).toBe(true);
        await user.click(all);
        await user.click(all);
        expect(all.checked).toBe(false);
        expect(all.indeterminate).toBe(false);
        await user.type(dialog.getByLabelText("Название"), "Все права");
        await user.click(dialog.getByRole("button", { name: "Выпустить" }));
        expect(dialog.getByRole("alert").textContent).toContain("Выберите хотя бы одно право");
        expect(calls.some((call) => call.method === "POST")).toBe(false);
        all.focus();
        await user.keyboard(" ");
        expect(dialog.queryByRole("alert")).toBeNull();
        await user.click(dialog.getByRole("button", { name: "Выпустить" }));
        await screen.findByTestId("api-key-secret");
        expect(calls.find((call) => call.method === "POST")?.body).toEqual({ label: "Все права", scopes: SCOPES.map((scope) => scope.code), expires_at: null });
    });

    it("редактирует название и права ключа", async () => {
        const user = userEvent.setup();
        const calls = mockApi([key()]);
        renderPage();
        const row = await screen.findByTestId("api-key-k-1");
        expect(within(row).queryByText(/^(Личный|Сервисный)$/)).toBeNull();
        await user.click(within(row).getByRole("button", { name: "Изменить" }));
        const dialog = within(screen.getByRole("dialog"));
        expect(dialog.queryByLabelText("Тип ключа")).toBeNull();
        const all = dialog.getByRole("checkbox", { name: "Выбрать все" }) as HTMLInputElement;
        expect(all.indeterminate).toBe(true);
        await user.click(all);
        await user.click(dialog.getByRole("button", { name: "Сохранить" }));
        await waitFor(() => expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({ label: "Мой скрипт", scopes: SCOPES.map((scope) => scope.code), expires_at: null }));
        expect(await within(await screen.findByTestId("api-key-k-1")).findByText("agent:ask")).toBeTruthy();
    });

    it("не выбирает права при пустом каталоге", async () => {
        const user = userEvent.setup();
        mockApi([], { scopes: [] });
        renderPage();
        await screen.findByText("API-ключей пока нет");
        await user.click(screen.getByRole("button", { name: "Выпустить ключ" }));
        const all = screen.getByRole("checkbox", { name: "Выбрать все" }) as HTMLInputElement;
        expect(all.disabled).toBe(true);
        expect(all.checked).toBe(false);
        expect(all.indeterminate).toBe(false);
    });

    it("блокирует выбор прав на время выпуска", async () => {
        const user = userEvent.setup();
        let release!: () => void;
        const createGate = new Promise<void>((resolve) => { release = resolve; });
        mockApi([], { createGate });
        renderPage();
        await screen.findByText("API-ключей пока нет");
        await user.click(screen.getByRole("button", { name: "Выпустить ключ" }));
        const dialog = within(screen.getByRole("dialog"));
        await user.type(dialog.getByLabelText("Название"), "Выпуск");
        await user.click(dialog.getByRole("button", { name: "Выпустить" }));
        await waitFor(() => expect((dialog.getByRole("checkbox", { name: "Выбрать все" }) as HTMLInputElement).disabled).toBe(true));
        for (const checkbox of dialog.getAllByRole("checkbox")) expect((checkbox as HTMLInputElement).disabled).toBe(true);
        await act(async () => release());
        await screen.findByTestId("api-key-secret");
    });

    it("выбор всех не выдаёт появившиеся позже права автоматически", async () => {
        const user = userEvent.setup();
        const available = [...SCOPES];
        mockApi([], { scopes: available });
        const { client } = renderPage();
        await screen.findByText("API-ключей пока нет");
        await user.click(screen.getByRole("button", { name: "Выпустить ключ" }));
        const all = screen.getByRole("checkbox", { name: "Выбрать все" }) as HTMLInputElement;
        await user.click(all);
        expect(all.checked).toBe(true);
        available.push({ code: "tasks:delete", description: "Удалять задачи" });
        await act(async () => { await client.invalidateQueries({ queryKey: personalApiKeyQueryKeys.scopes }); });
        const added = await screen.findByRole("checkbox", { name: /tasks:delete/ }) as HTMLInputElement;
        expect(added.checked).toBe(false);
        expect(all.checked).toBe(false);
        expect(all.indeterminate).toBe(true);
    });

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
