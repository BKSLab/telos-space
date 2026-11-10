import { useEffect, type ReactNode } from "react";
import { useLocation, useMatch } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import { useCurrentUser } from "@/lib/useAuth";
import type { Project } from "@/lib/types";
import { useChatDockStore } from "@/stores/chatDock";
import { ProjectChatProvider } from "./ProjectChatProvider";
import { FloatingChatDock } from "./FloatingChatDock";

/** Один транспорт для вкладки и окна, в том числе вне страниц проекта. */
export function ChatWorkspace({ children }: { children: ReactNode }) {
    const userId = useCurrentUser().data?.id ?? 0;
    const location = useLocation();
    const match = useMatch("/projects/:projectKey/*");
    const projectKey = match?.params.projectKey === "new" ? undefined : match?.params.projectKey;
    const projects = useQuery({ queryKey: queryKeys.projects, queryFn: () => api.get<Project[]>(endpoints.projects()), enabled: userId > 0 }).data;
    const selectedId = useChatDockStore((state) => state.projects[userId]);
    const selectProject = useChatDockStore((state) => state.selectProject);
    const selectPersonal = useChatDockStore(state => state.selectPersonal);
    const panel = useChatDockStore((state) => state.panel);
    const open = useChatDockStore((state) => state.open);
    const routeProject = projects?.find(item => item.key.toUpperCase() === projectKey?.toUpperCase());
    const selectedProject = projects?.find(item => item.id === selectedId);
    const section = match?.params["*"]?.replace(/\/$/, "");
    const globalChatPage = location.pathname.replace(/\/$/, "") === "/chat";
    const fullPage = section === "chat" || section === "conversations" || section === "knowledge" || globalChatPage;
    const teamPageVisible = Boolean(routeProject && section === "chat");
    // Открытое окно закреплено за выбранной перепиской, а не за текущим маршрутом.
    const project = fullPage ? routeProject : panel === "team" ? selectedProject : routeProject ?? selectedProject;
    const visiblePanel = fullPage ? null : panel;

    useEffect(() => {
        if (fullPage && teamPageVisible && routeProject && (routeProject.id !== selectedId || useChatDockStore.getState().modes[userId] !== "team")) selectProject(userId, routeProject.id);
        if (globalChatPage && useChatDockStore.getState().modes[userId] !== "personal") selectPersonal(userId);
    }, [fullPage, teamPageVisible, routeProject, selectedId, userId, selectProject, selectPersonal, globalChatPage]);
    useEffect(() => { if (fullPage) open(null); }, [fullPage, open]);
    useEffect(() => () => { useChatDockStore.getState().open(null); }, [userId]);

    return <ProjectChatProvider projectId={project?.id ?? 0} projectKey={project?.key ?? ""} chatVisible={teamPageVisible || visiblePanel === "team"}>
        {children}
        {userId > 0 && <FloatingChatDock project={project} routeProject={routeProject} projects={projects ?? []} userId={userId} fullPage={fullPage} panel={visiblePanel} />}
    </ProjectChatProvider>;
}
