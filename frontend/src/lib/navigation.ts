import { CalendarDays, FolderKanban, KeyRound, LayoutDashboard, LayoutTemplate, ListTodo, MessageCircle, Plug } from "lucide-react";

/** The same destinations are available in the desktop sidebar and mobile menu. */
export const GLOBAL_NAV_ITEMS = [
    { to: "/", label: "Портфель", icon: LayoutDashboard, end: true },
    { to: "/projects", label: "Проекты", icon: FolderKanban, end: false },
    { to: "/boards", label: "Доски", icon: LayoutTemplate, end: false },
    { to: "/my-tasks", label: "Мои задачи", icon: ListTodo, end: false },
    { to: "/calendar", label: "Календарь", icon: CalendarDays, end: false },
    { to: "/chat", label: "Чаты", icon: MessageCircle, end: false },
    { to: "/my-api-keys", label: "API-ключи", icon: KeyRound, end: false },
    { to: "/mcp", label: "MCP", icon: Plug, end: false },
];
