#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIVE_DIR="$PROJECT_DIR/live"
APP_TARGET="$LIVE_DIR/config/includes.chroot/opt/mirror-drive"
FONT_TARGET="$LIVE_DIR/config/includes.chroot/usr/share/fonts/truetype/plainloop"

if ! command -v lb >/dev/null 2>&1; then
    echo "live-build is required. On Debian/Ubuntu: sudo apt install live-build" >&2
    exit 1
fi

mkdir -p "$APP_TARGET"
rm -rf "$APP_TARGET/src"
cp -R "$PROJECT_DIR/src" "$APP_TARGET/src"
rm -rf "$APP_TARGET/assets"
cp -R "$PROJECT_DIR/assets" "$APP_TARGET/assets"
mkdir -p "$FONT_TARGET"
cp "$PROJECT_DIR/assets/DMSans.ttf" "$FONT_TARGET/DMSans.ttf"
chmod +x "$LIVE_DIR/config/includes.chroot/usr/local/bin/mirror-drive"
chmod +x "$LIVE_DIR/config/includes.chroot/usr/local/bin/mirror-drive-gui"
chmod +x "$LIVE_DIR/config/includes.chroot/usr/local/bin/mirror-drive-session"
chmod +x "$LIVE_DIR/config/hooks/normal/010-enable-mirror-drive.hook.chroot"
chmod 0644 "$LIVE_DIR/config/includes.chroot/etc/systemd/system/mirror-drive.service"

cd "$LIVE_DIR"
lb clean --purge 2>/dev/null || true
lb config \
    --mode debian \
    --architectures amd64 \
    --archive-areas "main contrib non-free-firmware" \
    --parent-archive-areas "main contrib non-free-firmware" \
    --binary-images iso-hybrid \
    --bootappend-live "boot=live components hostname=mirror-drive username=mirror" \
    --debian-installer none \
    --distribution trixie \
    --parent-mirror-bootstrap "http://deb.debian.org/debian" \
    --parent-mirror-chroot "http://deb.debian.org/debian" \
    --parent-mirror-chroot-security "http://security.debian.org/debian-security" \
    --parent-mirror-binary "http://deb.debian.org/debian" \
    --parent-mirror-binary-security "http://security.debian.org/debian-security" \
    --mirror-bootstrap "http://deb.debian.org/debian" \
    --mirror-chroot "http://deb.debian.org/debian" \
    --mirror-chroot-security "http://security.debian.org/debian-security" \
    --mirror-binary "http://deb.debian.org/debian" \
    --mirror-binary-security "http://security.debian.org/debian-security" \
    --iso-application "Plainloop Mirror Drive" \
    --iso-publisher "Plainloop" \
    --iso-volume "MIRROR_DRIVE" \
    --linux-flavours amd64 \
    --linux-packages linux-image
lb build

cp live-image-amd64.hybrid.iso "$PROJECT_DIR/Mirror-Drive-amd64.iso"
echo "Built: $PROJECT_DIR/Mirror-Drive-amd64.iso"
