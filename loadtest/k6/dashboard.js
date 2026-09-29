// Dashboard read load: simulates operators/judges polling the UI.
// Ramps 0 -> 50 -> 100 -> 200 virtual users, each hitting the three endpoints
// the web UI polls most often. Run with:
//   docker run --rm --network host -v "$PWD/loadtest:/loadtest" grafana/k6:0.54.0 run /loadtest/k6/dashboard.js
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';

export const options = {
  scenarios: {
    dashboard_polling: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 50 },
        { duration: '60s', target: 100 },
        { duration: '60s', target: 200 },
        { duration: '20s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<300'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export default function () {
  const endpoints = ['/api/state', '/api/status', '/api/recommendations'];
  for (const path of endpoints) {
    const res = http.get(`${BASE_URL}${path}`, { tags: { endpoint: path } });
    check(res, { 'status is 200': (r) => r.status === 200 });
  }
  sleep(1);
}

// ---- summary helpers (no external deps, so `k6 inspect`/`run` work offline) ----

function metricValues(data, name) {
  return (data.metrics[name] && data.metrics[name].values) || {};
}

function summarize(data) {
  const dur = metricValues(data, 'http_req_duration');
  const reqs = metricValues(data, 'http_reqs');
  const failed = metricValues(data, 'http_req_failed');
  return {
    requests: reqs.count || 0,
    rps: reqs.rate || 0,
    error_rate: failed.rate || 0,
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
    stdout: textReport('Jalani dashboard load test', summary),
    '/loadtest/results/dashboard-summary.json': JSON.stringify(summary, null, 2),
  };
}
