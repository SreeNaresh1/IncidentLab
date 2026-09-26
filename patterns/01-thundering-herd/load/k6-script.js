import http from 'k6/http';
import { check, sleep } from 'k6';

// TARGET_URL is fixed to the app's in-network address by run_load() in
// tools/lib/experiment.sh — k6 runs as a Compose service on the same
// Docker network as the app, so it reaches it by service name.
const BASE_URL = __ENV.TARGET_URL || 'http://app:8000';
const PRODUCT_ID = __ENV.PRODUCT_ID || '42'; // the single hot key
const VUS = parseInt(__ENV.VUS || '20', 10);
const DURATION = __ENV.DURATION || '30s';

export const options = {
  summaryTrendStats: [
    'avg',
    'min',
    'med',
    'max',
    'p(90)',
    'p(95)',
    'p(99)',
  ],

  scenarios: {
    load: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURATION,
    },
  },
};
export default function () {
  const res = http.get(`${BASE_URL}/product/${PRODUCT_ID}`);
  check(res, { 'status is 200': (r) => r.status === 200 });
  // Small per-VU jitter so requests aren't perfectly lockstep — the herd
  // still forms because every VU shares the same cache key and TTL.
  sleep(Math.random() * 0.3);
}
