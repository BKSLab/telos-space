import { MyTasksPage } from "@/routes/MyTasksPage";
import { TaskAttentionProvider } from "@/components/tasks/TaskAttentionProvider";
import { AgentConfirmModal } from "@/components/agent/AgentConfirmModal";
import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { AuthGuard } from "@/components/layout/AuthGuard";
import { ProjectLayout } from "@/components/projects/ProjectLayout";
import { PortfolioPage } from "@/routes/PortfolioPage";
import { NewProjectPage } from "@/routes/NewProjectPage";
import { ProjectTeamPage } from "@/routes/ProjectTeamPage";
import { BoardPage } from "@/routes/BoardPage";
import { TasksListPage } from "@/routes/TasksListPage";
import { ProjectDocumentsPage } from "@/routes/ProjectDocumentsPage";
import { DocumentDetailPage } from "@/routes/DocumentDetailPage";
import { ProjectSettingsPage } from "@/routes/ProjectSettingsPage";
import { ProjectConversationsPage } from "@/routes/ProjectConversationsPage";
import { ProjectKnowledgePage } from "@/routes/ProjectKnowledgePage";
import { ProjectChatPage } from "@/routes/ProjectChatPage";
import { ProjectOverviewPage } from "@/routes/ProjectOverviewPage";
import { ProjectRisksPage } from "@/routes/ProjectRisksPage";
import { ProfilePage } from "@/routes/ProfilePage";
import { LoginPage } from "@/routes/LoginPage";
import { RegisterPage } from "@/routes/RegisterPage";
import { NotFoundPage } from "@/routes/NotFoundPage";
import { Skeleton } from "@/components/ui/States";
const GlobalChatPage = lazy(() => import("@/routes/GlobalChatPage"));
const HelpPage = lazy(() => import("@/routes/HelpPage").then(module => ({ default: module.HelpPage })));

/**
 * Конструктор ИСР тянет React Flow и elkjs — самые тяжёлые зависимости
 * приложения. Отдельный чанк не даёт им попасть в стартовый бандл.
 */
const StructurePage = lazy(() =>
    import("@/routes/StructurePage").then((module) => ({ default: module.StructurePage })),
);
const ProjectCalendarPage = lazy(() =>
    import("@/routes/ProjectCalendarPage").then((module) => ({
        default: module.ProjectCalendarPage,
    })),
);
const ProjectBoardPage = lazy(() =>
    import("@/routes/ProjectBoardPage").then((module) => ({
        default: module.ProjectBoardPage,
    })),
);
const FreeBoardsPage = lazy(() =>
    import("@/routes/FreeBoardsPage").then((module) => ({ default: module.FreeBoardsPage })),
);
const MyCalendarPage = lazy(() => import("@/routes/MyCalendarPage"));

function StructureFallback() {
    return (
        <div role="status" aria-label="Загрузка структуры" className="flex h-full gap-3 p-4">
            <Skeleton className="h-full w-72 shrink-0" />
            <Skeleton className="h-full flex-1" />
        </div>
    );
}

function CalendarFallback() {
    return (
        <div role="status" aria-label="Загрузка календаря" className="flex h-full gap-3 p-4">
            <Skeleton className="h-full w-72 shrink-0" />
            <Skeleton className="h-full flex-1" />
        </div>
    );
}

function BoardFallback() {
    return (
        <div role="status" aria-label="Загрузка доски" className="p-4 sm:p-5">
            <Skeleton className="mb-5 h-10 w-56" />
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                <Skeleton className="h-56" />
                <Skeleton className="h-56" />
                <Skeleton className="h-56" />
            </div>
        </div>
    );
}

/** Приложение за стеной входа: всё внутри требует действительной сессии. */
function ProtectedApp() {
    return (
        <AuthGuard>
            <TaskAttentionProvider>
                <ChatWorkspace>
                    <AppShell>
                        <Routes>
                            <Route path="/" element={<PortfolioPage />} />
                            <Route path="/chat" element={<Suspense fallback={<div className="p-5"><Skeleton className="h-48 w-full" /></div>}><GlobalChatPage /></Suspense>} />
                            <Route path="/boards" element={<Suspense fallback={<BoardFallback />}><FreeBoardsPage /></Suspense>} />
                            <Route path="/my-tasks" element={<MyTasksPage />} />
                            <Route path="/calendar" element={<Suspense fallback={<CalendarFallback />}><MyCalendarPage /></Suspense>} />
                            <Route path="/projects/new" element={<NewProjectPage />} />
                            <Route path="/profile" element={<ProfilePage />} />
                            <Route path="/help/:sectionId?" element={<Suspense fallback={<div className="p-5"><Skeleton className="h-48 w-full" /></div>}><HelpPage /></Suspense>} />
                            <Route path="/projects/:projectKey" element={<ProjectLayout />}>
                                <Route index element={<ProjectOverviewPage />} />
                                <Route path="team" element={<ProjectTeamPage />} />
                                <Route path="board" element={<BoardPage />} />
                                <Route path="tasks" element={<TasksListPage />} />
                                <Route path="risks" element={<ProjectRisksPage />} />
                                <Route
                                    path="calendar"
                                    element={
                                        <Suspense fallback={<CalendarFallback />}>
                                            <ProjectCalendarPage />
                                        </Suspense>
                                    }
                                />
                                <Route
                                    path="structure"
                                    element={
                                        <Suspense fallback={<StructureFallback />}>
                                            <StructurePage />
                                        </Suspense>
                                    }
                                />
                                <Route
                                    path="whiteboard"
                                    element={
                                        <Suspense fallback={<BoardFallback />}>
                                            <ProjectBoardPage />
                                        </Suspense>
                                    }
                                />
                                <Route path="docs" element={<ProjectDocumentsPage />} />
                                <Route path="docs/:slug" element={<DocumentDetailPage />} />
                                <Route path="chat" element={<ProjectChatPage />} />
                                <Route path="conversations" element={<ProjectConversationsPage />} />
                                <Route path="knowledge" element={<ProjectKnowledgePage />} />
                                <Route path="settings" element={<ProjectSettingsPage />} />
                            </Route>
                            <Route path="*" element={<NotFoundPage />} />
                        </Routes>
                    </AppShell>
                    <AgentConfirmModal />
                </ChatWorkspace>
            </TaskAttentionProvider>
        </AuthGuard>
    );
}

function App() {
    return (
        <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="*" element={<ProtectedApp />} />
        </Routes>
    );
}

export default App;
