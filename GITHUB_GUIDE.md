# Simplified GitHub Guide

A simple, step-by-step guide. Read it once, then use the [cheat sheet](#cheat-sheet) at the bottom. This is the short version of [`CONTRIBUTING.md`](CONTRIBUTING.md), which has the full team rules (including a more advanced way of working with worktrees).

## The big picture

Every change follows the same path: **issue → your branch → pull request → review → merge.** You never edit `main` directly.

For the first deliverable, the whole team shares one issue and one integration branch. Each subteam has its own branch off it.

```
main                                  ← protected. You never push here.
 └── issue-N/integration              ← shared by the whole team. Pull requests go HERE.
      ├── issue-N/ports-traffic       ← subteam A
      ├── issue-N/trade-flows         ← subteam B
      ├── issue-N/prices-oil-flows    ← subteam C
      └── issue-N/routes-ships        ← subteam D
```

- `N` is the issue number, for example `issue-12`.
- A **pull request** (also called a merge request) is how you ask for your branch to be merged and reviewed.
- Branch names have no extra slashes. Use `issue-12/ports-traffic`, not `issue-12/ports/traffic`.

## One-time setup

1. **Install Git** and check it works:
   ```sh
   git --version
   ```
2. **Tell Git who you are:**
   ```sh
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```
3. **Log in to GitHub.** Make sure you have been added to the organization. The easiest login is the GitHub CLI:
   ```sh
   gh auth login
   ```
   If you do not want the CLI, use a personal access token when Git asks for a password.
4. **Clone the repository** (download it) and go into the folder:
   ```sh
   git clone https://github.com/TAMU-Aggie-Data-Science-Club/fall2026-bluevector.git
   cd fall2026-bluevector
   ```

## Keep API keys and tokens private

Some of our sources (Comtrade, EIA, Global Fishing Watch) need a key or token.

- **Never paste a key into any file in the repo**, including `DATA.md`, code, and notebooks. In `DATA.md`, write `free key required`, not the key.
- Keep keys in a `.env` file (it is git-ignored) or in an environment variable.
- The repo scans every push for secrets (a check called Gitleaks). If a key is pushed, tell the team right away, then revoke the key and create a new one. Deleting the file is not enough, because git remembers it.

## Every time you work: 10 steps

### Step 1. Find the issue

Open the project board and find the issue for the task. Click **Assignees** and assign it to yourself. Note the issue number (`N`). If you cannot find it, ask in the group chat.

### Step 2. Update `main`

Always start from the latest version:

```sh
git switch main
git pull origin main
```

### Step 3. Get the integration branch

This is the shared branch for the issue. It is created once for the whole team.

```sh
git fetch origin
git switch issue-N/integration
```

If you get an error saying it does not exist, **ask in the group chat.** Do not create it yourself, because two people creating it causes conflicts.

### Step 4. Get your subteam's branch

Your subteam shares one branch. **Only one teammate creates it.** Agree who, then:

```sh
git switch -c issue-N/your-subteam
git push -u origin issue-N/your-subteam
```

Your teammate then gets it with:

```sh
git fetch origin
git switch issue-N/your-subteam
```

Use the branch name from this table:

| Subteam | Branch |
|---|---|
| A · Ports & traffic | `issue-N/ports-traffic` |
| B · Trade flows | `issue-N/trade-flows` |
| C · Prices & oil flows | `issue-N/prices-oil-flows` |
| D · Routes & ships | `issue-N/routes-ships` |

### Step 5. Make your edit

Open the file in your editor and make your changes. For the first deliverable, that means filling in **only your own rows** of the table in `DATA.md`. Save the file.

### Step 6. Check what changed

```sh
git status
git diff
```

You should see only the files you meant to change. If you see anything from `data/` or a file with a key in it, stop (see [If something goes wrong](#if-something-goes-wrong)).

### Step 7. Commit

A commit saves your changes with a short message that explains *why*:

```sh
git add DATA.md
git commit -m "Fill PortWatch register row: fields, coverage, check result"
```

### Step 8. Push

Get your teammate's latest changes first, then upload yours:

```sh
git pull origin issue-N/your-subteam
git push -u origin issue-N/your-subteam
```

### Step 9. Open a pull request

Your subteam opens **one** pull request. Decide who does it.

1. Go to the repository on GitHub. Click the yellow **Compare & pull request** banner.
2. **Check the base branch.** It must say `issue-N/integration`, **not `main`**. Click the base branch dropdown to change it.
3. Write a short description:
   - **What changed:** what you filled in or built
   - **What I left out:** anything you did not finish
   - **How I checked it:** what you ran or compared
4. Click **Create pull request**.

Command line alternative:

```sh
gh pr create --base issue-N/integration --fill
```

### Step 10. Respond to review, then merge

1. A teammate reviews your pull request and may leave comments. If an automated review bot is enabled, wait for its comments and read them too. Read **all** comments before changing anything.
2. Fix what is asked, then commit and push again to the same branch. The pull request updates automatically.
3. Once it is approved, click **Merge pull request** and choose **Create a merge commit** (not squash). Then click **Delete branch**.

Your work is now in `issue-N/integration`. The final merge into `main` is not yours to do.

## Working with your teammate

You share one branch, so:

- **Pull before you push:** `git pull origin issue-N/your-subteam`.
- **Edit different rows.** Split the sources between you (for example, one person per source) so you do not overwrite each other.
- **Tell each other** before you start editing, so you are not both changing the same line.

## Editing in the browser (no terminal)

This is the easiest way to update the `DATA.md` table.

1. On GitHub, switch the branch dropdown (top left of the file view) to **your subteam's branch** (for example `issue-N/ports-traffic`). If it does not exist yet, switch to `issue-N/integration` instead.
2. Open `DATA.md` and click the **pencil icon** (Edit).
3. Edit only your own rows, then click **Commit changes**.
4. If you started from the integration branch, choose **Create a new branch for this commit and start a pull request** and name the branch after your subteam. If you started from your subteam's branch, commit directly to it.
5. When you open the pull request, confirm the base branch is `issue-N/integration`, write your description, and click **Create pull request**.

The table is wide, so a code editor on your computer may be easier to read.

## If something goes wrong

| Problem | Fix |
|---|---|
| I made changes while on `main` | Run `git switch -c issue-N/your-subteam`. Your changes come with you. Do not commit on `main`. |
| `git push` is rejected | Run `git pull origin issue-N/your-subteam`, then push again. |
| My pull request points at `main` | On the pull request page, click **Edit** next to the title and change the base branch to `issue-N/integration`. |
| GitHub says there are **merge conflicts** | See the steps below. |
| I accidentally added a file from `data/` | Do not push. Run `git restore --staged <file>`. If you already committed, run `git reset --soft HEAD~1` and recommit without it. |
| I committed or pushed a key or token | Tell the team right away. Revoke the key and create a new one. |
| I am not sure what to do | Ask before you try anything involving `--force` or `reset --hard`. |

### Fixing a merge conflict

A conflict means two people edited the same lines. This is likely in `DATA.md` because the whole team edits one table.

1. Bring in the latest changes:
   ```sh
   git switch issue-N/your-subteam
   git fetch origin
   git merge origin/issue-N/integration
   ```
2. Open `DATA.md`. Find the markers:
   ```
   <<<<<<< HEAD
   (your version)
   =======
   (their version)
   >>>>>>> origin/issue-N/integration
   ```
3. Keep **both** people's rows. Delete the three marker lines.
4. Save, then finish:
   ```sh
   git add DATA.md
   git commit
   git push
   ```

To avoid conflicts: edit **only your own rows**, and pull the latest changes before you start.

## Rules

- **Never push to `main`.**
- **Never commit data or keys.** Everything in `data/` stays on your computer, and keys stay in `.env`.
- **Update before you branch.** Start from an up-to-date `main`.
- **Keep pull requests small.** One subteam, one task per pull request.
- **Never force-push** (`--force`) without asking.
- **Review each other's pull requests.** Everyone is a reviewer.

## Cheat sheet

| What you want | Command |
|---|---|
| Download the repository | `git clone <url>` |
| Get the latest `main` | `git switch main` then `git pull origin main` |
| See all branches | `git branch -a` |
| Switch to a branch | `git switch <branch>` |
| Create a new branch | `git switch -c <branch>` |
| See what changed | `git status` and `git diff` |
| Stage a file | `git add <file>` |
| Save your changes | `git commit -m "message"` |
| Get your teammate's changes | `git pull origin <branch>` |
| Upload your branch | `git push -u origin <branch>` |
| Open a pull request | `gh pr create --base <branch> --fill` |
| Bring in the latest changes | `git fetch origin` then `git merge origin/<branch>` |