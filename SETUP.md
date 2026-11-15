# Setup for a new teammate (written for their coding agent)

You're setting up a teammate's Windows machine to work on Collectivize. Do each step, skip any
that's already done, and only stop for the steps marked **(human)**.

1. **Tools.** Install whatever is missing (check with `git --version`, `gh --version`, `uv --version`):

   ```
   winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
   winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements
   winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
   ```

   Freshly installed tools may not be on PATH in an already-open shell: open a new terminal, or call
   them by full path (`C:\Program Files\GitHub CLI\gh.exe`, `C:\Program Files\Git\cmd\git.exe`).
   If PowerShell says "running scripts is disabled", call the `.cmd`/`.exe` version of the command.
   Don't change the execution policy; that's the human's call.

2. **GitHub sign-in (human approves).** Run this in a terminal the human can see:

   ```
   gh auth login --web --git-protocol https --hostname github.com
   ```

   Tell the human to press Enter, copy the code, and approve it in the browser. Then
   `gh auth setup-git`, so `git push` uses the same login.

3. **Accept the invite to the repo** (Cody sent it; you can accept it for them):

   ```
   gh api user/repository_invitations --jq '.[] | select(.repository.full_name=="CodyGrodsky/Collectivize") | .id'
   gh api -X PATCH user/repository_invitations/<id>
   ```

   If there's no invite, they may already be a collaborator (check that `gh repo view CodyGrodsky/Collectivize --json viewerPermission`
   says WRITE or ADMIN). If it says READ, ask the human to ask Cody for an invite.

4. **Clone it** somewhere sensible (e.g. their home folder) and install the Python dependencies:

   ```
   git clone https://github.com/CodyGrodsky/Collectivize.git collectivize-vercel
   cd collectivize-vercel
   uv sync
   ```

5. **Check it runs**, then stop the server:

   ```
   uv run python scripts/seed_test_db.py --size small
   uv run python scripts/serve.py --no-login --no-ai
   ```

   (http://127.0.0.1:3000 should show the app signed in as Drew. `data/` is git-ignored.)

6. **Tell the human it's done**, and that from now on they should open the `collectivize-vercel` folder
   in Claude Code (or their editor's agent) and just say what to change. Then read `CLAUDE.md` in the
   repo and follow it: it has the push rules. Pushes to `main` go live at
   https://collectivize-tau.vercel.app in about 20 seconds.
