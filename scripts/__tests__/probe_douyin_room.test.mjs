import assert from "node:assert/strict";
import test from "node:test";

import {
  detectRoom,
  extractDirectStreamUrl,
  extractStreamUrlCandidates,
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
