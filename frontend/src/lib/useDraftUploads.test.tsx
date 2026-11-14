// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useDraftUploads } from "./useDraftUploads";

const file = new File(["draft"], "draft.txt", { type: "text/plain" });
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(yes => { resolve = yes; });
    return { promise, resolve };
}
afterEach(cleanup);

it.each(["cancel", "scope", "unmount"])("после %s поздний результат удаляется и не присоединяется к черновику", async action => {
    const request = deferred<{ id: string }>();
    const upload = vi.fn(() => request.promise), attach = vi.fn(), discard = vi.fn().mockResolvedValue(undefined);
    const view = renderHook(({ scope }) => useDraftUploads<{ id: string }>(scope), { initialProps: { scope: "chat-one" } });
    act(() => view.result.current.add([file], { upload, attach, discard }));
    expect(upload).toHaveBeenCalledOnce();
    expect(view.result.current.blocked).toBe(true);
    if (action === "cancel") act(() => view.result.current.cancel(view.result.current.entries[0].id));
    else if (action === "scope") view.rerender({ scope: "chat-two" });
    else view.unmount();
    if (action !== "unmount") expect(view.result.current.isBlocked()).toBe(false);
    await act(async () => { request.resolve({ id: "late-file" }); });
    expect(attach).not.toHaveBeenCalled();
    expect(discard).toHaveBeenCalledExactlyOnceWith({ id: "late-file" });
});

it("готовый черновик не удаляется при уходе, а ошибка допускает повтор без публикации метаданных", async () => {
    const upload = vi.fn().mockRejectedValueOnce(new Error("Нет сети")).mockResolvedValueOnce({ id: "ready" });
    const attach = vi.fn(), discard = vi.fn();
    const view = renderHook(() => useDraftUploads<{ id: string }>("chat"));
    act(() => view.result.current.add([file], { upload, attach, discard }));
    await waitFor(() => expect(view.result.current.entries[0].status).toBe("failed"));
    expect(view.result.current.blocked).toBe(true);
    expect(attach).not.toHaveBeenCalled();
    act(() => view.result.current.retry(view.result.current.entries[0].id));
    await waitFor(() => expect(attach).toHaveBeenCalledExactlyOnceWith({ id: "ready" }));
    expect(view.result.current.blocked).toBe(false);
    view.unmount();
    expect(discard).not.toHaveBeenCalled();
});

it("созданный диалог принимает собственные pending uploads без отмены при смене draft key", async () => {
    const request = deferred<{ id: string }>();
    const attach = vi.fn(), discard = vi.fn();
    const view = renderHook(({ scope }) => useDraftUploads<{ id: string }>(scope), { initialProps: { scope: "new" } });
    act(() => view.result.current.add([file], { upload: (_, context) => { context.adoptScope("created"); return request.promise; }, attach, discard }));
    view.rerender({ scope: "created" });
    expect(view.result.current.blocked).toBe(true);
    await act(async () => { request.resolve({ id: "ready" }); });
    expect(attach).toHaveBeenCalledOnce();
    expect(discard).not.toHaveBeenCalled();
});

it("удаление блокирует Send только в своём scope и не мешает следующему чату", async () => {
    const request = deferred<void>();
    const view = renderHook(({ scope }) => useDraftUploads("first" + scope), { initialProps: { scope: "one" } });
    let removal!: Promise<void>;
    act(() => { removal = view.result.current.remove("file", () => request.promise); });
    expect(view.result.current.blocked).toBe(true);
    view.rerender({ scope: "two" });
    expect(view.result.current.blocked).toBe(false);
    await act(async () => { request.resolve(); await removal; });
    expect(view.result.current.blocked).toBe(false);
});
