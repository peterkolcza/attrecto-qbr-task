#!/usr/bin/env bash
# Smoke test for deployed QBR application
# Usage: bash deploy/smoke-test.sh https://qbr.yourdomain.com

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
PASS=0
FAIL=0

check() {
    local desc="$1"
    local cmd="$2"
    if eval "$cmd" > /dev/null 2>&1; then
        echo "✓ $desc"
        PASS=$((PASS + 1))
    else
        echo "✗ $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "QBR Smoke Test — $BASE_URL"
echo "────────────────────────────"

# 1. Health check
check "Healthcheck responds" \
    "curl -sf '$BASE_URL/healthz' | grep -q 'ok'"

# 2. Landing page
check "Landing page loads" \
    "curl -sf '$BASE_URL/' | grep -q 'QBR'"

# 3. Start demo analysis
JOB_ID=$(curl -sf -X POST "$BASE_URL/analyze" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null || echo "")
if [ -n "$JOB_ID" ]; then
    echo "✓ Demo analysis started (job: $JOB_ID)"
    PASS=$((PASS + 1))

    # 4. Job detail page
    check "Job detail page loads" \
        "curl -sf '$BASE_URL/jobs/$JOB_ID' | grep -q '$JOB_ID'"

    # 5. Wait for completion (max 120s)
    echo "  Waiting for analysis to complete..."
    for i in $(seq 1 24); do
        STATE=$(curl -sf "$BASE_URL/jobs/$JOB_ID" 2>/dev/null | grep -oP '(?<=class="text-xs px-3 py-1 rounded\s+)[^"]+' | head -1 || echo "")
        if echo "$STATE" | grep -q "complete"; then
            check "Analysis completed" "true"
            check "Report page loads" \
                "curl -sf '$BASE_URL/jobs/$JOB_ID/report' | grep -q 'Portfolio'"
            break
        elif echo "$STATE" | grep -q "error"; then
            echo "✗ Analysis failed"
            FAIL=$((FAIL + 1))
            break
        fi
        sleep 5
    done
else
    echo "✗ Failed to start demo analysis"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "────────────────────────────"
echo "Results: $PASS passed, $FAIL failed"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
