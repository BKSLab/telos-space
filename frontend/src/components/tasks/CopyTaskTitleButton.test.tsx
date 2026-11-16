// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { copyText } from "@/lib/clipboard";
import { CopyTaskTitleButton } from "./CopyTaskTitleButton";

vi.mock("@/lib/clipboard", () => ({ copyText: vi.fn() }));

afterEach(() => {
    cleanup();
    vi.mocked(copyText).mockReset();
    vi.useRealTimers();
});

it("копирует полный Unicode-текст с ключом и подтверждает успех только после завершения", async () => {
    let complete!: () => void;
    vi.mocked(copyText).mockImplementation(() => new Promise<void>(resolve => { complete = resolve; }));
    const title = `Согласовать «релиз» 🚀 — ${"длинное название / 日本語 e\u0301 ".repeat(12)}финал`;
    render(<CopyTaskTitleButton taskKey="TELOS-23" title={title} />);
    const button = screen.getByRole("button", { name: "Скопировать ключ и название" });

    button.focus();
    fireEvent.click(button);

    expect(copyText).toHaveBeenCalledExactlyOnceWith(`TELOS-23 ${title}`);
    expect(screen.getByRole("status").textContent).toBe("");
    expect(button.querySelector(".lucide-check")).toBeNull();
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    expect(copyText).toHaveBeenCalledTimes(1);
    await act(async () => { complete(); });
    expect(screen.getByRole("status").textContent).toBe("Ключ и название скопированы");
    expect(button.querySelector(".lucide-check")).toBeTruthy();
    expect((button as HTMLButtonElement).disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBe("false");
    expect(document.activeElement).toBe(button);
});

it("берёт актуальные key/title после обновления props и работает с клавиатуры", async () => {
    vi.mocked(copyText).mockResolvedValue(undefined);
    const user = userEvent.setup();
    const view = render(<CopyTaskTitleButton taskKey="TELOS-23" title="До переименования" />);
    const button = screen.getByRole("button", { name: "Скопировать ключ и название" });
    expect(button.title).toBe("Скопировать ключ и название");
    expect(button.classList.contains("touch-target")).toBe(true);
    await user.tab();
    expect(document.activeElement).toBe(button);
    await user.keyboard("{Enter}");
    expect(copyText).toHaveBeenLastCalledWith("TELOS-23 До переименования");

    view.rerender(<CopyTaskTitleButton taskKey="NEW-24" title="После переименования 🧭" />);
    await user.keyboard(" ");

    expect(copyText).toHaveBeenLastCalledWith("NEW-24 После переименования 🧭");
    expect(copyText).toHaveBeenCalledTimes(2);
});

it("показывает понятную ошибку без ложного успеха и позволяет повторить копирование", async () => {
    vi.mocked(copyText).mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("Denied")).mockResolvedValueOnce(undefined);
    const user = userEvent.setup();
    render(<CopyTaskTitleButton taskKey="TELOS-23" title="Название" />);
    const button = screen.getByRole("button", { name: "Скопировать ключ и название" });
    await user.click(button);
    expect(screen.getByRole("status").textContent).toBe("Ключ и название скопированы");

    await user.click(button);

    expect(screen.getByRole("alert").textContent).toBe("Не удалось скопировать ключ и название. Попробуйте ещё раз.");
    expect(screen.getByRole("status").textContent).toBe("");
    expect(button.querySelector(".lucide-check")).toBeNull();
    expect((button as HTMLButtonElement).disabled).toBe(false);
    await user.click(button);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByRole("status").textContent).toBe("Ключ и название скопированы");
});

it("возвращает значок копирования после краткого подтверждения", async () => {
    vi.useFakeTimers();
    vi.mocked(copyText).mockResolvedValue(undefined);
    render(<CopyTaskTitleButton taskKey="TELOS-23" title="Название" />);
    const button = screen.getByRole("button", { name: "Скопировать ключ и название" });
    await act(async () => { fireEvent.click(button); });
    expect(button.querySelector(".lucide-check")).toBeTruthy();

    act(() => { vi.advanceTimersByTime(2000); });

    expect(button.querySelector(".lucide-check")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("");
});
