// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { GitTreeEntry } from "@/lib/projectKnowledge";
import { RepositoryTree } from "./RepositoryTree";

const sha = "a".repeat(40);
const folder = (path: string): GitTreeEntry => ({ path, type: "tree", sha: null, mode: "040000", size: null, index_state: "metadata", index_reason: null });
const file = (path: string): GitTreeEntry => ({ path, type: "blob", sha, mode: "100644", size: 42, index_state: "ready", index_reason: null });
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

function mount(flags: { stale?: boolean; truncated?: boolean } = {}) {
    const onSelect = vi.fn();
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
        const prefix = new URL(path, "http://local").searchParams.get("prefix");
        return { items: prefix === "src" ? [folder("src/auth"), file("src/index.ts")] : prefix === "src/auth" ? [file("src/auth/login.ts")] : [folder("src"), file("README.md")], total_files: 4, next_offset: null, truncated: false, stale: false, ...flags };
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); clients.push(client);
    function Example() {
        const [directory, setDirectory] = useState("");
        const props = { project: 1, connection: 1, sha, directory, selectedPath: "", onDirectory: setDirectory, onSelect };
        return <><button onClick={() => setDirectory("src/auth")}>Restore directory</button><RepositoryTree {...props} /></>;
    }
    render(<QueryClientProvider client={client}><Example /></QueryClientProvider>);
    return { get, onSelect };
}

it("opens nested directories while preserving their ancestors and lazily requests children", async () => {
    const { get, onSelect } = mount();
    const src = await screen.findByRole("treeitem", { name: "src" });
    expect(get.mock.calls.some(([path]) => path.includes("prefix=src"))).toBe(false);
    fireEvent.click(src);
    const auth = await screen.findByRole("treeitem", { name: "auth" });
    expect(src.getAttribute("aria-expanded")).toBe("true");
    expect(auth.getAttribute("aria-level")).toBe("2");
    fireEvent.click(auth);
    const login = await screen.findByRole("treeitem", { name: "login.ts" });
    expect(login.getAttribute("aria-level")).toBe("3");
    fireEvent.click(login);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ path: "src/auth/login.ts" }));
    fireEvent.click(auth);
    await waitFor(() => expect(screen.queryByRole("treeitem", { name: "login.ts" })).toBeNull());
    expect(screen.getByRole("treeitem", { name: "src" }).getAttribute("aria-expanded")).toBe("true");
});

it("moves focus through visible descendants and returns to the parent with ArrowLeft", async () => {
    mount();
    const src = await screen.findByRole("treeitem", { name: "src" });
    src.focus(); fireEvent.keyDown(src, { key: "ArrowRight" });
    const auth = await screen.findByRole("treeitem", { name: "auth" });
    fireEvent.keyDown(src, { key: "ArrowDown" }); expect(document.activeElement).toBe(auth);
    fireEvent.keyDown(auth, { key: "ArrowLeft" }); expect(document.activeElement).toBe(src);
    fireEvent.keyDown(src, { key: "ArrowLeft" });
    expect(src.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("treeitem", { name: "auth" })).toBeNull();
});

it("does not label a historical but complete directory as incomplete", async () => {
    mount({ stale: true });
    await screen.findByRole("treeitem", { name: "README.md" });
    expect(screen.queryByText(/неполн/)).toBeNull();
});

it("reports a genuinely incomplete catalogue only once at its root", async () => {
    mount({ truncated: true });
    fireEvent.click(await screen.findByRole("treeitem", { name: "src" }));
    await screen.findByRole("treeitem", { name: "auth" });
    expect(screen.getAllByText(/Каталог этой версии загружен не полностью/)).toHaveLength(1);
});

it("reopens the active URL directory after it was locally collapsed", async () => {
    mount();
    fireEvent.click(await screen.findByRole("treeitem", { name: "src" }));
    fireEvent.click(await screen.findByRole("treeitem", { name: "auth" }));
    await screen.findByRole("treeitem", { name: "login.ts" });
    fireEvent.click(screen.getByRole("treeitem", { name: "auth" }));
    expect(screen.queryByRole("treeitem", { name: "login.ts" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Restore directory" }));
    expect(await screen.findByRole("treeitem", { name: "login.ts" })).toBeTruthy();
    expect(screen.getByRole("treeitem", { name: "auth" }).getAttribute("aria-expanded")).toBe("true");
});


it.each(["", "folder40"])("opens a directory beyond its ancestors' first page, starting at '%s'", async initialDirectory => {
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
        const prefix = new URL(path, "http://local").searchParams.get("prefix");
        return { items: prefix === "folder40" ? [file("folder40/main.py")] : Array.from({ length: 40 }, (_, index) => folder(`folder${index}`)), next_offset: prefix ? null : 40, total_files: 41, truncated: false, stale: false };
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); clients.push(client);
    function Example() {
        const [directory, setDirectory] = useState(initialDirectory);
        return <RepositoryTree project={1} connection={1} sha={sha} directory={directory} selectedPath="" onDirectory={setDirectory} onSelect={() => {}} />;
    }
    render(<QueryClientProvider client={client}><Example /></QueryClientProvider>);
    if (!initialDirectory) {
        await screen.findByRole("treeitem", { name: "folder0" });
        fireEvent.change(screen.getByRole("textbox", { name: "Файл или папка" }), { target: { value: "folder40" } });
        fireEvent.click(screen.getByRole("button", { name: "Перейти" }));
    }
    expect(await screen.findByRole("treeitem", { name: "main.py" })).toBeTruthy();
    expect(get.mock.calls.some(([path]) => path.includes("prefix=folder40"))).toBe(true);
});
