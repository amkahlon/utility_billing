#!/usr/bin/env bash
# Full regression: migrate all sites, unit + integration tests on the test site (with ERPNext) and the ERPNext-free
# QA site, then the HTTP end-to-end suite (once, on one society) against a freshly restarted QA server on port 8001.
# Needs `bench start` running (Redis and the background worker).
# Usage (inside WSL):  bash ~/frappe-bench/apps/utility_billing/e2e/regression.sh
set -uo pipefail
cd "$HOME/frappe-bench"
export PATH="$HOME/.local/bin:$PATH"
status=0
PIDFILE="$HOME/qa-serve.pid"

for site in ub.localhost test.localhost qa.localhost; do
  echo "== migrate $site"
  bench --site "$site" migrate > "$HOME/migrate-$site.log" 2>&1 || { echo "migrate failed, see ~/migrate-$site.log"; status=1; }
done

# Never on ub.localhost (the dev site with the user's demo data): allow_tests is off there.
for site in test.localhost qa.localhost; do
  echo "== unit + integration on $site"
  out=$(bench --site "$site" run-tests --app utility_billing 2>&1 | sed "s/\x1b\[[0-9;]*m//g")
  echo "$out" | grep -E "✖|FAIL|ERROR|^Ran |^OK"
  echo "$out" | grep -qE "FAILED|ERROR" && status=1
done

echo "== restarting QA server on :8001"
if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null; sleep 2; fi
nohup bench --site qa.localhost serve --port 8001 --noreload > "$HOME/qa-serve.log" 2>&1 &
echo $! > "$PIDFILE"
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:8001/api/method/ping && break; sleep 1; done

# One run on one society is enough for a regression (each run adds a society with a full set of test data); set E2E_RUNS=2
# to check that the suite can be repeated on the same site.
RUNS="${E2E_RUNS:-1}"
echo "== end-to-end on qa.localhost:8001 ($RUNS run)"
for run in $(seq 1 "$RUNS"); do
  E2E_URL=http://127.0.0.1:8001 env/bin/python apps/utility_billing/e2e/run_e2e.py > "$HOME/e2e-run$run.log" 2>&1 || status=1
  grep -E "^FAIL" "$HOME/e2e-run$run.log" | cut -c1-300
  tail -1 "$HOME/e2e-run$run.log"
done

echo "== stopping QA server"
kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"

echo "== regression $([ $status -eq 0 ] && echo PASSED || echo FAILED)"
exit $status
