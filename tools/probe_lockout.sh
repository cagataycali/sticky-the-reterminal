#!/bin/bash
# §11 pocket-lockout acceptance probe — runs over the relay.
# Prereq: fw with tiny_lock + lock/unlock test verbs. Usage: ./probe_lockout.sh
RPC=${RPC:-/tmp/sticky_rpc.sh}
fail=0; step(){ echo "== $1"; }
val(){ echo "$2" | tr -d '\\\\' | grep -q "$3" && echo "   PASS" || { echo "   FAIL: $2"; fail=1; }; }

step "L0 baseline: unlocked"
R=$($RPC "status" 8); val L0 "$R" '"locked":false'
step "L1 lock verb"
R=$($RPC "lock" 8); echo "   $R"
R=$($RPC "status" 8); val L1 "$R" '"locked":true'
step "L2 relay tap while locked = owner remote reach, ALLOWED by design ruling"
R=$($RPC "tap 304 427" 8); val L2 "$R" '"routed"'
echo "   (physical GT911 discard is L8's hand test — synthetic taps enter past the router)"
$RPC "swipe 400 470 400 200" 8 >/dev/null # if tap opened kb, band-cancel home
step "L3 voice verb refuses honestly"
R=$($RPC "voice" 8); val L3 "$R" 'locked\|refus'
step "L4 miccheck refuses (D-UX5 — every mic path gates, not just voice)"
R=$($RPC "miccheck" 8); val L4 "$R" 'locked\|refus'
step "L5 remote reach STILL works: say renders while locked"
R=$($RPC "say lockout probe L5" 8); val L5 "$R" 'said\|rendered\|card_id'
step "L6 screenshot answers while locked (owner's eye, not device input)"
R=$($RPC "screenshot" 10); val L6 "$R" 'https://'
step "L7 unlock verb -> touch live again"
R=$($RPC "unlock" 8); R=$($RPC "status" 8); val L7 "$R" '"locked":false'
$RPC "page home" 8 >/dev/null # deterministic card under the tap
R=$($RPC "tap 304 427" 8); val L7b "$R" '"routed":true'
$RPC "swipe 400 470 400 200" 8 >/dev/null # home
step "L8 (MANUAL, hand required): AI-button hold while locked must NOT record —"
echo "   no upload event in log; short-press wakes glance card w/ lock glyph."
echo; [ $fail -eq 0 ] && echo "LOCKOUT PROBE: ALL RELAY-TESTABLE GATES PASS" || echo "LOCKOUT PROBE: FAILURES ABOVE"
exit $fail
