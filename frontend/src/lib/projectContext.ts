import type { Project, ProjectDescription } from "@/lib/types";

export const PROJECT_CONTEXT_FIELDS = [
    ["problem", "Проблема"],
    ["goal", "Цель"],
    ["expected_result", "Ожидаемый результат"],
    ["audience", "Пользователи и участники"],
    ["capabilities", "Текущие возможности"],
    ["boundaries", "Границы проекта"],
    ["constraints", "Ограничения"],
    ["glossary", "Термины"],
    ["decisions", "Принятые решения"],
    ["open_questions", "Открытые вопросы"],
    ["additional", "Дополнительно"],
] as const satisfies ReadonlyArray<readonly [keyof ProjectDescription, string]>;

export const EMPTY_PROJECT_DESCRIPTION: ProjectDescription = {
    problem: "", goal: "", expected_result: "", additional: "",
    audience: "", capabilities: "", boundaries: "", constraints: "",
    glossary: "", decisions: "", open_questions: "",
};

export function projectDescription(project: Pick<Project, "description_sections" | "description_md">): ProjectDescription {
    return {
        ...EMPTY_PROJECT_DESCRIPTION,
        ...(project.description_sections ?? { additional: project.description_md ?? "" }),
    };
}

export function projectContextLabel(field: string): string {
    return PROJECT_CONTEXT_FIELDS.find(([name]) => name === field)?.[1]
        ?? (field === "description_md" ? "Описание" : field);
}
