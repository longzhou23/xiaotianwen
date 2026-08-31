# Synthetic fixture assets

Only public, synthetic, non-executable test assets belong here.  P0 replay
cases currently use virtual `/synthetic/…` media references and do not need to
open real images.  A later fixture that needs bytes must use a relative file
from this directory, declare its SHA-256 and MIME type, and remain below the
per-case size limit.

Never put QQ media, screenshots with real conversations, downloaded images,
credentials, or any private recovery asset here.
