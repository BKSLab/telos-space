import type { Project } from "@/lib/types";
import { PROJECT_CONTEXT_FIELDS, projectContextLabel, projectDescription } from "@/lib/projectContext";

export function ProjectContextGaps({ project }: { project: Project }) {
    const sections = projectDescription(project);
    const unknown = project.context?.unknown_fields
        ?? PROJECT_CONTEXT_FIELDS.filter(([field]) => !sections[field]?.trim()).map(([field]) => field);
    return (
        <div className="text-[12px] text-muted">
            {unknown.length > 0
                ? <p>Пока не определено: {unknown.map(projectContextLabel).join(", ")}. Эти сведения можно уточнить в настройках проекта.</p>
                : <p>Все разделы паспорта заполнены.</p>}
        </div>
    );
}
