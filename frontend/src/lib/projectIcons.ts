import {
    Banknote,
    Bell,
    BookOpen,
    Bot,
    Boxes,
    Briefcase,
    Building2,
    CalendarDays,
    ChartColumn,
    ChartLine,
    ChartPie,
    ClipboardList,
    Cloud,
    Code,
    Container,
    Cpu,
    Database,
    Factory,
    FileText,
    Flag,
    FlaskConical,
    Folder,
    FolderKanban,
    Forklift,
    Gauge,
    Globe,
    GraduationCap,
    Handshake,
    Headset,
    HeartPulse,
    Leaf,
    Lightbulb,
    Lock,
    MapPin,
    Megaphone,
    MessageSquare,
    Package,
    Plane,
    Puzzle,
    Receipt,
    Rocket,
    Route,
    Scale,
    Server,
    Settings,
    ShieldCheck,
    Ship,
    ShoppingCart,
    Snowflake,
    Sparkles,
    Star,
    Store,
    Target,
    Thermometer,
    Trophy,
    Truck,
    Users,
    Wallet,
    Warehouse,
    Workflow,
    Wrench,
    Zap,
    type LucideIcon,
} from "lucide-react";

/** Иконка проекта из набора: имя хранится в API как `lucide:<name>`. */
export interface ProjectIconOption {
    name: string;
    label: string;
    Icon: LucideIcon;
}

export const LUCIDE_ICON_PREFIX = "lucide:";

/**
 * Набор иконок проекта. Порядок — порядок сетки в выборе: сначала общее,
 * затем логистика и склад, деньги, ИТ, люди и прочее. Имена — канонические
 * имена Lucide, их нельзя переименовывать: они сохранены у проектов.
 */
export const PROJECT_ICONS: readonly ProjectIconOption[] = [
    { name: "folder", label: "Папка", Icon: Folder },
    { name: "folder-kanban", label: "Доска", Icon: FolderKanban },
    { name: "rocket", label: "Запуск", Icon: Rocket },
    { name: "target", label: "Цель", Icon: Target },
    { name: "flag", label: "Веха", Icon: Flag },
    { name: "star", label: "Звезда", Icon: Star },
    { name: "zap", label: "Молния", Icon: Zap },
    { name: "sparkles", label: "Новое", Icon: Sparkles },
    { name: "lightbulb", label: "Идея", Icon: Lightbulb },
    { name: "puzzle", label: "Интеграция", Icon: Puzzle },
    { name: "trophy", label: "Результат", Icon: Trophy },
    { name: "truck", label: "Грузовик", Icon: Truck },
    { name: "route", label: "Маршрут", Icon: Route },
    { name: "map-pin", label: "Точка", Icon: MapPin },
    { name: "package", label: "Посылка", Icon: Package },
    { name: "boxes", label: "Коробки", Icon: Boxes },
    { name: "warehouse", label: "Склад", Icon: Warehouse },
    { name: "forklift", label: "Погрузчик", Icon: Forklift },
    { name: "container", label: "Контейнер", Icon: Container },
    { name: "plane", label: "Авиа", Icon: Plane },
    { name: "ship", label: "Море", Icon: Ship },
    { name: "globe", label: "Мир", Icon: Globe },
    { name: "thermometer", label: "Температура", Icon: Thermometer },
    { name: "snowflake", label: "Холод", Icon: Snowflake },
    { name: "heart-pulse", label: "Медицина", Icon: HeartPulse },
    { name: "flask-conical", label: "Лаборатория", Icon: FlaskConical },
    { name: "leaf", label: "Экология", Icon: Leaf },
    { name: "wallet", label: "Кошелёк", Icon: Wallet },
    { name: "banknote", label: "Деньги", Icon: Banknote },
    { name: "receipt", label: "Счёт", Icon: Receipt },
    { name: "shopping-cart", label: "Корзина", Icon: ShoppingCart },
    { name: "store", label: "Магазин", Icon: Store },
    { name: "scale", label: "Право", Icon: Scale },
    { name: "chart-column", label: "Столбцы", Icon: ChartColumn },
    { name: "chart-line", label: "График", Icon: ChartLine },
    { name: "chart-pie", label: "Доли", Icon: ChartPie },
    { name: "gauge", label: "Показатели", Icon: Gauge },
    { name: "code", label: "Код", Icon: Code },
    { name: "database", label: "База данных", Icon: Database },
    { name: "server", label: "Сервер", Icon: Server },
    { name: "cloud", label: "Облако", Icon: Cloud },
    { name: "cpu", label: "Железо", Icon: Cpu },
    { name: "bot", label: "Бот", Icon: Bot },
    { name: "workflow", label: "Процесс", Icon: Workflow },
    { name: "settings", label: "Настройки", Icon: Settings },
    { name: "wrench", label: "Ремонт", Icon: Wrench },
    { name: "shield-check", label: "Безопасность", Icon: ShieldCheck },
    { name: "lock", label: "Доступ", Icon: Lock },
    { name: "users", label: "Команда", Icon: Users },
    { name: "handshake", label: "Партнёры", Icon: Handshake },
    { name: "headset", label: "Поддержка", Icon: Headset },
    { name: "message-square", label: "Чат", Icon: MessageSquare },
    { name: "megaphone", label: "Маркетинг", Icon: Megaphone },
    { name: "bell", label: "Уведомления", Icon: Bell },
    { name: "file-text", label: "Документ", Icon: FileText },
    { name: "clipboard-list", label: "Чек-лист", Icon: ClipboardList },
    { name: "calendar-days", label: "Календарь", Icon: CalendarDays },
    { name: "book-open", label: "Знания", Icon: BookOpen },
    { name: "graduation-cap", label: "Обучение", Icon: GraduationCap },
    { name: "briefcase", label: "Бизнес", Icon: Briefcase },
    { name: "building-2", label: "Офис", Icon: Building2 },
    { name: "factory", label: "Производство", Icon: Factory },
];

