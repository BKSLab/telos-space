// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import type { FileDescriptor } from "@/lib/files";
import { FileList } from "./FileList";

afterEach(cleanup);
const image: FileDescriptor = { key: "1", name: "Схема.png", url: "/protected/original", previewUrl: "/protected/preview", previewable: true, mime: "image/png", size: 1200, caption: "Согласованная версия" };

it("использует защищённое превью, показывает подпись и открывает общий просмотр с оригиналом", async () => {
    const user = userEvent.setup();
    render(<FileList files={[image]} />);
    expect(screen.getByText("Согласованная версия")).toBeTruthy();
    const thumbnail = document.querySelector("img")!;
    expect(thumbnail.getAttribute("src")).toBe("/protected/preview");
    await user.click(screen.getByRole("button", { name: "Открыть изображение Схема.png" }));
    expect(screen.getByRole("dialog", { name: "Схема.png" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Скачать" }).getAttribute("href")).toBe("/protected/original");
    expect(screen.getByAltText("Схема.png").getAttribute("src")).toBe("/protected/preview");
    fireEvent.error(screen.getByAltText("Схема.png"));
    expect(screen.getByText("Не удалось загрузить изображение для просмотра.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Скачать" })).toBeTruthy();
});

it("после ошибки миниатюры сохраняет скачивание и иконку расширения", () => {
    render(<FileList files={[image]} />);
    fireEvent.error(document.querySelector("img")!);
    expect(screen.queryByRole("button", { name: "Открыть изображение Схема.png" })).toBeNull();
    expect(screen.getByRole("link", { name: "Скачать файл Схема.png" }).getAttribute("href")).toBe("/protected/original");
    expect(screen.getByText("PNG")).toBeTruthy();
});

it("не использует оригинал вместо отсутствующего превью", () => {
    render(<FileList files={[{ ...image, previewUrl: null }, { ...image, key: "2", name: "Бюджет.xlsx", mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", previewable: false }]} />);
    expect(document.querySelectorAll("img")).toHaveLength(0);
    expect(screen.getByText("XLSX")).toBeTruthy();
});
