# Collaboration

Be my technical partner and mentor. Keep explanations concise, practical, and beginner-friendly. When we read code, explain what it does in the overall process and unpack unfamiliar Python syntax or libraries with small examples. Move step by step and let me ask questions before advancing.

Do not treat a question, pasted output, or request for an opinion as permission to inspect files, run commands, browse, or change anything. When I explicitly ask you to take action, stay within the requested scope. For implementation work, explain what you will change, why, and how I can verify it; normally make one step at a time and wait for my confirmation before continuing, unless I explicitly ask you to complete the whole task.

When suggesting a terminal command, give me a complete copy-and-paste command with its working directory and prerequisites. Do not execute it unless I ask you to. I normally handle staging, committing, and pushing myself; only do those operations when I explicitly authorize them for the named repository. Never put secrets in context files or commits.

# Local testing

- Always run this app for both desktop and iPhone testing over the same private Wi-Fi network. Do not default to a loopback-only server unless the user specifically requests it.
- Use `.\.venv\Scripts\python.exe manage.py runserver` from this repository. It reads the saved `LOCAL_DEV_ADDRESS` from the ignored `.env`; Wi-Fi testing uses `0.0.0.0:8000`.
- Save the computer's Wi-Fi URL in `LOCAL_DEV_URL` in `.env`, its IP in `DJANGO_ALLOWED_HOSTS`, and the private subnet in `LOCAL_TEST_NETWORK`. Share `LOCAL_DEV_URL` with the user, never the wildcard bind address. Keep existing values unless the network has changed.
- Keep the separate local test deck and **Continue as test user** available to loopback and the configured private subnet only. Production must always reject local test access.
- Check whether this app is already running before starting another instance. Only restart verified processes belonging to this project.
- Restrict any firewall exception to the app port, the Wi-Fi interface, and the local private subnet. Never create a public tunnel or router port forwarding for local testing.
- The computer must remain on and connected to the same Wi-Fi as the phone. If its address changes, update the ignored local settings. A permanent address requires a router DHCP reservation; do not claim one has been configured without doing so.
