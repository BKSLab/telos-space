// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { FileTypeIcon } from "./FileTypeIcon";

afterEach(cleanup);

it("различает PDF, Markdown, Word, таблицу и текст формой, сохраняя подпись формата", () => {
    const files = [
        { name: "Условия.pdf", mime: "application/pdf", label: "PDF" },
        { name: "Решения.md", mime: "text/markdown", label: "MD" },
        { name: "Договор.docx", mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", label: "DOCX" },
        { name: "Бюджет.xlsx", mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", label: "XLSX" },
        { name: "Заметки.txt", mime: "text/plain", label: "TXT" },
    ];
    const shapes = files.map(file => {
        const { container, unmount } = render(<FileTypeIcon file={file} className="h-9 w-9" />);
        expect(container.textContent).toBe(file.label);
        const icon = container.firstElementChild!;
        expect(icon.getAttribute("aria-hidden")).toBe("true");
        expect(icon.className).toContain("h-9 w-9");
        expect(icon.className).not.toMatch(/\bborder\b|\bbg-/);
        const shape = container.querySelector("svg")!.innerHTML;
        unmount();
        return shape;
    });
    expect(new Set(shapes).size).toBe(files.length);
});

it("узнаёт все поддерживаемые таблицы, включая XLSM с фактическим MIME", () => {
    const extensions = ["csv", "xls", "xlsm", "xlsx"];
    const shapes = extensions.map(extension => {
        const { container, unmount } = render(<FileTypeIcon file={{ name: `Бюджет.${extension}`, mime: extension === "xlsm" ? "application/vnd.ms-excel.sheet.macroEnabled.12" : null }} />);
        expect(container.textContent).toBe(extension.toUpperCase());
        const shape = container.querySelector("svg")!.innerHTML;
        unmount();
        return shape;
    });
    expect(new Set(shapes).size).toBe(1);
});

it.each(["avif", "bmp", "gif", "jpeg", "jpg", "png", "webp"])("сохраняет значок изображения и подпись %s без миниатюры", extension => {
    const { container } = render(<FileTypeIcon file={{ name: `Схема.${extension}`, mime: `image/${extension}` }} />);
    expect(container.textContent).toBe(extension.toUpperCase());
    expect(container.querySelector("svg circle")).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
});

it("различает архив, презентацию и неизвестный формат", () => {
    const shapes = ["zip", "pptx", "unknown"].map(extension => {
        const { container, unmount } = render(<FileTypeIcon file={{ name: `Файл.${extension}`, mime: null }} />);
        expect(container.textContent).toBe(extension.slice(0, 5).toUpperCase());
        const shape = container.querySelector("svg")!.innerHTML;
        unmount();
        return shape;
    });
    expect(new Set(shapes).size).toBe(3);
});
