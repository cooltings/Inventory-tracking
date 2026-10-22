#!/usr/bin/env bash
# Install the x86_64 APK in the CI emulator, launch it and capture what happens.
set -u
APK="$1"
OUT="$2"
mkdir -p "$OUT"
adb install -r "$APK" | tee "$OUT/install.txt"
PKG=$(adb shell pm list packages | tr -d '\r' | sed 's/^package://' | grep -i zafinventory | head -1)
echo "package: $PKG" | tee "$OUT/package.txt"
adb logcat -c
adb shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1
# Qt + Python start-up on an emulator is slow; give it time, grabbing screens on the way
for t in 20 45 75; do
  sleep 25
  adb exec-out screencap -p > "$OUT/screen-${t}s.png"
done
adb shell pidof "$PKG" > "$OUT/pid.txt" || echo "NOT RUNNING" > "$OUT/pid.txt"
adb logcat -d > "$OUT/logcat.txt"
adb shell run-as "$PKG" sh -c 'find . -name error.log -exec cat {} \;' > "$OUT/error-log.txt" 2>&1 || true
adb shell run-as "$PKG" sh -c 'find . -name inventory.db' > "$OUT/db-files.txt" 2>&1 || true
grep -iE "python|qt|zaf|traceback|error|fatal" "$OUT/logcat.txt" | tail -200 > "$OUT/logcat-filtered.txt" || true
cat "$OUT/pid.txt"
