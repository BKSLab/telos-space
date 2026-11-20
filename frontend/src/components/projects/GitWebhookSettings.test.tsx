// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import * as clipboard from "@/lib/clipboard";
import type { GitConnection } from "@/lib/projectKnowledge";
import { GitWebhookSettings } from "./GitWebhookSettings";

const connection = { id: 4, project_id: 7, revision: 3, ref: "heads/main", has_webhook_secret: false } as GitConnection;
const clients: QueryClient[] = [];
function mount(current = connection, refresh = vi.fn(async () => undefined)) {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } }); clients.push(client);
    const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const view = render(<GitWebhookSettings connection={current} refresh={refresh} />, { wrapper });
    fireEvent.click(screen.getByText("Webhook GitHub"));
    return { ...view, refresh };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.unstubAllEnvs(); });

it("показывает точный публичный URL, копирует его и предупреждает о HTTP", async () => {
    vi.stubEnv("VITE_PUBLIC_URL", "http://example.test:8081");
    const copy = vi.spyOn(clipboard, "copyText").mockResolvedValue();
    mount();
    const url = "http://example.test:8081/api/v1/projects/7/git/connections/4/events";
    expect((screen.getByLabelText("Payload URL") as HTMLInputElement).value).toBe(url);
    expect(screen.getByText(/Сейчас адрес приёмника использует HTTP/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Копировать адрес" }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith(url));
    expect(screen.queryByText("Webhook подключён")).toBeNull();
});

it("не выдумывает публичный endpoint для локальной разработки и отвергает credentials в URL", () => {
    vi.stubEnv("VITE_PUBLIC_URL", ""); vi.stubEnv("DEV", true);
    const view = mount();
    expect(screen.getByText(/Публичный адрес приёмника не задан/)).toBeTruthy();
    expect(screen.queryByLabelText("Payload URL")).toBeNull();
    vi.stubEnv("VITE_PUBLIC_URL", "https://user:password@example.test");
    view.rerender(<GitWebhookSettings connection={connection} refresh={view.refresh} />);
    expect(screen.queryByLabelText("Payload URL")).toBeNull();
});

it("сохраняет секрет с revision, очищает поле и не объявляет доставку успешной", async () => {
    const put = vi.spyOn(api, "put").mockResolvedValue({});
    const view = mount();
    fireEvent.change(screen.getByLabelText("Новый секрет webhook"), { target: { value: "random-test-secret-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить секрет" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith("/api/v1/projects/7/git/connections/4/webhook-secret", { expected_revision: 3, webhook_secret: "random-test-secret-123" }));
    await waitFor(() => expect(view.refresh).toHaveBeenCalledTimes(1));
    expect((screen.getByLabelText("Новый секрет webhook") as HTMLInputElement).value).toBe("");
    expect(screen.getByText(/Укажите тот же секрет в GitHub и проверьте доставку/)).toBeTruthy();
    view.rerender(<GitWebhookSettings connection={{ ...connection, revision: 4, has_webhook_secret: true }} refresh={view.refresh} />);
    expect(screen.queryByLabelText("Новый секрет webhook")).toBeNull();
    expect(screen.getByText("Секрет сохранён в TELOS")).toBeTruthy();
});

it("заменяет и отключает секрет явными действиями, не запрашивая его сохранённое значение", async () => {
    const put = vi.spyOn(api, "put").mockResolvedValue({});
    const get = vi.spyOn(api, "get");
    mount({ ...connection, has_webhook_secret: true });
    fireEvent.click(screen.getByRole("button", { name: "Заменить секрет" }));
    expect((screen.getByLabelText("Новый секрет webhook") as HTMLInputElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    fireEvent.click(screen.getByRole("button", { name: "Отключить приём webhook" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith(expect.any(String), { expected_revision: 3, webhook_secret: null }));
    expect(get).not.toHaveBeenCalled();
    expect(await screen.findByText(/Приём webhook отключён в TELOS/)).toBeTruthy();
});

it("при конфликте не повторяет запись автоматически и позволяет перечитать revision", async () => {
    const put = vi.spyOn(api, "put").mockRejectedValue(new ApiError(409, "Подключение изменилось"));
    const view = mount();
    fireEvent.change(screen.getByLabelText("Новый секрет webhook"), { target: { value: "test-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить секрет" }));
    expect(await screen.findByText("Подключение изменилось")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Загрузить актуальные настройки webhook" }));
    await waitFor(() => expect(view.refresh).toHaveBeenCalledTimes(1));
    expect(put).toHaveBeenCalledTimes(1);
    await waitFor(() => expect((screen.getByLabelText("Новый секрет webhook") as HTMLInputElement).value).toBe(""));
});
