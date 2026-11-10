// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentPendingAction } from "@/lib/types";
import { AgentConfirmModal } from "./AgentConfirmModal";

const ID = "0f1e2d3c-0000-4000-8000-000000000001";

function action(overrides: Partial<AgentPendingAction> = {}): AgentPendingAction {
    return {
        id: ID,
        tool_name: "telos_delete_task",
        title: "Удалить задачу",
        status: "pending",
        arguments: { task_id: 7, expected_updated_at: "2026-09-30T10:00:00Z" },
        preview: { task: { id: 7, key: "DEMO-7", title: "Проверить окно заказа" } },
        result: {},
        project: { id: 1, key: "DEMO", name: "Демо" },
        created_at: "2026-09-30T10:00:00Z",
        expires_at: "2026-10-01T10:00:00Z",
        ...overrides,
    };
}

function json(status: number, body: unknown) {
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function Search() {
    const location = useLocation();
    return <><span data-testid="search">{location.search}</span><span data-testid="hash">{location.hash}</span></>;
}

function renderAt(path: string) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <MemoryRouter initialEntries={[path]}>
                <AgentConfirmModal />
                <Search />
            </MemoryRouter>
        </QueryClientProvider>,
    );
}

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

describe("окно подтверждения действия агента по ссылке", () => {
    it("при закрытии сохраняет режим личного чата, задачу, диалог и hash", async () => {
        vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, action({ status: "completed" })));
        renderAt(`/projects/DEMO/conversations?conversation=17&task=42&confirm=${ID}#message-5`);
        await screen.findByRole("dialog");
        fireEvent.click(screen.getAllByRole("button", { name: "Закрыть" })[0]);
        await waitFor(() => expect(screen.getByTestId("search").textContent).toBe("?conversation=17&task=42"));
        expect(screen.getByTestId("hash").textContent).toBe("#message-5");
    });
    it("без ?confirm= ничего не показывает и не ходит в API", () => {
        const fetch = vi.spyOn(globalThis, "fetch");
        renderAt("/my-tasks");
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(fetch).not.toHaveBeenCalled();
    });

    it("показывает, что будет сделано, подтверждает без параметров в теле и закрывается", async () => {
        const fetch = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValueOnce(json(200, action()))
            .mockResolvedValueOnce(json(200, action({ status: "completed", preview: {}, result: { deleted: true, participant_decision: "approve" } })));
        renderAt(`/my-tasks?confirm=${ID}&tab=x`);
        expect(await screen.findByText("Удалить задачу")).toBeTruthy();
        expect(screen.getByText("Проект «Демо» (DEMO)")).toBeTruthy();
        expect(screen.getByText("Проверить окно заказа")).toBeTruthy();
        // Служебная версия объекта человеку не показывается.
        expect(screen.queryByText("2026-09-30T10:00:00Z")).toBeNull();
        const confirm = screen.getByRole("button", { name: "Подтвердить" });
        expect(confirm.getAttribute("title")).toMatch(/ровно с этими параметрами/);
        fireEvent.click(confirm);
        expect(await screen.findByText("Выполнено")).toBeTruthy();
        const [url, init] = fetch.mock.calls[1];
        expect(String(url)).toMatch(new RegExp(`/api/v1/aris/pending/${ID}/confirm$`));
        expect(init?.method).toBe("POST");
        expect(init?.body).toBeUndefined();
        expect(screen.queryByRole("button", { name: "Подтвердить" })).toBeNull();
        // Первая «Закрыть» — крестик в шапке окна, последняя — кнопка в подвале.
        fireEvent.click(screen.getAllByRole("button", { name: "Закрыть" }).at(-1)!);
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        expect(screen.getByTestId("search").textContent).toBe("?tab=x");
    });

    it("чужое или неизвестное действие — понятное сообщение без кнопок решения", async () => {
        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json(404, { detail: "Объект или действие недоступны в этом проекте." }));
        renderAt(`/?confirm=${ID}`);
        expect(await screen.findByText(/предложено другому пользователю/)).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Подтвердить" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Отклонить" })).toBeNull();
    });

    it("повторное решение показывает ответ сервера и перечитывает состояние", async () => {
        vi.spyOn(globalThis, "fetch")
            .mockResolvedValueOnce(json(200, action()))
            .mockResolvedValueOnce(json(409, { detail: "По этому действию решение уже принято." }))
            .mockResolvedValueOnce(json(200, action({ status: "rejected", preview: {}, result: { message: "Участник отклонил действие. Изменения не применены." } })));
        renderAt(`/?confirm=${ID}`);
        fireEvent.click(await screen.findByRole("button", { name: "Подтвердить" }));
        expect(await screen.findByText("По этому действию решение уже принято.")).toBeTruthy();
        expect(await screen.findByText("Отклонено — ничего не изменилось")).toBeTruthy();
    });

    it("просроченное нельзя подтвердить, но можно отклонить", async () => {
        const fetch = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValueOnce(json(200, action({ status: "expired" })))
            .mockResolvedValueOnce(json(200, action({ status: "rejected", preview: {} })));
        renderAt(`/?confirm=${ID}`);
        expect(await screen.findByText("Срок подтверждения истёк")).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Подтвердить" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Отклонить" }));
        expect(await screen.findByText("Отклонено — ничего не изменилось")).toBeTruthy();
        expect(String(fetch.mock.calls[1][0])).toMatch(new RegExp(`/api/v1/aris/pending/${ID}/reject$`));
    });
});
