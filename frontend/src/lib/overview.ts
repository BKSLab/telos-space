/**
 * Актуальность разбора: сравнение времени вывода с временем данных.
 *
 * Разбор делается по кнопке и живёт до следующего запуска. Без отметки о
 * свежести старый вывод выглядит так же уверенно, как только что
 * сделанный, и читатель принимает решения по картине, которой уже нет.
 */

/** Насколько разбор отстал от данных. */
export type PulseFreshness = "none" | "fresh" | "stale";

/**
 * Определяет, устарел ли разбор.
 *
 * @param reportCreatedAt Время формирования разбора, ISO-строка.
 * @param dataUpdatedAt Время последнего изменения данных области.
 * @returns `none` — разбора не было; `stale` — данные новее вывода.
 */
export function reportFreshness(
    reportCreatedAt: string | null | undefined,
    dataUpdatedAt: string | null | undefined,
): PulseFreshness {
    if (!reportCreatedAt) {
        return "none";
    }
    const report = Date.parse(reportCreatedAt);
    const data = dataUpdatedAt ? Date.parse(dataUpdatedAt) : Number.NaN;
    if (Number.isNaN(report) || Number.isNaN(data)) {
        return "fresh";
    }
    return data > report ? "stale" : "fresh";
}

/**
 * Возвращает время последнего изменения из набора объектов.
 *
 * @param items Объекты с полем `updated_at`.
 * @returns Максимальная отметка времени или `null`, если набор пуст.
 */
export function latestUpdate(items: { updated_at?: string | null }[]): string | null {
    let latest: string | null = null;
    let latestValue = Number.NEGATIVE_INFINITY;
    for (const item of items) {
        if (!item.updated_at) {
            continue;
        }
        const value = Date.parse(item.updated_at);
        if (!Number.isNaN(value) && value > latestValue) {
            latestValue = value;
            latest = item.updated_at;
        }
    }
    return latest;
}
