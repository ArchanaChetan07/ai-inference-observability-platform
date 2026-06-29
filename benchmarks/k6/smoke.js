import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "30s",
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(99)<5000"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8082";

export default function () {
  const payload = JSON.stringify({
    model: "facebook/opt-1.3b",
    messages: [{ role: "user", content: "Say hello in one word." }],
    max_tokens: 8,
    stream: false,
  });

  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers: { "Content-Type": "application/json" },
    timeout: "120s",
  });

  check(res, {
    "status is 200": (r) => r.status === 200,
    "has ttft header or usage": (r) =>
      r.headers["X-Vllm-Ttft-Ms"] !== undefined ||
      r.headers["x-vllm-ttft-ms"] !== undefined ||
      (r.json("usage.ttft_ms") !== undefined),
  });

  sleep(1);
}
