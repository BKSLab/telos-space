// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { GitHubAccountPanel } from "@/components/profile/GitHubAccountPanel";
import { api, ApiError } from "@/lib/api";
import { githubPath } from "@/lib/github";
import { NewProjectPage } from "@/routes/NewProjectPage";
import { GitHubRepositoryPicker } from "./GitHubRepositoryPicker";
import { ProjectGitSources } from "./ProjectGitSources";

function mount(child: React.ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    render(<QueryClientProvider client={client}><MemoryRouter>{child}</MemoryRouter></QueryClientProvider>);
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each([null, { id: 7, login: "octocat" }])("предлагает только настройки при отсутствующем или просроченном токене", async account => {
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
        if (path === githubPath) return { account };
        throw new ApiError(409, "Подключите действующий токен GitHub в профиле.");
    });
    mount(<GitHubRepositoryPicker value={[]} onChange={vi.fn()} />);
    expect((await screen.findByRole("link", { name: "Настроить доступ к GitHub" })).getAttribute("href")).toBe("/profile?tab=github");
    expect(screen.queryByLabelText("Ссылка на репозиторий GitHub")).toBeNull();
    expect(get).toHaveBeenCalledTimes(account ? 2 : 1);
});

it("сохраняет, заменяет и удаляет токен в профиле", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ account: null });
    const put = vi.spyOn(api, "put")
        .mockResolvedValueOnce({ account: { id: 7, login: "octocat" } })
        .mockResolvedValueOnce({ account: { id: 8, login: "other" } });
    const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined);
    mount(<GitHubAccountPanel />);
    const token = await screen.findByLabelText("Токен GitHub") as HTMLInputElement;
    expect(token.type).toBe("password");
    fireEvent.change(token, { target: { value: "synthetic" } });
    fireEvent.click(screen.getByRole("button", { name: "Подключить GitHub" }));
    await screen.findByText("Аккаунт GitHub: octocat");
    expect(put).toHaveBeenNthCalledWith(1, githubPath, { token: "synthetic" });
    expect(token.value).toBe("");
    fireEvent.change(token, { target: { value: "synthetic-replacement" } });
    fireEvent.click(screen.getByRole("button", { name: "Заменить токен" }));
    await screen.findByText("Аккаунт GitHub: other");
    expect(put).toHaveBeenNthCalledWith(2, githubPath, { token: "synthetic-replacement" });
    fireEvent.click(screen.getByRole("button", { name: "Удалить токен" }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Удалить токен" }));
    await screen.findByText("Аккаунт GitHub не подключён.");
    expect(remove).toHaveBeenCalledWith(githubPath);
});

it("в настройках выбирает репозитории с нескольких страниц, требует согласия и передаёт ref", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => {
        if (path === githubPath) return { account: { id: 7, login: "octocat" } };
        if (path.endsWith("page=1")) return { repositories: [{ id: 1, owner: "org", name: "public", default_branch: "main", private: false }], next_page: 2 };
        if (path.endsWith("page=2")) return { repositories: [{ id: 2, owner: "org", name: "private", default_branch: "develop", private: true }], next_page: null };
        return { items: [], total: 0, next_offset: null };
    });
    const post = vi.spyOn(api, "post").mockResolvedValue({});
    mount(<ProjectGitSources projectId={7} canEdit />);
    fireEvent.click(screen.getByRole("button", { name: "Добавить репозитории" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "org/public" }));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "private" } });
    expect(screen.queryByRole("checkbox", { name: "org/public" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Загрузить ещё" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /org\/private/ }));
    const connect = screen.getByRole("button", { name: "Подключить выбранные" }) as HTMLButtonElement;
    expect(connect.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /Разрешаю команде/ }));
    fireEvent.change(screen.getByPlaceholderText("develop"), { target: { value: "heads/release" } });
    fireEvent.click(connect);
    await screen.findByText("Подключено 2 из 2.");
    expect(post.mock.calls).toEqual([
        ["/api/v1/projects/7/git/connections", { owner: "org", repo: "public", name: "org/public", purpose: "", ref: null, share_private_with_project: false }],
        ["/api/v1/projects/7/git/connections", { owner: "org", repo: "private", name: "org/private", purpose: "", ref: "heads/release", share_private_with_project: true }],
    ]);
});

it("создаёт проект один раз и после лимита повторяет только неподключённые репозитории", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => {
        if (path === githubPath) return { account: { id: 7, login: "octocat" } };
        if (path.includes("/repositories?")) throw new ApiError(503, "Каталог временно недоступен");
        if (path.endsWith("/defaults")) return { stages: [{ name: "Готово", color: "#123456", is_done_stage: true }] };
        return { id: 1, username: "tester", first_name: "Test", last_name: "User" };
    });
    let rateLimited = false;
    const post = vi.spyOn(api, "post").mockImplementation(async (path, body) => {
        if (path === "/api/v1/projects") return { id: 42, key: "TEST" };
        if ((body as { repo: string }).repo === "second" && !rateLimited) {
            rateLimited = true;
            throw new ApiError(429, "rate_limited", "1");
        }
        return {};
    });
    mount(<NewProjectPage />);
    fireEvent.change(screen.getByLabelText("Код"), { target: { value: "TEST" } });
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Тест проекта" } });
    fireEvent.click(screen.getByRole("button", { name: "Выбрать репозитории" }));
    await screen.findByText("Не удалось загрузить репозитории GitHub.");
    for (const repo of ["first", "second", "third"]) {
        fireEvent.change(screen.getByLabelText("Ссылка на репозиторий GitHub"), { target: { value: `https://github.com/org/${repo}.git` } });
        fireEvent.click(screen.getByRole("button", { name: "Добавить по ссылке" }));
    }
    const create = screen.getByRole("button", { name: "Создать проект" }) as HTMLButtonElement;
    await waitFor(() => expect(create.disabled).toBe(false));
    fireEvent.click(create);
    await screen.findByText("Проект создан");
    await screen.findByText("Подключено 1 из 3.");
    const retry = screen.getByRole("button", { name: "Повторить оставшиеся подключения" }) as HTMLButtonElement;
    expect(retry.disabled).toBe(true);
    await screen.findByText(/GitHub ограничил запросы/);
    await waitFor(() => expect(retry.disabled).toBe(false), { timeout: 2500 });
    expect(post).toHaveBeenCalledTimes(3);
    fireEvent.click(retry);
    await screen.findByText("Подключено 3 из 3.");
    expect(post.mock.calls.map(([path, body]) => [path, (body as { repo?: string }).repo])).toEqual([
        ["/api/v1/projects", undefined],
        ["/api/v1/projects/42/git/connections", "first"],
        ["/api/v1/projects/42/git/connections", "second"],
        ["/api/v1/projects/42/git/connections", "second"],
        ["/api/v1/projects/42/git/connections", "third"],
    ]);
});
