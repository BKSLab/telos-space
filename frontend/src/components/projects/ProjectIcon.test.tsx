// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ProjectIcon, type ProjectIconSource } from "./ProjectIcon";

const project: ProjectIconSource = { id: 7, color: "#7299cc", icon: null };
afterEach(cleanup);

it("показывает загруженный логотип раньше выбранной иконки", () => {
    render(<ProjectIcon project={{ ...project, icon: "lucide:rocket", icon_image_version: "v1" }} size={36} />);
    const image = screen.getByTestId("project-icon-image");
    expect(image.getAttribute("src")).toContain("/api/v1/projects/7/icon-image?v=v1");
    expect(image.getAttribute("width")).toBe("36");
    expect(image.getAttribute("aria-hidden")).toBe("true");
    expect(screen.queryByTestId("project-icon-lucide")).toBeNull();
});

it("при ошибке логотипа возвращается к сохранённой иконке", () => {
    render(<ProjectIcon project={{ ...project, icon: "lucide:rocket", icon_image_version: "v1" }} />);
    fireEvent.error(screen.getByTestId("project-icon-image"));
    expect(screen.getByTestId("project-icon-lucide").getAttribute("data-icon")).toBe("rocket");
    expect(screen.queryByTestId("project-icon-default")).toBeNull();
});

it("сохраняет legacy-эмодзи, в том числе после ошибки логотипа", () => {
    const { rerender } = render(<ProjectIcon project={{ ...project, icon: "🚀" }} />);
    expect(screen.getByTestId("project-icon-emoji").textContent).toBe("🚀");
    rerender(<ProjectIcon project={{ ...project, icon: "🚀", icon_image_version: "v1" }} />);
    fireEvent.error(screen.getByTestId("project-icon-image"));
    expect(screen.getByTestId("project-icon-emoji").textContent).toBe("🚀");
});

it.each([null, "", "  ", "lucide:missing-icon"])("показывает «Пик и цель» при недоступной иконке %s", icon => {
    render(<ProjectIcon project={{ ...project, icon }} />);
    const fallback = screen.getByTestId("project-icon-default");
    expect(fallback.getAttribute("data-icon")).toBe("peak-goal");
    expect(fallback.style.getPropertyValue("--project-icon-color")).toBe(project.color);
    expect(fallback.getAttribute("aria-hidden")).toBe("true");
});

it("использует «Пик и цель» после ошибки логотипа без сохранённой иконки", () => {
    render(<ProjectIcon project={{ ...project, icon_image_version: "v1" }} />);
    fireEvent.error(screen.getByTestId("project-icon-image"));
    expect(screen.getByTestId("project-icon-default")).toBeTruthy();
});

it("повторяет загрузку новой версии после ошибки предыдущей", () => {
    const { rerender } = render(<ProjectIcon project={{ ...project, icon_image_version: "v1" }} />);
    fireEvent.error(screen.getByTestId("project-icon-image"));
    rerender(<ProjectIcon project={{ ...project, icon_image_version: "v2" }} />);
    expect(screen.getByTestId("project-icon-image").getAttribute("src")).toContain("v=v2");
    expect(screen.queryByTestId("project-icon-default")).toBeNull();
});

it("ошибка другого проекта с той же версией не скрывает текущий логотип", () => {
    const { rerender } = render(<ProjectIcon project={{ ...project, icon_image_version: "v1" }} />);
    fireEvent.error(screen.getByTestId("project-icon-image"));
    rerender(<ProjectIcon project={{ ...project, id: 8, icon_image_version: "v1" }} />);
    expect(screen.getByTestId("project-icon-image").getAttribute("src")).toContain("/projects/8/icon-image");
});

it.each([16, 24, 28, 32, 36])("сохраняет заданный слот %i px у стандартного знака", size => {
    render(<ProjectIcon project={project} size={size} />);
    const fallback = screen.getByTestId("project-icon-default");
    expect(fallback.style.width).toBe(`var(--project-icon-size, ${size}px)`);
    expect(fallback.style.height).toBe(`var(--project-icon-size, ${size}px)`);
});
