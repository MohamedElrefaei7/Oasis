#!/usr/bin/env bash
#
# sign.sh — ad-hoc sign Oasis.app, inside out.
#
# Ad-hoc (`-s -`) because Oasis is distributed free and outside the App Store
# with no Apple Developer account: there is no Developer ID to sign with and
# nothing to notarize. That is a deliberate choice with a known cost — Gatekeeper
# will refuse the first launch and the user has to clear it by hand once
# (README § Install). What ad-hoc signing *does* buy is the thing arm64 requires
# anyway: every Mach-O in the bundle carries a valid signature, the bundle is
# internally consistent, and any later tampering is detectable.
#
# NO hardened runtime (`--options runtime`). It exists to satisfy notarization,
# which is not happening, and it would fight the frozen stack: PyInstaller's
# bootloader and torch both do things (JIT-adjacent mappings, dlopen of
# unsigned-by-us dylibs) that the hardened runtime restricts without
# entitlements written to excuse them. Adding it would be cargo cult with a
# real chance of breaking launch.
#
# ORDER IS THE WHOLE POINT. A bundle signature seals its contents by hash, so
# every nested Mach-O must be signed BEFORE the enclosing bundle; sign the .app
# first and the seal is invalid the moment anything inside it changes. This
# walks depth-first — deepest paths first — and signs the .app last.
#
# `--deep` would do this in one flag and is the wrong tool: it is deprecated,
# and on a bundle this size (445 Mach-O files) its behaviour on nested bundles
# and symlinks is exactly the kind of thing you do not want to be guessing at.
# An explicit walk is longer and says what it does.
#
# Usage:  bash packaging/sign.sh /path/to/Oasis.app
set -euo pipefail

APP="${1:?usage: sign.sh /path/to/Oasis.app}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTITLEMENTS="${HERE}/entitlements.plist"

[ -d "${APP}" ] || { echo "error: no bundle at ${APP}" >&2; exit 1; }
[ -f "${ENTITLEMENTS}" ] || { echo "error: no entitlements at ${ENTITLEMENTS}" >&2; exit 1; }

echo "==> collecting Mach-O files under ${APP}"

# Regular files only: symlinks are followed by codesign to their target, so
# signing both the link and the target signs the same bytes twice — and a
# link pointing at an as-yet-unsigned target would seal a stale hash. The
# deduped libtorch_cpu.dylib is exactly this case.
tmp="$(mktemp -t oasis-signlist)"
trap 'rm -f "$tmp" "$tmp.sorted"' EXIT

find "${APP}" -type f -perm +111 -print0 2>/dev/null \
  | xargs -0 file --mime-type \
  | awk -F': ' '$2 ~ /application\/x-mach-binary/ {print $1}' > "$tmp"

# Non-executable dylibs/.so do exist in Python trees; catch those too.
find "${APP}" -type f \( -name '*.dylib' -o -name '*.so' \) -print >> "$tmp"

# The main executable is signed as part of the .app bundle at the end, with
# entitlements. Signing it standalone here would just be overwritten.
grep -v "^${APP}/Contents/MacOS/" "$tmp" | sort -u > "$tmp.sorted"

count=$(wc -l < "$tmp.sorted" | tr -d ' ')
echo "==> signing ${count} nested Mach-O files (deepest first)"

# Deepest-first: sort by path-separator count, descending. Nothing in this
# bundle nests a Mach-O inside another Mach-O's bundle, but the ordering is
# what makes the walk correct rather than incidentally fine.
awk '{print gsub(/\//,"/") "\t" $0}' "$tmp.sorted" \
  | sort -rn -k1,1 \
  | cut -f2- \
  | while IFS= read -r f; do
      codesign --force --sign - --timestamp=none "$f" 2>&1 \
        | grep -v ': replacing existing signature' || true
    done

# Nested bundles next. KeyboardShortcuts_KeyboardShortcuts.bundle is a pure
# resource bundle (Info.plist + .lproj, no Mach-O), which Xcode left unsigned;
# codesign treats anything with an Info.plist as a nested code object, so sign
# it as a bundle rather than leaving --strict to decide how it feels about it.
while IFS= read -r b; do
    echo "==> signing nested bundle ${b#"${APP}"/}"
    codesign --force --sign - --timestamp=none "$b"
done < <(find "${APP}/Contents" -name '*.bundle' -o -name '*.framework' | sort -r)

echo "==> signing the app bundle (last)"
codesign --force --sign - --timestamp=none \
    --entitlements "${ENTITLEMENTS}" \
    --generate-entitlement-der \
    "${APP}"

echo "==> done"
