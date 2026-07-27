# Setup — GitHub Desktop and the Claude desktop app

No terminal required.

## 1. Put the project somewhere sensible

Unzip the download. You get a folder called `auscpi-forecast`. Move that whole
folder to wherever you keep projects — `Documents/projects/` is fine.

Do not put it inside a synced cloud folder that aggressively de-duplicates
(Dropbox, OneDrive selective sync). Git and file-sync tools fight.

## 2. Make it a repository

1. Open **GitHub Desktop**
2. **File → Add Local Repository**
3. Choose the `auscpi-forecast` folder
4. It will warn that this is not a git repository and offer to **create a
   repository here** — click that
5. On the create screen, leave *Git ignore* and *License* as **None**. The
   project already has both, and letting GitHub Desktop add its own will
   conflict.
6. Click **Create Repository**

## 3. First commit

GitHub Desktop now shows every file as a change.

1. Summary: `feat: project scaffold, provenance storage, forecast track record`
2. Click **Commit to main**

## 4. Publish it publicly

1. Click **Publish repository**
2. Name: `auscpi-forecast`
3. **Untick "Keep this code private"**

Public from the first commit is deliberate. The credibility of the whole project
rests on git history proving your forecasts predate the prints. Build privately
for three months and flip it public and the entire history arrives in one push
event, proving nothing about timing.

## 5. Add your API key secrets

On github.com, in your new repository:

**Settings → Secrets and variables → Actions → New repository secret**

- `FUELCHECK_API_KEY`
- `FUELCHECK_API_SECRET`

Free registration at <https://api.nsw.gov.au/Product/Index/22>.

Separately, on your own machine, copy `.env.example` to `.env` and put the same
values in it. `.env` is gitignored and must never be committed.

## 6. Claude Code

Install the Claude desktop app and use the **Code** tab — it runs Claude Code
without a terminal. Point it at the `auscpi-forecast` folder.

Claude Code requires a Pro, Max, Team, Enterprise, or Console account; the free
plan does not include access.

If you prefer the terminal after all, install with
`curl -fsSL https://claude.ai/install.sh | bash` on macOS or Linux, or
`irm https://claude.ai/install.ps1 | iex` in Windows PowerShell, then run
`claude` from the project folder. Docs: <https://code.claude.com/docs/en/setup>.

## The daily loop

The two tools do not overlap and never fight, as long as you keep the split:

| Tool | Does |
|---|---|
| Claude Code | Edits files, installs packages, runs tests, hits live APIs |
| GitHub Desktop | Reviews the diff, commits, pushes |

`CLAUDE.md` instructs Claude Code **not** to run git commands. So the loop is:

1. Ask Claude Code for a change
2. Switch to GitHub Desktop — the changed files appear
3. Read the diff. This is the step that keeps it your project rather than
   something you cannot explain in an interview.
4. Write a commit message, **Commit to main**, **Push origin**

## Commit conventions

Prefix everything. It makes the history readable to someone scrolling it in an
interview and lets you filter the bot's data commits out of your own work.

| Prefix | For |
|---|---|
| `feat:` | new capability |
| `fix:` | bug fix |
| `data:` | automated snapshot commits (the Action writes these) |
| `forecast:` | a track record entry |
| `docs:` | documentation |
| `refactor:` | no behaviour change |
| `test:` | tests only |

## When to commit

Commit when something works, not when something is finished. One logical change
per commit. Push daily at minimum — an unpushed commit is not evidence of
anything.

If GitHub Desktop shows a huge pile of unrelated changes, that is a sign the last
Claude Code session ran too long without review. Shorter sessions, more commits.

Never commit `.env`, raw retailer product data, or anything with a key in it.
