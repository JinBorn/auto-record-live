import assert from "node:assert/strict";
import test from "node:test";

import {
  detectRoom,
  extractDirectStreamUrl,
  extractStreamUrlCandidates,
  pickPreferredStreamUrl,
} from "../probe_douyin_room.mjs";

test("extractDirectStreamUrl prefers hls m3u8 url when multiple candidates exist", () => {
  const html = `
    <html><body>
      <script>
        window.__DATA__ = {
          "hls_pull_url":"https:\\/\\/live-play.example.com\\/stream\\/abc123.m3u8?token=a\\u0026ts=1",
          "flv_pull_url":"https:\\/\\/live-play.example.com\\/stream\\/abc123.flv?token=b"
        };
      </script>
    </body></html>
  `;

  const url = extractDirectStreamUrl(html);
  assert.equal(
    url,
    "https://live-play.example.com/stream/abc123.m3u8?token=a&ts=1",
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
  const candidates = extractStreamUrlCandidates("https://cdn.example/live/room.m3u8?token=abc");
  assert.equal(candidates[0], "https://cdn.example/live/room.m3u8?token=abc");
});

test("extractStreamUrlCandidates decodes percent-encoded stream urls", () => {
  const candidates = extractStreamUrlCandidates(
    "https%3A%2F%2Fpull.example.com%2Flive%2Froom.m3u8%3Ftoken%3Dabc",
  );
  assert.equal(candidates[0], "https://pull.example.com/live/room.m3u8?token=abc");
});

test("extractStreamUrlCandidates decodes multi-layer encoded and x-escaped stream urls", () => {
  const candidates = extractStreamUrlCandidates(
    '{"stream_url":"\\x68\\x74\\x74\\x70\\x73%253A%252F%252Fpull.example.com%252Flive%252Froom.m3u8%253Ftoken%253Dxyz"}',
  );
  assert.equal(candidates[0], "https://pull.example.com/live/room.m3u8?token=xyz");
});

test("pickPreferredStreamUrl prefers m3u8 over flv candidates", () => {
  const preferred = pickPreferredStreamUrl([
    "https://cdn.example/live/room.flv?token=1",
    "https://cdn.example/live/room.m3u8?token=1",
  ]);
  assert.equal(preferred, "https://cdn.example/live/room.m3u8?token=1");
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
    observedUrls: ["https://pull.example.com/stream/abc123.m3u8?token=1"],
  });

  assert.equal(result.state, "live");
  assert.equal(result.sourceType, "direct_stream");
  assert.equal(result.reason, "stream_url_detected");
  assert.equal(
    result.streamUrl,
    "https://pull.example.com/stream/abc123.m3u8?token=1",
  );
});
