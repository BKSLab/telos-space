/**
 * Native vector brand masters and deterministic web encodings.
 * Run from frontend: node scripts/build-brand-assets.mjs
 * The approved PNGs and imagegen avatar master in docs/logo are kept intact.
 */
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const publicDir = path.join(root, "frontend/public");
const brandDir = path.join(publicDir, "brand");
await mkdir(brandDir, { recursive: true });

const gradients = `<defs>
  <linearGradient id="ts-spectrum" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#00d5ed"/><stop offset=".45" stop-color="#4199ff"/><stop offset="1" stop-color="#cd58f4"/></linearGradient>
  <linearGradient id="ts-left" x1="0" y1="0" x2=".5" y2="1"><stop stop-color="#b9eaff"/><stop offset=".56" stop-color="#1685ff"/><stop offset="1" stop-color="#075583"/></linearGradient>
  <linearGradient id="ts-right" x1="0" y1="0" x2=".5" y2="1"><stop stop-color="#b9b6ff"/><stop offset=".55" stop-color="#a552ed"/><stop offset="1" stop-color="#513487"/></linearGradient>
  <linearGradient id="ts-dot" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#00c9fa"/><stop offset="1" stop-color="#9ab9ff"/></linearGradient>
  <linearGradient id="ts-wordmark" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="321" y2="0"><stop stop-color="#00c8f4"/><stop offset=".45" stop-color="#577bff"/><stop offset="1" stop-color="#cf58f1"/></linearGradient>
</defs>`;

// The arc, point and split ascending chevron trace the approved geometric mark.
function mark(mono = false, small = false) {
    const color = (id) => mono ? "#e8e8e8" : `url(#ts-${id})`;
    const arc = small
        ? `<path d="M18 87 A51 51 0 1 1 110 87" fill="none" stroke="${color("spectrum")}" stroke-width="8" stroke-linecap="round"/>`
        : `<path d="M18 91C-4 52 10 6 64 6S132 52 110 91C124 48 103 18 64 18S4 48 18 91Z" fill="${color("spectrum")}"/>`;
    return `${arc}<circle cx="64" cy="44" r="8" fill="${color("dot")}"/>
<path d="M64 57 8 116H35L64 85Z" fill="${color("left")}"/>
<path d="M64 57 120 116H93L64 85Z" fill="${color("right")}"/>`;
}

// Outlined lettering keeps the wordmark independent of installed/web fonts.
const glyphs = {
    T: "M3 4H51M27 4V67",
    E: "M50 4H5V67H50M5 35H44",
    L: "M5 4V67H50",
    O: "M27 3C8 3 2 14 2 35S8 68 27 68 52 56 52 35 46 3 27 3Z",
    S: "M49 8C37 0 5-1 4 18C3 36 48 30 49 49C50 70 18 73 3 61",
    P: "M5 67V4H29C44 4 51 9 51 22S44 40 29 40H5",
    A: "M2 67 27 4 53 67",
    C: "M50 9C41 1 28 0 17 5C6 10 2 21 2 35S6 61 17 66C28 71 41 69 50 61",
};

function offsetGlyph(source, offset) {
    let command = "";
    let coordinate = 0;
    return source.replace(/[A-Z]|-?\d+(?:\.\d+)?/g, token => {
        if (/^[A-Z]$/.test(token)) { command = token; coordinate = 0; return token; }
        const horizontal = command === "H" || (command !== "V" && coordinate % 2 === 0);
        coordinate++;
        return String(Number(token) + (horizontal ? offset : 0));
    });
}

function word(text, x, mono = false, colored = false) {
    const outline = [...text].map((letter, index) => offsetGlyph(glyphs[letter], index * 67)).join(" ");
    return `<path transform="translate(${x} 45)" d="${outline}" fill="none" stroke="${mono || !colored ? "#e8e8e8" : "url(#ts-wordmark)"}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>`;
}

