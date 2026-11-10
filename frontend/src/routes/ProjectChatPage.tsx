import { ProjectChatWorkspace } from "@/components/chat/ProjectChatWorkspace";

export function ProjectChatPage() {
    return <section className="flex h-full min-h-0 flex-col" aria-label="Чат проекта">
        <ProjectChatWorkspace />
    </section>;
}
