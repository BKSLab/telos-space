import { describe, expect, it } from "vitest";
import { isProjectFileProcessing, projectFileId, projectFilesPollInterval, taskFilesPollInterval } from "./projectFiles";
import type { FileExtractionStatus, FileIndexStatus, ProjectFile } from "./types";

const file = (extraction_status: FileExtractionStatus, index_status: FileIndexStatus) => ({ extraction_status, index_status }) as ProjectFile;

describe("обновление состояний файлов", () => {
    it.each([
        ["pending", "pending"], ["processing", "pending"], ["ready", "pending"], ["ready", "processing"], ["pending", "disabled"], ["processing", "disabled"],
    ] as const)("ждёт завершения %s/%s", (extraction, index) => {
        expect(isProjectFileProcessing(file(extraction, index))).toBe(true);
        expect(projectFilesPollInterval([file(extraction, index)], false)).toBe(2000);
    });

    it.each([
        ["ready", "ready"], ["failed", "disabled"], ["empty", "disabled"], ["unsupported", "disabled"], ["ready", "failed"],
        ["disabled", "disabled"],
    ] as const)("останавливается в конечном состоянии %s/%s", (extraction, index) => {
        expect(projectFilesPollInterval([file(extraction, index)], false)).toBe(false);
    });

    it("локальный поиск продолжает обрабатываться при выключенных знаниях", () => {
        expect(projectFilesPollInterval([{ ...file("ready", "disabled"), fts_status: "pending" }], false)).toBe(2000);
        expect(projectFilesPollInterval([{ ...file("ready", "disabled"), fts_status: "ready" }], false)).toBe(false);
        expect(taskFilesPollInterval([{ extraction_status: "processing", index_status: "disabled" }], false)).toBe(2000);
        expect(taskFilesPollInterval([{ extraction_status: "ready", fts_status: "pending", index_status: "disabled" }], false)).toBe(2000);
        expect(taskFilesPollInterval([{ extraction_status: "ready", fts_status: "ready", index_status: "pending" }], false)).toBe(false);
        expect(taskFilesPollInterval([{ extraction_status: "disabled", fts_status: "ready", index_status: "disabled" }], false)).toBe(false);
        expect(taskFilesPollInterval([{ extraction_status: "processing" }], true)).toBe(false);
    });

    it("останавливает polling после сетевой ошибки даже с сохранённым pending", () => {
        expect(projectFilesPollInterval([file("pending", "pending")], true)).toBe(false);
        expect(projectFilesPollInterval(undefined, false)).toBe(false);
        expect(projectFilesPollInterval([], false)).toBe(false);
        expect(projectFilesPollInterval([file("ready", "ready"), file("ready", "processing")], false)).toBe(2000);
    });
});

describe("ссылка на файл", () => {
    it("принимает ID файла", () => { expect(projectFileId("42")).toBe(42); });
    it.each([null, "", "0", "-1", "1.5", "5abc", "Infinity", "9007199254740992"])("игнорирует недопустимый ID %s", (value) => {
        expect(projectFileId(value)).toBeNull();
    });
});
