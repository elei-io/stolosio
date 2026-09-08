#!/usr/bin/python3
"""Launch the pinned browser as blessuser; Browserless keeps a separate identity."""

import os
import pwd
import sys
from pathlib import Path

user = pwd.getpwnam("blessuser")
if os.getuid() != 0:
    raise SystemExit("The browser must be launched by the isolated controller")

# Browserless/Puppeteer creates a private profile before invoking the executable.
# Transfer only that disposable profile, never an arbitrary path supplied by a client.
for argument in sys.argv[1:]:
    if argument.startswith("--user-data-dir="):
        profile = Path(argument.split("=", 1)[1]).resolve()
        if not (
            str(profile).startswith("/tmp/browserless-data-dirs/")
            or str(profile).startswith("/tmp/puppeteer_dev_chrome_profile-")
        ):
            raise SystemExit("Browser profile must be in the disposable runtime directory")
        profile.mkdir(parents=True, exist_ok=True)
        os.chown(profile, user.pw_uid, user.pw_gid)

os.environ["HOME"] = user.pw_dir
os.execv(
    "/usr/bin/setpriv",
    [
        "setpriv",
        f"--reuid={user.pw_uid}",
        f"--regid={user.pw_gid}",
        "--clear-groups",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--no-new-privs",
        str(Path(sys.argv[0]).absolute()) + ".real",
        *sys.argv[1:],
    ],
)
