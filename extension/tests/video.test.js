import test from 'node:test';
import assert from 'node:assert/strict';
import { formatTime, replayUrl, videoId } from '../src/video-context.js';

const url = 'https://www.youtube.com/watch?v=N-q2z4N_Rsk';
test('video URL forms route by exact allowed host and replay never invents time', () => {
  for (const input of [url, 'https://youtu.be/N-q2z4N_Rsk', 'https://www.youtube-nocookie.com/embed/N-q2z4N_Rsk']) assert.equal(videoId(input), 'N-q2z4N_Rsk');
  for (const input of ['https://youtube.com.evil/watch?v=N-q2z4N_Rsk', 'javascript:alert(1)', 'https://youtu.be/bad']) assert.equal(videoId(input), null);
  for (const timestamp_seconds of [null, undefined, NaN, Infinity, -1, '141', 141.5, 90000]) assert.equal(replayUrl({ video_url: url, timestamp_seconds }, { url }), null);
  assert.equal(replayUrl({ video_url: url, timestamp_seconds: 141 }, { url }), `${url}&t=141s`);
  assert.equal(replayUrl({ video_url: url, timestamp_seconds: 141 }, { url, video_duration_seconds: 10 }), null);
  assert.equal(formatTime(141.8), '2:21');
  assert.equal(formatTime(3725), '1:02:05');
});
