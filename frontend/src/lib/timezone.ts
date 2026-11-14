/** IANA-зона браузера; UTC подходит для окружений без Intl/данных часовых поясов. */
export function browserTimezone(): string {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
        return "UTC";
    }
}
