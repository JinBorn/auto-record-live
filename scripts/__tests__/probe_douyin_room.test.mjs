import assert from "node:assert/strict";
import test from "node:test";

import {
  detectRoom,
  extractDirectStreamUrl,
  extractStreamUrlCandidates,
  parseCookieString,
  pickPreferredStreamUrl,
} from "../probe_douyin_room.mjs";

// All fixtures must include sign= or wsSecret= in the query string because
// isLikelyStreamUrl() now rejects unsigned URLs (Douyin's CDN 403s any pull
// without a signing token, so unsigned candidates would be unrecordable
// even when they advertise a higher quality tier).

test("extractDirectStreamUrl prefers hls m3u8 url when multiple candidates exist", () => {
  const html = `
    <html><body>
      <script>
        window.__DATA__ = {
          "hls_pull_url":"https:\/\/live-play.example.com\/stream\/abc123.m3u8?sign=hls&ts=1",
          "flv_pull_url":"https:\/\/live-play.example.com\/stream\/abc123.flv?sign=flv"
        };
      </script>
    </body></html>
  `;

  const url = extractDirectStreamUrl(html);
  assert.equal(
    url,
    "https://live-play.example.com/stream/abc123.m3u8?sign=hls&ts=1",
  );
});

test("extractDirectStreamUrl ignores static asset urls", () => {
  const html = `
    <html><body>
      <script src="https://cdn.example.com/assets/app.js"></script>
      <img src="https://cdn.example.com/assets/live.png" />
    </body></html>
  `;

  const url = extractDirectStreamUrl(html);
  assert.equal(url, null);
});

test("extractDirectStreamUrl returns null when no stream link is present", () => {
  const html = `<html><body><div>暂未开播</div></body></html>`;
  assert.equal(extractDirectStreamUrl(html), null);
});

test("extractStreamUrlCandidates parses direct url input", () => {
  const candidates = extractStreamUrlCandidates("https://cdn.example/live/room.m3u8?sign=abc");
  assert.equal(candidates[0], "https://cdn.example/live/room.m3u8?sign=abc");
});

test("extractStreamUrlCandidates decodes percent-encoded stream urls", () => {
  const candidates = extractStreamUrlCandidates(
    "https%3A%2F%2Fpull.example.com%2Flive%2Froom.m3u8%3Fsign%3Dabc",
  );
  assert.equal(candidates[0], "https://pull.example.com/live/room.m3u8?sign=abc");
});

test("extractStreamUrlCandidates decodes multi-layer encoded and x-escaped stream urls", () => {
  const candidates = extractStreamUrlCandidates(
    '{"stream_url":"\x68\x74\x74\x70\x73%253A%252F%252Fpull.example.com%252Flive%252Froom.m3u8%253Fsign%253Dxyz"}',
  );
  assert.equal(candidates[0], "https://pull.example.com/live/room.m3u8?sign=xyz");
});

test("pickPreferredStreamUrl prefers m3u8 over flv candidates", () => {
  const preferred = pickPreferredStreamUrl([
    "https://cdn.example/live/room.flv?sign=f",
    "https://cdn.example/live/room.m3u8?sign=h",
  ]);
  assert.equal(preferred, "https://cdn.example/live/room.m3u8?sign=h");
});

test("detectRoom marks live when stream url exists in observed network urls", async () => {
  const page = {
    async title() {
      return "直播间";
    },
    async content() {
      return "<html><body><div>unknown state</div></body></html>";
    },
  };

  const result = await detectRoom(page, {
    observedUrls: ["https://pull.example.com/stream/abc123.m3u8?sign=ob"],
  });

  assert.equal(result.state, "live");
  assert.equal(result.sourceType, "direct_stream");
  assert.equal(result.reason, "stream_url_detected");
  assert.equal(
    result.streamUrl,
    "https://pull.example.com/stream/abc123.m3u8?sign=ob",
  );
});

test("isLikelyStreamUrl rejects unsigned URLs even when path looks like a stream", () => {
  // The unsigned _uhd master playlist Douyin embeds in the page DOM —
  // matches every other heuristic (m3u8, pull, stream) but lacks sign=.
  // Must NOT be returned as a candidate.
  const candidates = extractStreamUrlCandidates(
    "http://pull-x3-q5-hls.douyincdn.com/thirdgame/stream-1_uhd/index.m3u8?expire=999",
  );
  assert.equal(candidates.length, 0);
});

test("isLikelyStreamUrl accepts wsSecret= as alternative signing parameter", () => {
  const candidates = extractStreamUrlCandidates(
    "http://pull-hls.douyincdn.com/thirdgame/stream-1_md/playlist.m3u8?wsSecret=abc&wsTime=1",
  );
  assert.equal(candidates.length, 1);
});

test("parseCookieString turns header string into Playwright cookie array with .douyin.com domain", () => {
  const cookies = parseCookieString("k1=v1; k2=v2; sid=abc123");
  assert.deepEqual(cookies, [
    { name: "k1", value: "v1", domain: ".douyin.com", path: "/" },
    { name: "k2", value: "v2", domain: ".douyin.com", path: "/" },
    { name: "sid", value: "abc123", domain: ".douyin.com", path: "/" },
  ]);
});

test("parseCookieString skips empty / malformed pairs and trims whitespace", () => {
  // Empty input → empty array (defends addCookies from receiving []).
  assert.deepEqual(parseCookieString(""), []);
  assert.deepEqual(parseCookieString(null), []);
  // Trailing semicolons, double semicolons, and pairs without "=" must
  // be silently dropped — not throw — so a slightly malformed cookie
  // header from F12 doesn't kill the probe.
  const cookies = parseCookieString("  a = 1 ; ; broken ; b=2;");
  assert.deepEqual(cookies, [
    { name: "a", value: "1", domain: ".douyin.com", path: "/" },
    { name: "b", value: "2", domain: ".douyin.com", path: "/" },
  ]);
});

test("parseCookieString preserves '=' inside cookie value (e.g. base64 padding)", () => {
  const cookies = parseCookieString("token=abc==; sid=q==");
  assert.deepEqual(cookies, [
    { name: "token", value: "abc==", domain: ".douyin.com", path: "/" },
    { name: "sid", value: "q==", domain: ".douyin.com", path: "/" },
  ]);
});

test("extractStreamUrlCandidates accepts signed _uhd URL whose query is JSON-escaped with \\u0026", () => {
  // Regression: Douyin's HTML JSON-encodes & as &. Earlier the URL
  // regex excluded backslash, truncating the match before &sign=
  // and dropping every signed _uhd URL as unsigned. The fix removes
  // backslash from the negated char class and lets normalizeEscapedUrl
  // decode & → & before the signing check.
  const html =
    '"main":{"flv":"http://pull-flv-q13.douyincdn.com/thirdgame/' +
    'stream-1_uhd.flv?expire=99\\u0026sign=abcdef\\u0026t=1"}';
  const candidates = extractStreamUrlCandidates(html);
  assert.ok(
    candidates.some((u) => u.includes("_uhd.flv") && u.includes("sign=abcdef")),
    `expected signed _uhd URL in candidates, got: ${JSON.stringify(candidates)}`,
  );
});
