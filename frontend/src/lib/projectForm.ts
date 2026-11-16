import type { Project, ProjectDescription, ProjectStatus, ProjectUpdate } from "@/lib/types";
import { PROJECT_COLORS } from "@/lib/types";
import { parseDateOnly } from "@/lib/dates";
import { EMPTY_PROJECT_DESCRIPTION, PROJECT_CONTEXT_FIELDS, projectDescription } from "@/lib/projectContext";

export interface ProjectFormValues {
    key: string;
    name: string;
    description_sections: ProjectDescription;
    status: ProjectStatus;
    color: string;
    icon: string;
    start_date: string;
    due_date: string;
    due_date_comment: string;
}

export const EMPTY_PROJECT_FORM: ProjectFormValues = {
    key: "",
    name: "",
    description_sections: { ...EMPTY_PROJECT_DESCRIPTION },
    status: "PLANNING",
    color: PROJECT_COLORS[0],
    icon: "",
    start_date: "",
    due_date: "",
    due_date_comment: "",
};

export const PROJECT_KEY_PATTERN = /^[A-Za-z][A-Za-z0-9]{1,9}$/;

export function toProjectFormValues(project: Project): ProjectFormValues {
    return {
        key: project.key,
        name: project.name,
        description_sections: projectDescription(project),
        status: project.status,
        color: project.color,
        icon: project.icon ?? "",
        start_date: project.start_date ?? "",
        due_date: project.due_date ?? "",
        due_date_comment: "",
    };
}

/** Готовит тело запроса: пустые строки превращаются в null. */
export function toProjectPayload(values: ProjectFormValues) {
    return {
        key: values.key.toUpperCase(),
        name: values.name.trim(),
        description_sections: values.description_sections,
        status: values.status,
        color: values.color,
        icon: values.icon.trim() || null,
        start_date: values.start_date || null,
        due_date: values.due_date || null,
    };
}

/** Код проекта участвует в номерах задач, поэтому у существующего проекта он не меняется. */
export function toProjectUpdatePayload(values: ProjectFormValues, project?: Project): ProjectUpdate {
    const { key, description_sections, ...rest } = toProjectPayload(values);
    void key;
    if (!project) return { ...rest, description_sections };
    const original = toProjectPayload(toProjectFormValues(project));
    const changed = Object.fromEntries(Object.entries(rest).filter(([field, value]) =>
        JSON.stringify(value) !== JSON.stringify(original[field as keyof typeof original]),
    )) as ProjectUpdate;
    const sections: Partial<ProjectDescription> = {};
    for (const [field] of PROJECT_CONTEXT_FIELDS) {
        const value = canonicalProjectSection(description_sections[field]);
        if (value !== canonicalProjectSection(original.description_sections[field])) sections[field] = value;
    }
    if (Object.keys(sections).length > 0) {
        changed.description_sections = sections;
        changed.expected_context_revision = project.context_revision ?? 0;
    }
    if (Object.hasOwn(changed, "due_date")) {
        changed.expected_due_date = project.due_date;
        changed.due_date_comment = values.due_date_comment.trim();
    }
    return changed;
}

/** Pydantic str_strip_whitespace удаляет Unicode White_Space, но сохраняет BOM. */
function canonicalProjectSection(value: string | undefined): string {
    return (value ?? "").replace(/^\p{White_Space}+|\p{White_Space}+$/gu, "");
}

/** После явного выбора пользователя переносит его правки поверх свежих данных. */
export function rebaseProjectFormValues(draft: ProjectFormValues, previous: Project, current: Project): ProjectFormValues {
    const latest = toProjectFormValues(current);
    const changes = toProjectUpdatePayload(draft, previous);
    const retained = Object.fromEntries(Object.entries(draft).filter(([field]) =>
        field !== "description_sections" && Object.hasOwn(changes, field),
    ));
    const sections = { ...latest.description_sections };
    for (const [field] of PROJECT_CONTEXT_FIELDS) {
        if (Object.hasOwn(changes.description_sections ?? {}, field)) {
            sections[field] = changes.description_sections?.[field] ?? "";
        }
    }
    const rebased = { ...latest, ...retained, description_sections: sections };
    if (current.due_date !== previous.due_date) rebased.due_date_comment = "";
    return rebased;
}

export function isProjectFormValid(values: ProjectFormValues): boolean {
    return PROJECT_KEY_PATTERN.test(values.key) && values.name.trim().length > 0
        && (!values.start_date || parseDateOnly(values.start_date) !== null)
        && (!values.due_date || parseDateOnly(values.due_date) !== null)
        && !(values.start_date && values.due_date && values.due_date < values.start_date);
}

export function requiresDeadlineComment(project: Project, values: ProjectFormValues): boolean {
    return (project.due_date_has_been_set || project.due_date !== null)
        && (values.due_date || null) !== project.due_date;
}
