#!/bin/bash
# Runs on EVERY boot. If the user filled in the boot-partition wifi.conf with
# their own network, import it into NetworkManager and then scrub the plaintext
# password off the FAT partition (it lives in the OS afterwards, not in a file
# anyone can read). Ethernet needs none of this. The image ships wifi.conf EMPTY
# (no secrets baked in) -- each user types their own creds post-flash.
set -u
CONF=/boot/firmware/wifi.conf
[ -f "$CONF" ] || CONF=/boot/wifi.conf          # fallback if boot isn't at /firmware
[ -f "$CONF" ] || exit 0

# Parse SSID= / PSK=, tolerate CRLF (files edited on Windows) and surrounding space.
SSID=$(sed -n 's/\r$//; s/^[[:space:]]*SSID[[:space:]]*=[[:space:]]*//p' "$CONF" | head -n1)
PSK=$(sed -n 's/\r$//; s/^[[:space:]]*PSK[[:space:]]*=[[:space:]]*//p' "$CONF" | head -n1)

# Nothing to do if the user left SSID blank (the shipped default).
[ -n "${SSID:-}" ] || exit 0

echo "coin-dojo-wifi: importing network '$SSID' from $CONF"
nmcli connection delete "coin-dojo-wifi" >/dev/null 2>&1 || true
if [ -n "${PSK:-}" ]; then
  nmcli connection add type wifi con-name "coin-dojo-wifi" ifname wlan0 \
    ssid "$SSID" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" \
    connection.autoconnect yes >/dev/null
else
  # open network (no password)
  nmcli connection add type wifi con-name "coin-dojo-wifi" ifname wlan0 \
    ssid "$SSID" connection.autoconnect yes >/dev/null
fi
ADDED=$?

if [ "$ADDED" -eq 0 ]; then
  nmcli connection up "coin-dojo-wifi" >/dev/null 2>&1 || true   # autoconnect catches it when in range
  # Scrub: rewrite the template with empty creds so the password does NOT linger on FAT.
  cat > "$CONF" <<'TPL'
# Coin Dojo WiFi setup (OPTIONAL -- Ethernet needs no setup).
# Your network was imported on last boot and these fields were cleared;
# the password now lives in the OS, not in this file.
# To change WiFi: fill both lines, save, and reboot.
SSID=
PSK=
TPL
  sync
  echo "coin-dojo-wifi: done (creds moved into NetworkManager, wifi.conf cleared)"
else
  echo "coin-dojo-wifi: nmcli add failed; leaving wifi.conf in place to retry" >&2
fi
