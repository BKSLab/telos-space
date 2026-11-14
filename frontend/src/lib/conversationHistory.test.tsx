// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { AgentMessage, AgentMessageAccepted, AgentMessagePage, AgentPageContext } from "@/lib/types";
import { useArisConversations } from "./useArisConversations";
import { useProjectConversations } from "./useProjectConversations";

vi.mock("@/lib/useAuth", () => ({ useCurrentUser: () => ({ data: { id: 9 } }) }));
const clients: QueryClient[] = [];
const message = (id: number): AgentMessage => ({ id, conversation_id: 7, request_id: `request-${id}`, role: "user", content: `Сообщение ${id}`, sources: [], status: "completed", error: null, created_at: "2026-11-14T10:00:00Z" });
const usePersonal = (around: number | null) => useArisConversations(7, true, around);
const useProject = (around: number | null) => useProjectConversations(1, 7, around);

function setup(kind: "personal" | "project") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retryDelay: 0 } } });
    clients.push(client);
    const requests: { path: string; params: URLSearchParams; body: Record<string, unknown> | null }[] = [];
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input), "http://localhost");
        const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
        requests.push({ path: url.pathname, params: url.searchParams, body });
        let result: unknown = { items: [], next_offset: null };
        if (url.pathname.endsWith("/messages")) {
            if (body) {
                const accepted: AgentMessageAccepted = { user_message: message(90), assistant_message: { ...message(91), role: "assistant", message_references: [] } };
                result = accepted;
            } else {
                const page: AgentMessagePage = { items: [message(url.searchParams.has("before_id") ? 1 : url.searchParams.has("around_message_id") ? 42 : 90)], next_before_id: url.searchParams.has("around_message_id") ? 42 : null };
                result = page;
            }
        }
        return new Response(JSON.stringify(result), { status: 200, headers: { "content-type": "application/json" } });
    });
    const useChat = kind === "personal" ? usePersonal : useProject;
    const view = renderHook(({ around }: { around: number | null }) => useChat(around), { initialProps: { around: 42 as number | null }, wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
    return { ...view, requests, fetch };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it.each(["personal", "project"] as const)("%s history загружает цитируемое старое сообщение и отделяет latest от around", async kind => {
    const { result, requests, rerender } = setup(kind);
    await waitFor(() => expect(result.current.messages.data?.pages[0].items[0].id).toBe(42));
    const first = requests.find(request => request.path.endsWith("/messages"))!;
    expect(first.params.get("around_message_id")).toBe("42");
    expect(first.params.has("before_id")).toBe(false);
    await act(async () => { await result.current.messages.fetchNextPage(); });
    const before = requests.find(request => request.params.has("before_id"))!;
    expect(before.params.get("before_id")).toBe("42");
    expect(before.params.has("around_message_id")).toBe(false);
    rerender({ around: null });
    await waitFor(() => expect(result.current.messages.data?.pages[0].items[0].id).toBe(90));
});

it.each(["personal", "project"] as const)("%s send передаёт полный page_context с timezone неизменным JSON", async kind => {
    const { result, requests } = setup(kind);
    const pageContext: AgentPageContext = { route: "/projects/DEMO/whiteboard", project_key: "DEMO", task_id: 9, board_id: 2, board_selection: [{ type: "sticker", id: 3 }], timezone: "Europe/Samara" };
    await act(async () => { await result.current.send.mutateAsync({ id: 7, content: "Что обсуждали сегодня?", requestId: "stable-request", fileIds: [], pageContext }); });
    const posted = requests.find(request => request.body != null)!;
    expect(posted.path).toBe(kind === "personal" ? "/api/v1/aris/conversations/7/messages" : "/api/v1/projects/1/aris/conversations/7/messages");
    expect(posted.body).toEqual({ content: "Что обсуждали сегодня?", request_id: "stable-request", file_ids: [], page_context: pageContext });
});
