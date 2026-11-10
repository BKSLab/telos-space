import { HELP_SECTIONS, type HelpArticle, type HelpSection } from "@/content/help";
import { findCatalogEntries } from "@/lib/integrationCatalog";

const STOP_WORDS = new Set(["как", "где", "что", "это", "для", "или", "и", "в", "на", "с", "к", "по", "из", "не", "у", "о", "об", "чтобы", "можно"]);

export interface HelpSearchResult {
    section: HelpSection;
    article: HelpArticle;
    excerpt: string;
}

export function normalizeHelpText(value: string): string {
    return value.toLocaleLowerCase("ru-RU").replaceAll("ё", "е");
}

export function helpSearchTerms(query: string): string[] {
    return [...new Set(normalizeHelpText(query).match(/[\p{L}\p{N}_:-]+/gu) ?? [])].filter(term => !STOP_WORDS.has(term));
}

export function helpHighlights(text: string, query: string): { text: string; match: boolean }[] {
    const terms = helpSearchTerms(query);
    if (!terms.length) return [{ text, match: false }];
    const expression = new RegExp(terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).sort((a, b) => b.length - a.length).join("|"), "gu");
    const parts: { text: string; match: boolean }[] = [];
    let cursor = 0;
    for (const match of normalizeHelpText(text).matchAll(expression)) {
        if (match.index > cursor) parts.push({ text: text.slice(cursor, match.index), match: false });
        parts.push({ text: text.slice(match.index, match.index + match[0].length), match: true });
        cursor = match.index + match[0].length;
    }
    if (cursor < text.length) parts.push({ text: text.slice(cursor), match: false });
    return parts;
}

export function searchHelp(query: string, sections: HelpSection[] = HELP_SECTIONS): HelpSearchResult[] {
    const terms = helpSearchTerms(query);
    if (!terms.length) return [];
    const results: (HelpSearchResult & { score: number })[] = [];
    for (const section of sections) for (const article of section.articles) {
        if (article.catalog) {
            for (const entry of findCatalogEntries(article.catalog, terms.filter(term => term !== article.catalog))) {
                const title = `${entry.kind === "action" ? "Действие · " : ""}${entry.name} · ${entry.summary}`;
                results.push({ section, article: { id: entry.id, title, paragraphs: [entry.description] },
                    excerpt: `${entry.kind === "action" ? "Через telos_execute_project_tool. " : ""}${entry.description || entry.summary}`.slice(0, 260),
                    score: terms.filter(term => normalizeHelpText(title).includes(term)).length - 1 });
            }
        }
        const body = [...article.paragraphs, ...(article.steps ?? []), ...(article.bullets ?? [])].join(" ");
        const title = normalizeHelpText(`${section.title} ${article.title}`);
        const text = normalizeHelpText(`${title} ${body} ${article.keywords ?? ""}`);
        if (!terms.every(term => text.includes(term))) continue;
        const normalizedBody = normalizeHelpText(body);
        const positions = terms.map(term => normalizedBody.indexOf(term)).filter(position => position >= 0);
        const start = Math.max(0, (positions.length ? Math.min(...positions) : 0) - 64);
        const excerpt = `${start ? "…" : ""}${body.slice(start, start + 240).trim()}${body.length > start + 240 ? "…" : ""}`;
        results.push({ section, article, excerpt, score: terms.filter(term => title.includes(term)).length });
    }
    return results.sort((a, b) => b.score - a.score);
}
