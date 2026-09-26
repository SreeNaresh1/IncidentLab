import http from 'k6/http';
import { check } from 'k6';

// TARGET_URL is fixed to service-a's in-network address by run_load() in
// tools/lib/experiment.sh — k6 runs as a Compose service on the same
// Docker network, so it reaches it by service name.
const BASE_URL = __ENV.TARGET_URL || 'http://service-a:8000';
const DURATION = __ENV.DURATION   || '30s';

// All stages (BASELINE, BREAK, FIX) use open-loop constant-arrival-rate
// with the exact same offered arrival rate (100 req/s) for complete
// scientific comparability across stages.
const EXECUTOR  = __ENV.EXECUTOR  || 'constant-arrival-rate';
const RATE      = parseInt(__ENV.RATE       || '100', 10);  // iterations/s
const PRE_ALLOC = parseInt(__ENV.PRE_ALLOC  || '200', 10);
const MAX_VUS   = parseInt(__ENV.MAX_VUS    || '1000', 10);

export const options = (function () {
  if (EXECUTOR === 'constant-vus') {
    const VUS = parseInt(__ENV.VUS || '10', 10);
    return {
      scenarios: {
        load: {
          executor: 'constant-vus',
          vus:      VUS,
          duration: DURATION,
        },
      },
      summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
    };
  }
  // Default for all stages: constant-arrival-rate (open-loop)
  return {
    scenarios: {
      load: {
        executor:        'constant-arrival-rate',
        rate:            RATE,
        timeUnit:        '1s',
        duration:        DURATION,
        preAllocatedVUs: PRE_ALLOC,
        maxVUs:          MAX_VUS,
      },
    },
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  };
}());

export default function () {
  const res = http.get(`${BASE_URL}/checkout`);
  check(res, { 'status is 200': (r) => r.status === 200 });
}
