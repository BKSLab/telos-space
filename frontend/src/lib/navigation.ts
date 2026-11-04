import { BookOpen, CalendarDays, LayoutDashboard, LayoutTemplate, ListTodo } from "lucide-react";

/** The same destinations are available in the desktop sidebar and mobile menu. */
export const GLOBAL_NAV_ITEMS = [
    { to: "/", label: "Портфель", icon: LayoutDashboard, end: true },
    { to: "/boards", label: "Доски", icon: LayoutTemplate, end: false },
    { to: "/my-tasks", label: "Мои задачи", icon: ListTodo, end: false },
    { to: "/calendar", label: "Календарь", icon: CalendarDays, end: false },
];

export const HELP_NAV_ITEM = { to: "/help", label: "Справка", icon: BookOpen, end: false };
