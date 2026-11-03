import { lazy, Suspense, useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Expand, MessageSquare, X } from "lucide-react";
import type { Project } from "@/lib/types";
import { useProjectChat } from "@/lib/useProjectChat";
import { useChatDockStore, type ChatPanel } from "@/stores/chatDock";
import { useUiStore } from "@/stores/ui";
import { cn } from "@/lib/cn";
import { IconButton } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/States";
import { ChatSelector } from "./ChatSelector";
import { ProjectChatWorkspace } from "./ProjectChatWorkspace";
import "@/components/tim/tim.css";
import "./chat.css";

const TimPanel = lazy(() => import("@/components/tim/TimPanel").then(module => ({ default: module.TimPanel })));

interface Props { project?: Project; routeProject?: Project; projects: Project[]; userId: number; fullPage: boolean; panel: ChatPanel | null }

export function FloatingChatDock({ project, routeProject, projects, userId, fullPage, panel }: Props) {
    const navigate = useNavigate();
    const location = useLocation();
    const canvasPage = /^\/projects\/[^/]+\/(?:whiteboard|structure)\/?$/.test(location.pathname) || /^\/boards(?:\/|$)/.test(location.pathname);
    const riskOpen = useUiStore(state => state.selectedRisk !== null);
    const open = useChatDockStore(state => state.open);
    const selectProject = useChatDockStore(state => state.selectProject);
    const selectPersonal = useChatDockStore(state => state.selectPersonal);
    const { info, error } = useProjectChat();
    const unread = error ? 0 : info?.unread_count ?? 0;
    const dialog = useRef<HTMLDivElement>(null);
    const launcher = useRef<HTMLButtonElement>(null);
    useEffect(() => { if (panel) dialog.current?.focus({ preventScroll: true }); }, [panel]);
    const close = () => { open(null); requestAnimationFrame(() => launcher.current?.focus()); };
    const choose = (value: string) => {
        const selected = projects.find(item => `project:${item.id}` === value);
        if (selected) { selectProject(userId, selected.id); open("team"); }
        else if (value === "personal") { selectPersonal(userId); open("personal"); }
    };
    const launch = () => {
        if (panel) { close(); return; }
        const state = useChatDockStore.getState();
        const remembered = projects.find(item => item.id === state.projects[userId]);
        if (routeProject) { selectProject(userId, routeProject.id); open("team"); }
        else if (state.modes[userId] === "team" && remembered) { selectProject(userId, remembered.id); open("team"); }
        else { selectPersonal(userId); open("personal"); }
    };
    const expand = () => {
        open(null);
        const params = new URLSearchParams(location.search);
        params.delete("view");
        if (project?.id !== routeProject?.id) params.delete("message");
        if (panel === "team" && project) navigate({ pathname: `/projects/${project.key}/chat`, search: params.toString(), hash: location.hash });
        else navigate({ pathname: "/chat", search: params.toString(), hash: location.hash });
    };
    if (fullPage) return null;

    return <div data-react-aria-top-layer="true" className={cn("chat-dock pointer-events-none fixed z-[150] flex max-w-[calc(100vw-1.5rem)] flex-col items-end gap-3", riskOpen && !panel && "chat-dock-risk")}>
        {panel && <div ref={dialog} role="dialog" aria-modal="false" aria-label="Чаты" id="floating-chats" tabIndex={-1}
            onKeyDown={event => { if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); event.stopPropagation(); close(); } }}
            className={cn("chat-floating-panel pointer-events-auto fixed inset-0 flex min-h-0 flex-col overflow-hidden border shadow-panel outline-none sm:relative sm:inset-auto sm:h-[min(44rem,calc(var(--app-height,100dvh)-6rem))] sm:w-[30rem] sm:max-w-full sm:rounded-[var(--radius-panel)]", panel === "personal" ? "brand-ai-panel border-transparent" : "border-line bg-surface")}>
            <header className="chat-floating-header material-metal flex shrink-0 items-start gap-2 border-b border-line px-3 py-3">
                <ChatSelector projects={projects} value={panel === "personal" ? "personal" : `project:${project?.id ?? ""}`} onChange={choose} />
                <IconButton label="Открыть чат во вкладке" onClick={expand}><Expand size={16} /></IconButton>
                <IconButton label="Свернуть чат" onClick={close}><X size={17} /></IconButton>
            </header>
            <div className="min-h-0 flex-1">
                {panel === "personal" ? <Suspense fallback={<div className="p-4"><Skeleton className="h-40 w-full" /></div>}><TimPanel userId={userId} embedded /></Suspense>
                    : project ? <ProjectChatWorkspace key={project.id} compact /> : <p className="p-5 text-sm text-muted">Выберите доступный проект или личный диалог.</p>}
            </div>
        </div>}
        {!panel && <button ref={launcher} type="button" id="chat-launcher" aria-label="Открыть чаты" aria-expanded={false} aria-controls="floating-chats" onClick={launch}
            className={cn("material-glass pointer-events-auto relative flex h-11 items-center gap-2 rounded-full border border-line px-4 text-sm font-medium text-primary shadow-panel hover:border-accent/60 focus-visible:outline-2 focus-visible:outline-accent", canvasPage && "chat-canvas-launcher")}>
            <MessageSquare size={18} className="text-accent" aria-hidden="true" /><span className="chat-launcher-label">Чаты</span>
            {unread > 0 && <span className="chat-launcher-unread rounded-full bg-accent px-1.5 py-0.5 text-xs text-on-accent" title="Непрочитано в текущем проекте">{unread > 99 ? "99+" : unread}</span>}
        </button>}
    </div>;
}
