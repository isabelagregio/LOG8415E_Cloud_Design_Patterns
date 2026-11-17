# SSH access refresh patch (Marco 1)

This patch updates the current Gatekeeper public IP and the lab terminal's
changing public egress IP. It also allows a deploy to resume using existing
`.state/aws.json` instead of creating duplicate infrastructure.

Files belong in `scripts/` of the repository. Never replace `.state/aws.json`.

The lab's older SSH client does not recognize `StrictHostKeyChecking=accept-new`.
This patch uses `StrictHostKeyChecking=no` for compatibility on the isolated
teaching environment. This does NOT provide strong SSH server identity checking;
for production, pin and verify host keys instead.

With valid temporary AWS credentials and an active virtual environment:

    python3 scripts/refresh_access.py
    ./scripts/deploy.sh

The initial security-group rules remain limited to a single current client IP
(`/32`), and both port 22 and 8000 follow that IP across lab sessions.
No EC2 instance is created by refresh_access.py.

If the lab credentials have expired, obtain fresh temporary credentials from
the learner portal before running the patch. This script does not and should
not fetch AWS Academy session credentials automatically.
