import { LockKeyhole, Users } from "lucide-react";
import type { Project } from "@/lib/types";
import { Select } from "@/components/ui/Field";

export function ChatSelector({ projects, value, onChange, extraOption }: {
    projects: Project[];
    value: string;
    onChange: (value: string) => void;
    extraOption?: { value: string; label: string };
}) {
    const personal = value === "personal" || value === "project-personal";
    const Icon = personal ? LockKeyhole : Users;
    return <div className="chat-selector min-w-0 flex-1">
        <Select aria-label="Выбор чата" value={value} onChange={event => onChange(event.target.value)} className="h-9 min-w-0 truncate text-sm">
            <option value="personal">Личный — Арис</option>
            {projects.map(project => <option key={project.id} value={`project:${project.id}`}>{project.key} · {project.name}</option>)}
            {extraOption && <option value={extraOption.value}>{extraOption.label}</option>}
        </Select>
        <p className="chat-selector-privacy mt-1 flex items-center gap-1.5 text-xs text-muted"><Icon size={12} className="shrink-0" aria-hidden="true" />{personal ? "Видно только вам" : value === "knowledge" ? "Знания проекта" : "Общий чат · виден участникам проекта"}</p>
    </div>;
}
