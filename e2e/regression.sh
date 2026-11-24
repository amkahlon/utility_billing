#!/usr/bin/env bash
# Regression gate for each increment (user's decision 2026-11-15): migrate all sites, check our DocTypes don't clash with
# ERPNext's on the test site, run the unit + integration tests once on the ERPNext-free QA site, then the HTTP end-to-end
# suite (once, on one society) against a freshly restarted QA server on port 8001.
# Before a release or a client deployment, run the tests on both sites:  TEST_SITES="test.localhost qa.localhost" bash regression.sh
# Needs `bench start` running (Redis and the background worker).
# Usage (inside WSL):  bash ~/frappe-bench/apps/utility_billing/e2e/regression.sh
set -uo pipefail
cd "$HOME/frappe-bench"
export PATH="$HOME/.local/bin:$PATH"
status=0
PIDFILE="$HOME/qa-serve.pid"

# The background worker makes notifications and runs loads; without it (WSL stops `bench start` when the laptop sleeps) cases
# fail for no fault of the code (QA-24 on 2026-11-21). Checked now and again before the end-to-end suite.
workers_up() { bench --site qa.localhost doctor 2>/dev/null | grep -qE "Workers online: [1-9]"; }
workers_up || { echo "== regression NOT RUN: no background worker; start the bench (bench start) and run again"; exit 2; }

for site in ub.localhost test.localhost qa.localhost; do
  echo "== migrate $site"
  bench --site "$site" migrate > "$HOME/migrate-$site.log" 2>&1 || { echo "migrate failed, see ~/migrate-$site.log"; status=1; }
done

echo "== DocType clash check on test.localhost (with ERPNext)"
(cd sites && ../env/bin/python ../apps/utility_billing/e2e/check_clash.py test.localhost 2>/dev/null) || status=1

# Never on ub.localhost (the dev site with the user's demo data): allow_tests is off there.
for site in ${TEST_SITES:-qa.localhost}; do
  echo "== unit + integration on $site"
  out=$(bench --site "$site" run-tests --app utility_billing 2>&1 | sed "s/\x1b\[[0-9;]*m//g")
  grep -E "✖|FAIL|ERROR|^Ran |^OK" <<< "$out"
  # a here-string, not `echo | grep -q`: grep -q stops at the first match, echo then dies of a broken pipe and, with pipefail,
  # the pipeline counts as false, so a failed run was reported as PASSED (found 2026-11-17). Every "Ran" must be followed by OK.
  if grep -qE "FAILED|ERROR" <<< "$out" || [ "$(grep -c '^Ran ' <<< "$out")" -ne "$(grep -c '^OK' <<< "$out")" ]; then
    status=1
  fi
done

workers_up || { echo "== no background worker now (did the laptop sleep?): the end-to-end results below can't be trusted"; status=1; }
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

# Browser tests of the main screens (real Chromium, Playwright in ~/e2e-venv: server/wsl-10-browser-tests.sh installs it).
if [ -x "$HOME/e2e-venv/bin/python" ]; then
  echo "== browser tests on qa.localhost:8001"
  "$HOME/e2e-venv/bin/python" apps/utility_billing/e2e/run_browser.py > "$HOME/browser-run.log" 2>&1 || status=1
  grep -E "^FAIL" "$HOME/browser-run.log" | cut -c1-300
  tail -1 "$HOME/browser-run.log"
else
  echo "== browser tests SKIPPED: ~/e2e-venv is missing (run server/wsl-10-browser-tests.sh)"; status=1
fi

echo "== stopping QA server"
kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"

echo "== regression $([ $status -eq 0 ] && echo PASSED || echo FAILED)"
exit $status
