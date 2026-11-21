# Secrets & privacy in images (scrub rules)

A stock card image holds **its owner's account token**. Keep your own backup of the card if you like, but **no
personal secret may carry into any build or pack that is shared**, and a custom OS needs none of it.

## Personal data in a stock image (scrub it from anything shared)
| What | Where | Needed in a custom build? |
|---|---|---|
| Launcher **UserToken** (account/online) | `~/.local/share/launcher/launcher.ini` | **No**: it only works with the stock launcher |
| Account name ("real name") | same `launcher.ini` | No |
| Telemetry **upload_token** | `Arcadia20XX/savedata/diagnostics.cfg` | No: remove it |
| An SSH key or password you added | your own customised image only | No: set fresh ones per build |

## Why a custom build needs none of it
- A custom OS does not run the stock launcher, so the token has no function there.
- Your own untouched backup keeps the original token and account, so going back to stock loses nothing.
- No token is stored in this repository in any form, plaintext or hash: a hash of one owner's token still points
  at that owner's account (#246).

## How a scrub step finds the token
A scrub step reads `launcher.ini` on your own copy of the image, removes the `UserToken` line (and the account
name and the telemetry `upload_token`), and checks the line is gone. It needs no stored value to compare against,
and it never prints the token.

## Rule
Any shared image or pack is built **personal-data-free**: strip the UserToken, the account name and the telemetry
token, and never include an SSH key or password. (Tracked in issue #10.)