function logoBody(mono = false, tagline = false) {
    return `${gradients}<g transform="translate(8 16)">${mark(mono)}</g>${word("TELOS", 176, mono)}${word("SPACE", 552, mono, true)}${tagline ? '<text x="176" y="158" fill="#b9bec8" font-family="Arial, sans-serif" font-size="21" letter-spacing="6.5">ПРОСТРАНСТВО СМЫСЛОВ</text>' : ""}`;
}

const svg = (viewBox, body) => {
    const [, , width, height] = viewBox.split(" ");
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${viewBox}" fill="none">${body}</svg>\n`;
};
const symbol = svg("0 0 128 128", gradients + mark());
const logo = svg("0 0 900 160", logoBody());
const logoTagline = svg("0 0 900 190", logoBody(false, true));
const logoMono = svg("0 0 900 160", logoBody(true));
const symbolMono = svg("0 0 128 128", mark(true));
const favicon = svg("0 0 128 128", `${gradients}<rect width="128" height="128" rx="27" fill="#1f1f1f"/><g transform="translate(9 9) scale(.86)">${mark(false, true)}</g>`);

for (const [name, source] of Object.entries({
    "telos-symbol.svg": symbol,
    "telos-logo.svg": logo,
    "telos-logo-tagline.svg": logoTagline,
    "telos-logo-mono.svg": logoMono,
    "telos-symbol-mono.svg": symbolMono,
})) await writeFile(path.join(brandDir, name), source);
await writeFile(path.join(publicDir, "favicon.svg"), favicon);

let browser;
try {
    browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
} catch {
    browser = await chromium.launch();
}

