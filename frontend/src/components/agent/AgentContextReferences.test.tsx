// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { ExternalSourceReference } from "@/lib/types";
import { AgentContextReferences } from "./AgentContextReferences";

afterEach(cleanup);
const source: ExternalSourceReference = { project_id: 7, source_id: "web:example", kind: "web_page", title: "Python Docs", url: "https://docs.python.org/3/", excerpt: "Search snippet only", coverage: "search_snippet", fetched_at: "2026-11-16T12:00:00Z", is_primary: true, state: "current" };

it("показывает поисковый фрагмент отдельно от прочитанной страницы", () => {
    render(<AgentContextReferences projectId={7} external={[source]} />);
    expect(screen.getByText(/Python Docs · Поисковый фрагмент/)).toBeTruthy();
    expect(screen.getByText(/Страница не прочитана/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Открыть источник" }).getAttribute("href")).toBe("https://docs.python.org/3/");
});

it("не отображает ссылки соседнего проекта и не выполняет URL или HTML источника", () => {
    const { container } = render(<AgentContextReferences projectId={7} external={[
        { ...source, source_id: "web:other", project_id: 8, title: "Другой проект", excerpt: "Приватная цитата" },
        { ...source, url: "javascript:alert(1)", excerpt: "<script>untrusted()</script>" },
    ]} />);
    expect(screen.queryByText("Приватная цитата")).toBeNull();
    expect(screen.queryByText(/Другой проект/)).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>untrusted()</script>")).toBeTruthy();
});

it("сохраняет metadata прежнего web-наблюдения, скрывая цитату и все недоступные или старые Git-источники", () => {
    const observed: ExternalSourceReference = { ...source, state: "stale", coverage: "extracted_text", content_hash: "a".repeat(64) };
    const { container, rerender } = render(<AgentContextReferences projectId={7} external={[observed]} />);
    fireEvent.click(screen.getByText(/Python Docs · Прочитанная веб-страница/));
    expect(screen.getByText(/Сохранённое наблюдение; актуальность страницы не перепроверена/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Открыть источник" }).getAttribute("href")).toBe(source.url);
    expect(container.querySelector("time")?.getAttribute("datetime")).toBe(source.fetched_at);
    expect(screen.getByText(`SHA-256: ${observed.content_hash}`)).toBeTruthy();
    expect(screen.queryByText(source.excerpt!)).toBeNull();
    expect(screen.queryByText(/Часть источников ответа изменилась/)).toBeNull();

    const hidden: ExternalSourceReference[] = [
        { ...source, source_id: "git_file:1", kind: "git_file", state: "stale", title: "Старый приватный файл", url: "https://github.com/private/old-file", excerpt: "Скрытая приватная цитата" },
        { ...source, source_id: "git_change:1:1:1", kind: "git_change", state: "stale", title: "Старая приватная связь", url: "https://github.com/private/old-change" },
        ...(["web_page", "git_file", "git_change"] as const).map((kind, index) => ({ ...source, source_id: `unavailable:${index}`, kind, state: "unavailable" as const, title: `Недоступный источник ${index}`, url: `https://example.org/unavailable/${index}` })),
    ];
    rerender(<AgentContextReferences projectId={7} external={[observed, ...hidden]} />);
    for (const item of hidden) {
        expect(screen.queryByText(new RegExp(item.title))).toBeNull();
        expect(container.innerHTML).not.toContain(item.url);
    }
    expect(screen.queryByText("Скрытая приватная цитата")).toBeNull();
    expect(screen.getByRole("link", { name: "Открыть источник" }).getAttribute("href")).toBe(source.url);
});
