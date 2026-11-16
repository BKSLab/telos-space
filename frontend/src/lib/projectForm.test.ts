import { describe, expect, it } from "vitest";
import {
    EMPTY_PROJECT_FORM,
    isProjectFormValid,
    toProjectPayload,
    toProjectUpdatePayload,
    toProjectFormValues,
    requiresDeadlineComment,
    rebaseProjectFormValues,
    type ProjectFormValues,
} from "@/lib/projectForm";
import type { Project } from "@/lib/types";

const VALID: ProjectFormValues = {
    ...EMPTY_PROJECT_FORM,
    key: "proj",
    name: "  Тестовый проект  ",
    icon: " 🚀 ",
    due_date: "2026-12-20",
};

describe("isProjectFormValid", () => {
    it("требует корректный код и непустое название", () => {
        expect(isProjectFormValid(VALID)).toBe(true);
        expect(isProjectFormValid({ ...VALID, key: "V" })).toBe(false);
        expect(isProjectFormValid({ ...VALID, key: "1PROJ" })).toBe(false);
        expect(isProjectFormValid({ ...VALID, key: "PROJPROJPROJ" })).toBe(false);
        expect(isProjectFormValid({ ...VALID, name: "   " })).toBe(false);
    });
});

describe("toProjectPayload", () => {
    it("нормализует код, обрезает строки и заменяет пустые значения на null", () => {
        const payload = toProjectPayload(VALID);

        expect(payload.key).toBe("PROJ");
        expect(payload.name).toBe("Тестовый проект");
        expect(payload.description_sections).toEqual(EMPTY_PROJECT_FORM.description_sections);
        expect(payload.icon).toBe("🚀");
        expect(payload.start_date).toBeNull();
        expect(payload.due_date).toBe("2026-12-20");
    });
});

const PROJECT: Project = {
    id: 7, key: "PROJ", name: "Проект", description_md: "Старое **описание**",
    status: "ACTIVE", color: "#58a6ff", icon: null, start_date: "2026-09-01",
    due_date: "2026-12-20", order_index: 0, created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
};

describe("паспорт и сроки", () => {
    it("сохраняет старое описание целиком в дополнительных сведениях", () => {
        expect(toProjectFormValues(PROJECT).description_sections.additional).toBe(PROJECT.description_md);
    });

    it("исключает неизменённый срок из PATCH, чтобы не затереть чужой перенос", () => {
        const payload = toProjectUpdatePayload(toProjectFormValues(PROJECT), PROJECT);
        expect(payload).not.toHaveProperty("due_date");
        expect(payload).not.toHaveProperty("due_date_comment");
    });

    it("передаёт исходный срок и причину его удаления", () => {
        const values = { ...toProjectFormValues(PROJECT), due_date: "", due_date_comment: "  Перепланирование  " };
        expect(requiresDeadlineComment(PROJECT, values)).toBe(true);
        expect(toProjectUpdatePayload(values, PROJECT)).toMatchObject({
            due_date: null, expected_due_date: "2026-12-20", due_date_comment: "Перепланирование",
        });
    });

    it("различает первое назначение и возврат срока после удаления", () => {
        const undated = { ...PROJECT, due_date: null, due_date_has_been_set: false };
        expect(requiresDeadlineComment(undated, VALID)).toBe(false);
        expect(requiresDeadlineComment({ ...undated, due_date_has_been_set: true }, VALID)).toBe(true);
        expect(requiresDeadlineComment(PROJECT, toProjectFormValues(PROJECT))).toBe(false);
    });

    it("отклоняет обратный период и несуществующие даты, разрешает открытый период", () => {
        expect(isProjectFormValid({ ...VALID, start_date: "2027-01-01" })).toBe(false);
        expect(isProjectFormValid({ ...VALID, due_date: "2026-02-30" })).toBe(false);
        expect(isProjectFormValid({ ...VALID, start_date: "2026-09-07", due_date: "" })).toBe(true);
    });
});

describe("toProjectUpdatePayload", () => {
    it("не отправляет код проекта: он участвует в номерах задач", () => {
        expect(toProjectUpdatePayload(VALID)).not.toHaveProperty("key");
        expect(toProjectUpdatePayload(VALID).name).toBe("Тестовый проект");
    });
});