try {
    const page = await browser.newPage();
    async function encode(source, width, height, type = "image/png", quality = .88) {
        const dataUrl = await page.evaluate(async ({ source, width, height, type, quality }) => {
            const picture = new Image();
            picture.src = source;
            await picture.decode();
            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;
            const context = canvas.getContext("2d");
            context.imageSmoothingQuality = "high";
            context.drawImage(picture, 0, 0, width, height);
            return canvas.toDataURL(type, quality);
        }, { source, width, height, type, quality });
        if (!dataUrl.startsWith(`data:${type};base64,`)) throw new Error(`Browser cannot encode ${type}`);
        return Buffer.from(dataUrl.split(",")[1], "base64");
    }
    const svgSource = (source) => `data:image/svg+xml;base64,${Buffer.from(source).toString("base64")}`;
    const pngSource = async (filename) => `data:image/png;base64,${(await readFile(filename)).toString("base64")}`;
    const avatarSource = await pngSource(path.join(root, "docs/logo/ARIS_AVATAR_MASTER.png"));
    for (const size of [64, 128, 256, 512]) {
        await writeFile(path.join(brandDir, `aris-avatar-${size}.webp`), await encode(avatarSource, size, size, "image/webp"));
    }
    const portraitSource = await pngSource(path.join(root, "docs/logo/Футуристичный портрет мыслителя в стекле.png"));
    const portrait = await encode(portraitSource, 768, 768, "image/webp", .9);
    await writeFile(path.join(brandDir, "aris-portrait.webp"), portrait);

    const icoPngs = [];
    for (const size of [16, 32, 48]) {
        const png = await encode(svgSource(favicon), size, size);
        icoPngs.push(png);
        if (size !== 48) await writeFile(path.join(publicDir, `favicon-${size}.png`), png);
    }
    // ICO directory + PNG entries: 16, 32 and 48 px, with full alpha.
    const header = Buffer.alloc(6 + icoPngs.length * 16);
    header.writeUInt16LE(1, 2);
    header.writeUInt16LE(icoPngs.length, 4);
    let offset = header.length;
    icoPngs.forEach((png, index) => {
        const entry = 6 + index * 16;
        header[entry] = header[entry + 1] = [16, 32, 48][index];
        header.writeUInt16LE(1, entry + 4);
        header.writeUInt16LE(32, entry + 6);
        header.writeUInt32LE(png.length, entry + 8);
        header.writeUInt32LE(offset, entry + 12);
        offset += png.length;
    });
    await writeFile(path.join(publicDir, "favicon.ico"), Buffer.concat([header, ...icoPngs]));

    const deviceIcon = svg("0 0 128 128", `${gradients}<rect width="128" height="128" fill="#1f1f1f"/><g transform="translate(18 18) scale(.72)">${mark()}</g>`);
    for (const [name, size] of [["apple-touch-icon.png", 180], ["icon-192.png", 192], ["icon-512.png", 512]]) {
        await writeFile(path.join(publicDir, name), await encode(svgSource(deviceIcon), size, size));
    }
    // The entire mark remains inside the central maskable safe circle.
    const maskableIcon = svg("0 0 128 128", `${gradients}<rect width="128" height="128" fill="#1f1f1f"/><g transform="translate(27 27) scale(.58)">${mark()}</g>`);
    await writeFile(path.join(publicDir, "icon-maskable-512.png"), await encode(svgSource(maskableIcon), 512, 512));

    const presentation = svg("0 0 1600 900", `<defs><radialGradient id="cover-glow"><stop stop-color="#275273" stop-opacity=".3"/><stop offset="1" stop-color="#181818" stop-opacity="0"/></radialGradient></defs><rect width="1600" height="900" fill="#181818"/><ellipse cx="220" cy="280" rx="780" ry="600" fill="url(#cover-glow)"/><path d="M90 660H1510" stroke="#3c3c3c"/><g transform="translate(115 240) scale(1.45)">${logoBody(false, true)}</g><text x="135" y="730" fill="#cccccc" font-size="30" font-family="Arial, sans-serif">Цель → Контекст → Действие → Результат</text><text x="135" y="788" fill="#9b8cff" font-size="23" font-family="Arial, sans-serif">Вместе с ARIS</text>`);
    await writeFile(path.join(brandDir, "presentation-cover.svg"), presentation);
    const cover = svg("0 0 1200 630", `<defs><linearGradient id="cover-bg"><stop stop-color="#181818"/><stop offset="1" stop-color="#252536"/></linearGradient><linearGradient id="cover-fade"><stop stop-color="#181818"/><stop offset=".4" stop-color="#181818" stop-opacity="0"/></linearGradient></defs><rect width="1200" height="630" fill="url(#cover-bg)"/><image href="data:image/webp;base64,${portrait.toString("base64")}" x="635" y="0" width="630" height="630"/><rect x="635" width="200" height="630" fill="url(#cover-fade)"/><g transform="translate(55 185) scale(.76)">${logoBody(false, true)}</g><text x="72" y="435" fill="#cccccc" font-size="23" font-family="Arial, sans-serif">От цели и контекста к результату</text><text x="72" y="482" fill="#9b8cff" font-size="21" font-family="Arial, sans-serif">Ваш проектный помощник — ARIS</text>`);
    await writeFile(path.join(brandDir, "share-cover.png"), await encode(svgSource(cover), 1200, 630));
} finally {
    await browser.close();
}

await writeFile(path.join(publicDir, "site.webmanifest"), JSON.stringify({
    id: "/",
    name: "TELOS SPACE",
    short_name: "TELOS SPACE",
    description: "Пространство смыслов: проекты, знания, задачи и результаты вместе с Арисом.",
    lang: "ru",
    start_url: "/",
    scope: "/",
    display: "standalone",
    prefer_related_applications: false,
    categories: ["productivity", "business"],
    shortcuts: [
        { name: "Мои задачи", url: "/my-tasks", icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }] },
        { name: "Чаты и Арис", url: "/chat", icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }] },
        { name: "Доски", url: "/boards", icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }] },
    ],
    background_color: "#181818",
    theme_color: "#181818",
    icons: [
        { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
        { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
        { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
}, null, 2) + "\n");

console.log("TELOS SPACE: vector logos, avatar encodings, favicons, device icons, manifest and covers built.");
