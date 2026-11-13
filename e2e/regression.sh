#!/usr/bin/env bash
# Full regression: unit + integration tests on the dev site and the ERPNext-free QA site,
# then the HTTP end-to-end suite against the QA site on port 8001.
# Needs `bench start` running (Redis). Starts the QA web server on :8001 if it isn't running.
# Usage (inside WSL):  bash ~/frappe-bench/apps/utility_billing/e2e/regression.sh
set -uo pipefail
cd "$HOME/frappe-bench"
export PATH="$HOME/.local/bin:$PATH"
status=0

for site in ub.localhost qa.localhost; do
  echo "== unit + integration on $site"
  out=$(bench --site "$site" run-tests --app utility_billing 2>&1 | sed "s/\x1b\[[0-9;]*m//g")
  echo "$out" | grep -E "✖|FAIL|ERROR|^Ran |^OK"
  echo "$out" | grep -qE "FAILED|ERROR" && status=1
done

if ! curl -s -o /dev/null http://127.0.0.1:8001/api/method/ping; then
  echo "== starting QA server on :8001"
  nohup bench --site qa.localhost serve --port 8001 --noreload > "$HOME/qa-serve.log" 2>&1 &
  for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:8001/api/method/ping && break; sleep 1; done
fi

echo "== end-to-end on qa.localhost:8001"
E2E_URL=http://127.0.0.1:8001 env/bin/python apps/utility_billing/e2e/run_e2e.py | tail -1 || status=1
E2E_URL=http://127.0.0.1:8001 env/bin/python apps/utility_billing/e2e/run_e2e.py >/dev/null || status=1

echo "== regression $([ $status -eq 0 ] && echo PASSED || echo FAILED)"
exit $status
