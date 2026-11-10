import { expect, it } from "vitest";
import { catalogEntries, findCatalogEntries, integrationCatalog, resolveSchema, schemaDocument, schemaType } from "./integrationCatalog";
import { searchHelp } from "./helpSearch";

it("ищет методы, действия и поля схем и ведёт к конкретной карточке", () => {
    const http = findCatalogEntries("api", ["wbs", "suggestion"]).find(entry => entry.path?.endsWith("/suggestion"))!;
    expect(http.scopes).toEqual(["structure:read"]);
    const tools = findCatalogEntries("mcp", ["telos_create_task"]);
    expect(tools.some(entry => entry.name === "telos_create_task")).toBe(true);
    expect(searchHelp("telos_create_task").some(result => result.article.id === tools[0].id)).toBe(true);
    const matchingLinks = searchHelp("telos_create_task").filter(result => result.article.id.startsWith("mcp-"));
    expect(new Set(matchingLinks.map(result => result.article.title)).size).toBe(matchingLinks.length);
    expect(matchingLinks.some(result => result.article.title.startsWith("Действие · telos_create_task"))).toBe(true);
    expect(findCatalogEntries("mcp", ["request_id"]).some(entry => entry.name === "telos_execute_project_tool")).toBe(true);
    expect(searchHelp("MCP request_id").some(result => result.section.id === "mcp" && result.article.id.startsWith("mcp-"))).toBe(true);
    expect(findCatalogEntries("api", ["start_date"]).some(entry => entry.requestBody)).toBe(true);
});

it("различает доступ и публикует единые имена MCP без дублей", () => {
    expect(integrationCatalog.api.find(entry => entry.method === "GET" && entry.path === "/api/v1/users/me/api-keys")?.access).toBe("session");
    expect(integrationCatalog.mcp.every(entry => entry.name.startsWith("telos_"))).toBe(true);
    expect(integrationCatalog.actions.every(entry => entry.name.startsWith("telos_"))).toBe(true);
    const entries = [...catalogEntries("api"), ...catalogEntries("mcp")];
    expect(new Set(entries.map(entry => entry.id)).size).toBe(entries.length);
});

it("раскрывает общие и локальные схемы, не прикладывая весь каталог определений", () => {
    const name = Object.keys(integrationCatalog.schemas).find(key => integrationCatalog.schemas[key].properties)!;
    const schema = { $ref: `#/components/schemas/${name}` };
    expect(resolveSchema(schema).properties).toBeTruthy();
    expect(schemaDocument(schema).definitions[name]).toBe(integrationCatalog.schemas[name]);
    expect(Object.keys(schemaDocument({ type: "string" }).definitions)).toHaveLength(0);
    const root = { $defs: { Choice: { type: "string", enum: ["A", "B"] } } };
    expect(schemaType({ $ref: "#/$defs/Choice" }, root)).toBe('"A" | "B"');
    expect(schemaType({ anyOf: [{ type: "integer" }, { type: "null" }] })).toBe("integer | null");
    expect(schemaType({ type: "string", const: "send" })).toBe('"send"');
});
