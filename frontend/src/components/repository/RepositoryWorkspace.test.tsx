// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { GitConnection } from "@/lib/projectKnowledge";
import { RepositoryWorkspace } from "./RepositoryWorkspace";
import { repositoryKey } from "./repositoryApi";
import { useArisWidgetStore } from "@/stores/arisWidget";
import { useChatDockStore } from "@/stores/chatDock";

vi.mock("./RepositoryGraph", () => ({ default: () => <p>Карта графа</p> }));
vi.mock("./MonacoSource", () => ({ default: () => <p>Monaco</p> }));
const sha = "a".repeat(40);
const clients: QueryClient[] = [];
const connection = (id: number): GitConnection => ({ id, project_id: 7, provider: "github", repository_id: id + 100, owner: "owner", repo: `repo${id}`, name: `Repo ${id}`, purpose: "", ref: "heads/main", enabled: true, is_private: true, share_private_with_project: true, has_credential: true, has_webhook_secret: false, revision: 1, generation: 1, credential_revision: 1, state: "ready", observed_sha: sha, indexed_sha: sha, observed_at: null, indexed_at: null, checked_at: null, last_error_code: null, coverage: { total_files: 1200, cataloged_files: 1200, indexed_files: 1199, metadata_files: 1, failed_files: 0, metadata_counts: { binary_file: 1 }, truncated: false } });
function Navigation() { const location = useLocation(); const navigate = useNavigate(); return <><output data-testid="location">{location.search}</output><button onClick={() => navigate(-1)}>History back</button><button onClick={() => navigate("?view=code&code_view=search&code_q=restored")}>Restore search</button></>; }
function mount(search = "?view=code") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); clients.push(client);
    client.setQueryData(queryKeys.currentUser, { id: 1 });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/projects/DEMO/knowledge${search}`]}><RepositoryWorkspace projectId={7} /><Navigation /></MemoryRouter></QueryClientProvider>);
    return client;
}
function respond(path: string) {
    const url = new URL(path, "http://local"); const id = Number(url.pathname.match(/connections\/(\d+)/)?.[1] ?? 0);
    if (/\/connections$/.test(url.pathname)) { const offset = Number(url.searchParams.get("offset")); return { items: Array.from({ length: offset >= 20 ? 3 : 10 }, (_, index) => connection(offset + index + 1)), total: offset >= 20 ? 23 : null, next_offset: offset >= 20 ? null : offset + 10 }; }
    if (/\/connections\/\d+$/.test(url.pathname)) return connection(id);
    if (url.pathname.endsWith("/snapshots")) return { items: [], next_offset: null };
    if (url.pathname.endsWith("/tree")) return { commit_sha: sha, items: [{ path: "src/auth.py", type: "blob", mode: "100644", sha, size: 40, index_state: "ready", index_reason: null }], total_files: 1200, next_offset: null, truncated: false, stale: false };
    if (url.pathname.endsWith("/symbols")) return { items: [], next_offset: null, analyzer_version: "v1", coverage: {} };
    if (url.pathname.endsWith("/search")) return { items: [], partial: false };
    if (url.pathname.endsWith("/file")) return { path: "src/auth.py", commit_sha: sha, line_start: 55, line_end: 56, offset: 0, next_offset: 8000, total_chars: 9000, text: `repo${id}: Привет\r\n<script>window.unsafe=true</script>`, source_url: "https://github.com/owner/repo/blob/sha/src/auth.py", stale: false, partial: false };
    if (url.pathname.endsWith("/aris/conversations")) return { items: [{ id: 15 }], next_offset: null };
    throw new Error(`Unexpected path: ${path}`);
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); useArisWidgetStore.setState({ drafts: {}, conversations: {} }); useChatDockStore.setState({ panel: null }); });

it("pages beyond twenty repositories and opens a pinned source from the second page", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async path => respond(path)); mount();
    expect(await screen.findByRole("button", { name: "Открыть Repo 10" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Дальше" }));
    await screen.findByRole("button", { name: "Открыть Repo 20" });
    fireEvent.click(screen.getByRole("button", { name: "Дальше" }));
    fireEvent.click(await screen.findByRole("button", { name: "Открыть Repo 23" }));
    fireEvent.click(await screen.findByRole("treeitem", { name: /src\/auth.py/ }));
    await screen.findByLabelText("Код src/auth.py");
    expect(get.mock.calls.some(([path, options]) => path.includes("connections/23/file") && path.includes(`sha=${sha}`) && options?.signal instanceof AbortSignal)).toBe(true);
    expect(get.mock.calls.find(([path]) => path.includes("/tree"))?.[0]).toContain("children=true");
    expect(screen.getByTestId("location").textContent).toContain("connection=23");
    expect((window as unknown as { unsafe?: boolean }).unsafe).toBeUndefined();
});

it("restores range and repository on Back, without leaking the previous source", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => respond(path));
    mount(`?view=code&connection=1&sha=${sha}&path=src%2Fauth.py&line=55&end=56&kind=task_report`);
    await screen.findByText(/repo1: Привет/);
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("line_start=55"), expect.objectContaining({ signal: expect.any(AbortSignal) }));
    fireEvent.change(screen.getByLabelText("Репозиторий", { exact: true }), { target: { value: "2" } });
    await waitFor(() => expect(screen.queryByText(/repo1: Привет/)).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "History back" }));
    await screen.findByText(/repo1: Привет/);
    expect(screen.getByTestId("location").textContent).toContain("kind=task_report");
    fireEvent.click(screen.getByRole("button", { name: "Читать дальше" }));
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(expect.stringContaining("offset=15"), expect.anything()));
});

it("purges every cached range of a revoked repository and unmounts its panes", async () => {
    let denied = false;
    vi.spyOn(api, "get").mockImplementation(async path => { if (denied && path.includes("/file")) throw new ApiError(403, "Доступ отозван"); return respond(path); });
    const client = mount(`?view=code&connection=1&sha=${sha}&path=src%2Fauth.py`);
    await screen.findByText(/repo1: Привет/); denied = true;
    await client.invalidateQueries({ queryKey: repositoryKey(7, 1) });
    await screen.findByText(/Прочитанные данные удалены из кэша/);
    expect(screen.queryByText(/repo1: Привет/)).toBeNull();
    await waitFor(() => expect(client.getQueryCache().findAll({ queryKey: repositoryKey(7, 1) }).every(query => query.state.data === undefined)).toBe(true));
});

it("adds component identifiers to the existing private draft without sending a message", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => respond(path)); const post = vi.spyOn(api, "post");
    mount(`?view=code&connection=1&sha=${sha}&path=src%2Fauth.py&line=55&end=56`);
    fireEvent.click(await screen.findByRole("button", { name: "Спросить о компоненте" }));
    await waitFor(() => expect(useArisWidgetStore.getState().drafts["1:15"]?.content).toContain(`SHA=${sha}`));
    expect(useArisWidgetStore.getState().drafts["1:15"]?.content).toContain("строки 55–56");
    expect(useChatDockStore.getState().panel).toBe("personal"); expect(post).not.toHaveBeenCalled();
});

it("aborts the old repository's in-flight file when switching sources", async () => {
    let signal: AbortSignal | undefined;
    vi.spyOn(api, "get").mockImplementation(async (path, options) => {
        if (path.includes("connections/1/file")) { signal = options?.signal ?? undefined; return new Promise(() => {}); }
        return respond(path);
    }); mount(`?view=code&connection=1&sha=${sha}&path=src%2Fauth.py`);
    await waitFor(() => expect(signal).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Репозиторий", { exact: true }), { target: { value: "2" } });
    await waitFor(() => expect(signal?.aborted).toBe(true));
});

it("treats repository cursors as opaque and returns to the visited cursor", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => {
        const url = new URL(path, "http://local");
        if (/\/connections$/.test(url.pathname)) { const cursor = Number(url.searchParams.get("offset")); return { items: [connection(1)], total: null, next_offset: cursor === 0 ? 205 : cursor === 205 ? 900 : null }; }
        return respond(path);
    }); mount();
    fireEvent.click(await screen.findByRole("button", { name: "Дальше" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toContain("repo_offset=205"));
    fireEvent.click(await screen.findByRole("button", { name: "Дальше" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toContain("repo_offset=900"));
    fireEvent.click(await screen.findByRole("button", { name: "Назад" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toContain("repo_offset=205"));
});

it("restores search input from history without overwriting it with the previous draft", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => respond(path));
    mount("?view=code&code_view=search&code_q=before");
    await screen.findByRole("searchbox");
    fireEvent.click(screen.getByRole("button", { name: "Restore search" }));
    await waitFor(() => expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("restored"));
    await new Promise(resolve => setTimeout(resolve, 450));
    expect(screen.getByTestId("location").textContent).toContain("code_q=restored");
});

it("purges connection metadata and every source when the project list returns forbidden", async () => {
    let denied = false;
    vi.spyOn(api, "get").mockImplementation(async path => { if (denied && /\/connections\?/.test(path)) throw new ApiError(403, "Проект недоступен"); return respond(path); });
    const client = mount(`?view=code&connection=1&sha=${sha}&path=src%2Fauth.py`);
    await screen.findByText(/repo1: Привет/); denied = true;
    await client.invalidateQueries({ queryKey: repositoryKey(7, "connections") });
    await screen.findByText(/Доступ к исходникам изменился/);
    expect(screen.queryByRole("option", { name: "Repo 1" })).toBeNull();
    expect(screen.queryByText(/repo1: Привет/)).toBeNull();
});
