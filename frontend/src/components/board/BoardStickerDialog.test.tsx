// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api, endpoints } from "@/lib/api";
import { BoardStickerDialog } from "./BoardStickerDialog";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each(["я", "яя"])("searches the full server source for short query %s beyond the preview", async query => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // The server matched text outside the preview; the result must not be filtered again locally.
    const task = { id: 1, key: "DEMO-1", title: "Source", description_preview: "prefix", last_comment: null };
    const get = vi.spyOn(api, "get").mockResolvedValue([task]);
    render(<QueryClientProvider client={client}><BoardStickerDialog projectId={1} sticker={null} tasks={[]} tasksLoading={false} isSaving={false} error={null} onClose={() => {}} onSave={() => {}} /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("Поиск задачи"), { target: { value: query } });
    await waitFor(() => expect(get).toHaveBeenCalledWith(`${endpoints.projectTasks(1)}?search=${encodeURIComponent(query)}`, { signal: expect.any(AbortSignal) }));
    await waitFor(() => expect(screen.getByText("DEMO-1")).toBeTruthy());
    client.clear();
});
