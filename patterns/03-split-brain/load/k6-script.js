import http from 'k6/http';
import { check, sleep } from 'k6';

// Both node URLs are reachable on quorum-net regardless of whether
// cluster-net (the peer channel) is currently partitioned -- clients
// don't know or care about the partition, they just talk to whichever
// node they're connected to. That's the point: unlike Patterns 01/02,
// this script deliberately does NOT target "the leader" -- it picks a
// node at random per write, exactly like real traffic that has no idea
// a partition is happening.
const NODE1_URL = __ENV.NODE1_URL || 'http://node-1:8000';
const NODE2_URL = __ENV.NODE2_URL || 'http://node-2:8000';
const RATE = parseInt(__ENV.RATE || '15', 10);
const DURATION = __ENV.DURATION || '20s';
const KEY_COUNT = parseInt(__ENV.KEY_COUNT || '20', 10);

export const options = {
  scenarios: {
    load: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  // k6's default summaryTrendStats omits p(99) -- Pattern 01 found this
  // the hard way. Not repeating that here.
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export default function () {
  const key = `key${Math.floor(Math.random() * KEY_COUNT)}`;
  const value = `v-${__VU}-${__ITER}-${Math.floor(Math.random() * 1000000)}`;
  const targetUrl = Math.random() < 0.5 ? NODE1_URL : NODE2_URL;

  const res = http.put(`${targetUrl}/kv/${key}`, JSON.stringify({ value }), {
    headers: { 'Content-Type': 'application/json' },
  });
  // 200 = this node believed it was leader and accepted the write.
  // 503 = this node correctly knows it's not leader and rejected it.
  // Both are valid outcomes depending on which node k6 happened to hit --
  // this pattern's pass/fail doesn't come from k6's own check/error rate,
  // it comes from directly comparing both nodes' stored data afterward
  // (see verify.py's divergence computation).
  check(res, { 'got a response': (r) => r.status === 200 || r.status === 503 });
  sleep(0.05);
}
