import { describe, expect, it } from "vitest";
import { HELP_SECTIONS } from "@/content/help";
import { helpHighlights, searchHelp } from "./helpSearch";

describe("поиск по справке", () => {
    it("ищет по всему руководству, включая шаги и термины", () => {
        expect(searchHelp("чек-лист").some(result => result.section.id === "tasks")).toBe(true);
        expect(searchHelp("baseline").some(result => result.article.id === "calendar")).toBe(true);
        expect(searchHelp("подтверждением").some(result => result.article.id === "aris-and-chats")).toBe(true);
        expect(searchHelp("как создать задачу").some(result => result.article.id === "task-card")).toBe(true);
    });
    it("объединяет слова запроса и учитывает русские буквы независимо от регистра и ё", () => {
        expect(searchHelp("MCP 401").some(result => result.article.id === "mcp-troubleshooting")).toBe(true);
        expect(searchHelp("ОТЧЕТ").some(result => result.article.id === "task-completion")).toBe(true);
        expect(searchHelp("MCP несуществующееслово")).toEqual([]);
    });
    it("выше ставит совпадение в заголовке", () => {
        expect(searchHelp("Обзор проекта")[0].article.id).toBe("project-overview");
    });
    it("обрабатывает пустые запросы и знаки без регулярных выражений от пользователя", () => {
        for (const query of ["", "  ", "[.*+?()]"]) expect(searchHelp(query)).toEqual([]);
    });
    it("подсвечивает исходный текст, сохраняя регистр, ё и специальные символы", () => {
        const text = "Итоговый отчёт <script> остаётся текстом";
        const parts = helpHighlights(text, "ОТЧЕТ");
        expect(parts.filter(part => part.match).map(part => part.text)).toEqual(["отчёт"]);
        expect(parts.map(part => part.text).join("")).toBe(text);
        expect(helpHighlights(text, "[.*+?()]")).toEqual([{ text, match: false }]);
    });
    it("сохраняет последовательность разделов и однозначные ссылки на заголовки", () => {
        expect(HELP_SECTIONS.map(section => section.id)).toEqual(["about", "projects", "tasks", "boards", "analytics", "api", "mcp"]);
        const ids = HELP_SECTIONS.flatMap(section => section.articles.map(article => article.id));
        expect(new Set(ids).size).toBe(ids.length);
        expect(HELP_SECTIONS.every(section => section.articles.length > 0 && section.articles.every(article => article.paragraphs.length > 0))).toBe(true);
    });
});
