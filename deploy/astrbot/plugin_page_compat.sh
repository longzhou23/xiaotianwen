#!/bin/sh
set -eu

# AstrBot 4.27.4 serves plugin pages in a sandboxed iframe while its page
# response still declares X-Frame-Options: SAMEORIGIN. Chromium treats an
# iframe without allow-same-origin as an opaque origin and renders the page
# blank. Patch the generated Dashboard bundle at startup until the upstream
# image contains the compatible sandbox flag. The replacement is idempotent
# and is skipped automatically when the bundle has already been fixed.
for bundle in /AstrBot/astrbot/dashboard/dist/assets/PluginPagePage-*.js; do
  [ -f "$bundle" ] || continue
  sed -i \
    -e 's/sandbox:"allow-scripts allow-forms allow-downloads"/sandbox:"allow-scripts allow-same-origin allow-forms allow-downloads allow-top-navigation-by-user-activation"/g' \
    -e 's/sandbox:"allow-scripts allow-same-origin allow-forms allow-downloads"/sandbox:"allow-scripts allow-same-origin allow-forms allow-downloads allow-top-navigation-by-user-activation"/g' \
    "$bundle"
done

exec "$@"
