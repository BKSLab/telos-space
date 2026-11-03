import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { MoreHorizontal } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { Project } from "@/lib/types";
import { useCurrentUser } from "@/lib/useAuth";
import { useChatDockStore } from "@/stores/chatDock";
import { ChatSelector } from "@/components/chat/ChatSelector";
import { TimPanel } from "@/components/tim/TimPanel";
import { Popover } from "@/components/ui/Popover";

export default function GlobalChatPage() {
    const userId = useCurrentUser().data?.id ?? 0;
    const projects = useQuery({ queryKey: queryKeys.projects, queryFn: () => api.get<Project[]>(endpoints.projects()) }).data ?? [];
    const navigate = useNavigate();
    const choose = (value: string) => {
        const project = projects.find(item => `project:${item.id}` === value);
        if (project) { useChatDockStore.getState().selectProject(userId, project.id); navigate(`/projects/${project.key}/chat`); }
    };
    return <section aria-label="Чаты" className="flex h-full min-h-0 flex-col">
        <header className="material-metal flex shrink-0 items-start gap-2 border-b border-line px-3 py-2 sm:gap-3 sm:px-5 sm:py-4">
            <div className="min-w-0 max-w-lg flex-1"><ChatSelector projects={projects} value="personal" onChange={choose} /></div>
            {projects.length > 0 && <Popover label="Проектные диалоги и знания" width={320} trigger={<MoreHorizontal size={18} aria-hidden="true" />}>
                {({ close }) => <div className="max-h-80 space-y-2 overflow-y-auto p-2">
                    {projects.map(project => <section key={project.id} className="rounded-lg border border-line-subtle p-2">
                        <h2 className="mb-1 truncate text-sm font-medium text-primary">{project.key} · {project.name}</h2>
                        <div className="flex flex-wrap gap-2 text-sm text-accent">
                            <Link aria-label={`${project.key} · Личные диалоги`} to={`/projects/${project.key}/chat?view=personal`} onClick={close} className="flex min-h-11 items-center rounded px-2 py-1 hover:bg-hover">Личные диалоги</Link>
                            <Link aria-label={`${project.key} · Знания`} to={`/projects/${project.key}/chat?view=knowledge`} onClick={close} className="flex min-h-11 items-center rounded px-2 py-1 hover:bg-hover">Знания</Link>
                        </div>
                    </section>)}
                </div>}
            </Popover>}
        </header>
        <div className="min-h-0 flex-1"><TimPanel userId={userId} embedded /></div>
    </section>;
}
