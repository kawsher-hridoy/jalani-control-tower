// Decision-path load: hits the planning path (snapshot -> intel -> LP -> projection)
// without executing anything, via POST /api/plan/preview. Arrival-rate executor so
// the request rate is controlled directly regardless of response latency. Run with:
//   docker run --rm --network host -v "$PWD/loadtest:/loadtest" grafana/k6:0.54.0 run /loadtest/k6/decision.js
import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const errors = new Rate('errors');

export const options = {
  scenarios: {
    plan_preview: {
      executor: 'ramping-arrival-rate',
      startRate: 2,
      timeUnit: '1s',
      preAllocatedVUs: 30,
      maxVUs: 100,
      stages: [
        { duration: '40s', target: 10 },
        { duration: '40s', target: 20 },
        { duration: '40s', target: 20 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.02'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export default function () {
  const res = http.post(`${BASE_URL}/api/plan/preview`, JSON.stringify({}), {
    headers: { 'Content-Type': 'application/json' },
  });
  const ok = check(res, { 'status is 200': (r) => r.status === 200 });
  errors.add(!ok);
}

// ---- summary helpers (no external deps, so `k6 inspect`/`run` work offline) ----

function metricValues(data, name) {
  return (data.metrics[name] && data.metrics[name].values) || {};
}

function summarize(data) {
  const dur = metricValues(data, 'http_req_duration');
  const reqs = metricValues(data, 'http_reqs');
  const errRate = metricValues(data, 'errors');
  return {
    requests: reqs.count || 0,
    rps: reqs.rate || 0,
    error_rate: errRate.rate || 0,
    latency_ms: {
      avg: dur.avg || 0,
      p50: dur.med || 0,
      p90: dur['p(90)'] || 0,
      p95: dur['p(95)'] || 0,
      p99: dur['p(99)'] || 0,
      max: dur.max || 0,
    },
  };
}

function textReport(title, s) {
  const row = (label, value) => `  ${label.padEnd(10)}${value}\n`;
  let out = `\n${title}\n${'='.repeat(title.length)}\n`;
  out += row('requests', s.requests);
  out += row('rps', s.rps.toFixed(2));
  out += row('errors', `${(s.error_rate * 100).toFixed(2)}%`);
  out += '  latency (ms)\n';
  out += row('  avg', s.latency_ms.avg.toFixed(2));
  out += row('  p50', s.latency_ms.p50.toFixed(2));
  out += row('  p90', s.latency_ms.p90.toFixed(2));
  out += row('  p95', s.latency_ms.p95.toFixed(2));
  out += row('  p99', s.latency_ms.p99.toFixed(2));
  out += row('  max', s.latency_ms.max.toFixed(2));
  return out;
}

export function handleSummary(data) {
  const summary = summarize(data);
  return {
    stdout: textReport('Jalani decision-path load test', summary),
    '/loadtest/results/decision-summary.json': JSON.stringify(summary, null, 2),
  };
}
