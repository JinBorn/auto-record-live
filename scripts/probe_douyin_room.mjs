import { chromium } from "playwright";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

function buildResult(fields = {}) {
  return {
    ok: true,
    state: "offline",
    sourceType: null,
    streamUrl: null,
    reason: "unknown",
    pageTitle: null,
    ...fields,
  };
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) {
      continue;
    }
    const key = token.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = "1";
      continue;
    }
    args[key] = next;
    i += 1;
  }
  return args;
}

function normalizeEscapedUrl(rawValue) {
  let normalized = String(rawValue)
    .replace(/\\u([0-9a-fA-F]{4})/g, (_, hex) =>
      String.fromCharCode(Number.parseInt(hex, 16)),
    )
    .replace(/\\x([0-9a-fA-F]{2})/g, (_, hex) =>
      String.fromCharCode(Number.parseInt(hex, 16)),
    )
    .replace(/\\\//g, "/")
    .replace(/&amp;/g, "&");

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const lowered = normalized.toLowerCase();
    if (!/^https?%[0-9a-f]{2}/.test(lowered)) {
      break;
    }

    const previous = normalized;
    try {
      normalized = decodeURIComponent(normalized);
    } catch {
      break;
    }
    if (normalized === previous) {
      break;
    }
  }

  return normalized;
}

function isLikelyStreamUrl(rawUrl) {
  if (!rawUrl) {
    return false;
  }

  const url = rawUrl.trim();
  const lower = url.toLowerCase();
  if (!(lower.startsWith("https://") || lower.startsWith("http://"))) {
    return false;
  }

  const noQuery = lower.split("?")[0];
  const blockedSuffixes = [
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
  ];
  if (blockedSuffixes.some((suffix) => noQuery.endsWith(suffix))) {
    return false;
  }

  if (lower.includes(".m3u8") || lower.includes(".flv")) {
    return true;
  }

  return lower.includes("pull") && (lower.includes("stream") || lower.includes("live"));
}

function streamUrlScore(url) {
  const lower = url.toLowerCase();
  let score = 0;

  if (lower.includes(".m3u8")) {
    score += 50;
  }
  if (lower.includes(".flv")) {
    score += 40;
  }
  if (lower.includes("hls")) {
    score += 10;
  }
  if (lower.includes("pull")) {
    score += 8;
  }
  if (lower.includes("stream")) {
    score += 6;
  }
  if (lower.includes("live")) {
    score += 4;
  }

  return score;
}

function sortStreamUrlCandidates(candidates) {
  return Array.from(candidates).sort(
    (left, right) => streamUrlScore(right) - streamUrlScore(left),
  );
}

function extractStreamUrlCandidates(rawText) {
  if (!rawText) {
    return [];
  }

  const text = String(rawText);
  const candidates = new Set();
  const keyPatterns = [
    /"(?:streamUrl|stream_url|hls_pull_url|flv_pull_url|main_hls|main_flv|origin_hls|origin_flv)"\s*:\s*"([^"]+)"/gi,
  ];
  for (const pattern of keyPatterns) {
    for (const match of text.matchAll(pattern)) {
      const normalized = normalizeEscapedUrl(match[1]);
      if (isLikelyStreamUrl(normalized)) {
        candidates.add(normalized);
      }
    }
  }

  for (const match of text.matchAll(/https?:\\?\/\\?\/[^\s"'<>\\]+/gi)) {
    const normalized = normalizeEscapedUrl(match[0]);
    if (isLikelyStreamUrl(normalized)) {
      candidates.add(normalized);
    }
  }
  for (const match of text.matchAll(/https?%(?:25)*3a%(?:25)*2f%(?:25)*2f[^\s"'<>\\]+/gi)) {
    const normalized = normalizeEscapedUrl(match[0]);
    if (isLikelyStreamUrl(normalized)) {
      candidates.add(normalized);
    }
  }

  return sortStreamUrlCandidates(candidates);
}

function pickPreferredStreamUrl(rawCandidates) {
  const uniqueCandidates = new Set();
  for (const rawCandidate of rawCandidates) {
    for (const candidate of extractStreamUrlCandidates(rawCandidate)) {
      uniqueCandidates.add(candidate);
    }
  }

  return sortStreamUrlCandidates(uniqueCandidates)[0] || null;
}

function extractDirectStreamUrl(html) {
  return pickPreferredStreamUrl([html]);
}

async function detectRoom(page, options = {}) {
  const title = await page.title();
  const html = await page.content();
  const observedUrls = Array.isArray(options.observedUrls) ? options.observedUrls : [];
  const streamUrl = pickPreferredStreamUrl([html, ...observedUrls]);

  const liveMarkers = [
    '"status":2',
    '"live_status":2',
    '"is_live":true',
    "直播中",
  ];
  if (liveMarkers.some((marker) => html.includes(marker))) {
    return buildResult({
      state: "live",
      sourceType: streamUrl ? "direct_stream" : "browser_capture",
      streamUrl,
      reason: "page_marker_detected",
      pageTitle: title,
    });
  }

  const offlineMarkers = [
    '"status":4',
    '"live_status":4',
    "暂未开播",
    "还没开播",
  ];
  if (offlineMarkers.some((marker) => html.includes(marker))) {
    return buildResult({
      state: "offline",
      reason: "page_marker_detected",
      pageTitle: title,
    });
  }

  if (streamUrl) {
    return buildResult({
      state: "live",
      sourceType: "direct_stream",
      streamUrl,
      reason: "stream_url_detected",
      pageTitle: title,
    });
  }

  return buildResult({
    state: "offline",
    reason: "live_state_unknown",
    pageTitle: title,
  });
}

async function main() {
  const args = parseArgs(process.argv);
  const roomUrl = args["room-url"];
  const profileDir = args["profile-dir"] || "data/tmp/chrome-profile";
  const timeoutMs = Number.parseInt(args["timeout-ms"] || "20000", 10);
  const headless = args["headless"] === "1";

  if (!roomUrl) {
    console.log(JSON.stringify({
      ok: false,
      error: "room_url_missing",
    }));
    process.exit(2);
  }

  const browser = await chromium.launchPersistentContext(profileDir, {
    headless,
    viewport: { width: 1440, height: 900 },
  });

  try {
    const page = browser.pages()[0] || await browser.newPage();
    const observedStreamUrls = new Set();
    const collectObservedStreamUrls = (rawValue) => {
      for (const candidate of extractStreamUrlCandidates(rawValue)) {
        observedStreamUrls.add(candidate);
      }
    };

    page.on("request", (request) => {
      collectObservedStreamUrls(request.url());
    });
    page.on("response", (response) => {
      collectObservedStreamUrls(response.url());
      const contentType = response.headers()["content-type"] || "";
      if (!contentType.includes("json")) {
        return;
      }

      void response.text()
        .then((text) => {
          collectObservedStreamUrls(text);
        })
        .catch(() => {
          // best-effort probe only; ignore body read errors
        });
    });

    await page.goto(roomUrl, {
      timeout: timeoutMs,
      waitUntil: "domcontentloaded",
    });
    await page.waitForTimeout(2000);
    const result = await detectRoom(page, {
      observedUrls: Array.from(observedStreamUrls),
    });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.log(JSON.stringify({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    }));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

const isDirectExecution =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectExecution) {
  await main();
}

export {
  detectRoom,
  extractDirectStreamUrl,
  extractStreamUrlCandidates,
  parseArgs,
  pickPreferredStreamUrl,
};