describe("ревизия и сохранение черновика паспорта", () => {
    it("не переносит whitespace-only goal поверх новой цели при rebase остальных правок", () => {
        const previous: Project = { ...PROJECT, context_revision: 1,
            description_sections: { problem: "", goal: "Alpha", expected_result: "", additional: "" } };
        const draft = toProjectFormValues(previous);
        draft.description_sections.goal = " Alpha ";
        draft.description_sections.constraints = "Мои ограничения";
        const current: Project = { ...previous, context_revision: 2,
            description_sections: { ...previous.description_sections!, goal: "Beta" } };
        const merged = rebaseProjectFormValues(draft, previous, current);
        expect(merged.description_sections.goal).toBe("Beta");
        expect(toProjectUpdatePayload(draft, previous)).toEqual({
            description_sections: { constraints: "Мои ограничения" }, expected_context_revision: 1,
        });
        expect(toProjectUpdatePayload(merged, current)).toEqual({
            description_sections: { constraints: "Мои ограничения" }, expected_context_revision: 2,
        });
    });

    it("при audience-only PATCH не отправляет нетронутый legacy raw Markdown как additional", () => {
        const raw = "\n\n  # Прежний Markdown  \n\t";
        const previous: Project = { ...PROJECT, description_sections: null, description_md: raw, context_revision: 0 };
        const draft = toProjectFormValues(previous);
        draft.description_sections.audience = "Команда проекта";
        expect(toProjectUpdatePayload(draft, previous)).toEqual({
            description_sections: { audience: "Команда проекта" }, expected_context_revision: 0,
        });
        expect(draft.description_sections.additional).toBe(raw);
    });

    it("игнорирует канонические whitespace-only правки без ревизии и сохраняет явную очистку", () => {
        const previous: Project = { ...PROJECT, context_revision: 1,
            description_sections: { problem: "", goal: "Alpha", expected_result: "", additional: "" } };
        const draft = toProjectFormValues(previous);
        for (const unchanged of [" Alpha ", "\tAlpha\n", "\u0085Alpha\u00a0"]) {
            draft.description_sections.goal = unchanged;
            expect(toProjectUpdatePayload(draft, previous)).toEqual({});
        }
        draft.description_sections.goal = " \n\t";
        expect(toProjectUpdatePayload(draft, previous)).toEqual({
            description_sections: { goal: "" }, expected_context_revision: 1,
        });
        draft.description_sections.goal = "\ufeffAlpha";
        expect(toProjectUpdatePayload(draft, previous)).toEqual({
            description_sections: { goal: "\ufeffAlpha" }, expected_context_revision: 1,
        });
    });

    it("не переписывает прежний Markdown при сохранении метаданных и передаёт только изменённое", () => {
        const values = { ...toProjectFormValues(PROJECT), name: "Новое название" };
        expect(toProjectUpdatePayload(values, PROJECT)).toEqual({ name: "Новое название" });
        expect(toProjectUpdatePayload(toProjectFormValues(PROJECT), PROJECT)).toEqual({});
        expect(values.description_sections.additional).toBe(PROJECT.description_md);
        expect(values.description_sections.goal).toBe("");
    });

    it("при правке раздела передаёт прочитанную ревизию, включая исходную нулевую", () => {
        const values = toProjectFormValues(PROJECT);
        values.description_sections.goal = "Подтверждённая цель";
        expect(toProjectUpdatePayload(values, PROJECT)).toMatchObject({ expected_context_revision: 0 });
        expect(toProjectUpdatePayload(values, { ...PROJECT, context_revision: 7 })).toMatchObject({ expected_context_revision: 7 });
        expect(toProjectUpdatePayload(values, PROJECT)).not.toHaveProperty("due_date");
    });

    it("после явного разрешения конфликта сохраняет свои правки, обновляя незатронутые сведения", () => {
        const previous: Project = { ...PROJECT, context_revision: 1,
            description_sections: { problem: "", goal: "Прежняя цель", expected_result: "", additional: "" } };
        const draft = toProjectFormValues(previous);
        draft.description_sections.goal = "Моя цель";
        draft.description_sections.constraints = "Мои ограничения";
        const current: Project = { ...previous, name: "Актуальное название", context_revision: 2,
            due_date: "2027-01-01", description_sections: { ...previous.description_sections!,
                goal: "Чужая правка цели", capabilities: "Работающий каталог" } };
        const merged = rebaseProjectFormValues(draft, previous, current);
        expect(merged.name).toBe(current.name);
        expect(merged.due_date).toBe(current.due_date);
        expect(merged.description_sections.goal).toBe("Моя цель");
        expect(merged.description_sections.constraints).toBe("Мои ограничения");
        expect(merged.description_sections.capabilities).toBe("Работающий каталог");
        const payload = toProjectUpdatePayload(merged, current);
        expect(payload.expected_context_revision).toBe(2);
        expect(payload).not.toHaveProperty("name");
        expect(payload).not.toHaveProperty("due_date");
    });

    it("при rebase не восстанавливает имя и иконку, исключённые из PATCH после нормализации", () => {
        const previous: Project = { ...PROJECT, icon: "lucide:rocket", context_revision: 1 };
        const draft = toProjectFormValues(previous);
        draft.name = ` ${previous.name} `;
        draft.icon = " lucide:rocket ";
        draft.description_sections.goal = "Моя цель";
        const initial = toProjectUpdatePayload(draft, previous);
        expect(initial).not.toHaveProperty("name");
        expect(initial).not.toHaveProperty("icon");
        const current: Project = { ...previous, name: "Название после чужой правки", icon: "lucide:flag",
            color: "#123456", context_revision: 2 };
        const merged = rebaseProjectFormValues(draft, previous, current);
        expect(merged.name).toBe(current.name);
        expect(merged.icon).toBe(current.icon);
        expect(merged.color).toBe(current.color);
        expect(merged.description_sections.goal).toBe("Моя цель");
        const payload = toProjectUpdatePayload(merged, current);
        expect(payload).not.toHaveProperty("name");
        expect(payload).not.toHaveProperty("icon");
        expect(payload).not.toHaveProperty("color");
        expect(payload.expected_context_revision).toBe(2);
    });

    it("при rebase просит новую причину только после изменения серверного исходного срока", () => {
        const previous: Project = { ...PROJECT, due_date: "2026-10-20", context_revision: 1 };
        const draft = { ...toProjectFormValues(previous), due_date: "2026-12-20", due_date_comment: "OldReason" };
        draft.description_sections.goal = "Моя цель";
        const current: Project = { ...previous, due_date: "2026-11-20", context_revision: 2 };
        const merged = rebaseProjectFormValues(draft, previous, current);
        expect(merged.due_date).toBe("2026-12-20");
        expect(merged.description_sections.goal).toBe("Моя цель");
        expect(merged.due_date_comment).toBe("");
        expect(draft.due_date_comment).toBe("OldReason");
        expect(toProjectUpdatePayload(merged, current)).toMatchObject({
            due_date: "2026-12-20", expected_due_date: "2026-11-20", due_date_comment: "",
            description_sections: { goal: "Моя цель" }, expected_context_revision: 2,
        });
        const stable = rebaseProjectFormValues(draft, previous, { ...current, due_date: previous.due_date });
        expect(stable.due_date_comment).toBe("OldReason");
        expect(stable.due_date).toBe("2026-12-20");
        expect(stable.description_sections.goal).toBe("Моя цель");
    });

    it("при rebase сохраняет осмысленную правку метаданных и обновляет незатронутые разделы", () => {
        const previous: Project = { ...PROJECT, context_revision: 1 };
        const draft = { ...toProjectFormValues(previous), name: " Новое название ", icon: " lucide:rocket " };
        const current: Project = { ...previous, context_revision: 2,
            description_sections: { problem: "Новая проблема", goal: "Чужая цель", expected_result: "", additional: "" } };
        const merged = rebaseProjectFormValues(draft, previous, current);
        expect(merged.description_sections.problem).toBe("Новая проблема");
        expect(merged.description_sections.goal).toBe("Чужая цель");
        expect(toProjectUpdatePayload(merged, current)).toEqual({ name: "Новое название", icon: "lucide:rocket" });
    });
});
