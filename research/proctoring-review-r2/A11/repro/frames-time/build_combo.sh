#!/bin/sh
# ISOLATED REPRO tree (NOT an integration run): A01r2 29cadde backend+contracts, overlaid with the module packages
# A02 7769f13 capture, A03 c0275a2 phone, A04 4014444 attention, A05 8f763a1 fusion, A08 5509950 evidence.
# Plus a copy of the A07 3fef6fb desktop dir for the renderer geometry check (r3).
set -e
S=/tmp/claude-0/-home-user-Qostanay-hub/45deca49-8d87-5e54-89f8-dc4697612908/scratchpad
W=$S/a11/find-frames-time
rm -rf "$W/combo" && mkdir -p "$W/combo"
cp -r "$S/wt/A01r2/proctoring/backend" "$W/combo/backend"
cp -r "$S/wt/A01r2/proctoring/contracts" "$W/combo/contracts"
for m in A02:capture A03:phone A04:attention A05:fusion A08:evidence; do
  r=${m%%:*}; p=${m##*:}
  rm -rf "$W/combo/backend/proctor/$p"
  cp -r "$S/wt/$r/proctoring/backend/proctor/$p" "$W/combo/backend/proctor/"
done
find "$W/combo" -name __pycache__ -prune -exec rm -rf {} \;
rm -rf "$W/ui" && mkdir -p "$W/ui/proctoring"
cp -r "$S/wt/A07/proctoring/desktop" "$W/ui/proctoring/desktop"
cp -r "$S/wt/A07/proctoring/contracts" "$W/ui/proctoring/contracts"
ln -s "$S/node/node_modules" "$W/ui/proctoring/desktop/node_modules"
echo "combo + ui ready under $W"