const BY_NAME = new Map(PROJECT_ICONS.map((option) => [option.name, option]));

/** Компактный выбор; полный справочник выше продолжает отображать сохранённые иконки. */
const PICKER_NAMES = new Set([
    "folder", "folder-kanban", "target", "rocket", "lightbulb", "code",
    "database", "bot", "globe", "users", "handshake", "headset",
    "book-open", "flask-conical", "briefcase", "chart-column", "shield-check", "package",
]);
export const PROJECT_PICKER_ICONS = PROJECT_ICONS.filter(option => PICKER_NAMES.has(option.name));

export function lucideIconValue(name: string): string {
    return `${LUCIDE_ICON_PREFIX}${name}`;
}

/** Иконка набора по значению поля `icon`; эмодзи и неизвестные имена — `null`. */
export function findProjectIcon(icon: string | null | undefined): ProjectIconOption | null {
    if (!icon?.startsWith(LUCIDE_ICON_PREFIX)) return null;
    return BY_NAME.get(icon.slice(LUCIDE_ICON_PREFIX.length)) ?? null;
}

/** Старый формат: эмодзи. Строка `lucide:…` эмодзи не считается, даже неизвестная. */
export function projectEmoji(icon: string | null | undefined): string | null {
    const value = icon?.trim();
    if (!value || value.startsWith(LUCIDE_ICON_PREFIX)) return null;
    return value;
}

export const ICON_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const ICON_IMAGE_MAX_BYTES = 2 * 1024 * 1024;

/** Проверка до загрузки: тот же тип и размер, что проверит сервер. */
export function iconImageProblem(file: Pick<File, "type" | "size">): string | null {
    if (!(ICON_IMAGE_TYPES as readonly string[]).includes(file.type)) {
        return "Подходят только PNG, JPG или WebP.";
    }
    if (file.size > ICON_IMAGE_MAX_BYTES) return "Картинка больше 2 МБ.";
    return null;
}
