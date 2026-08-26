# TestRail to Qase Migration

Migrates test data from **TestRail** into [Qase](https://qase.io), using the TestRail API and the Qase API.

Free to use for every paying and trialing Qase customer. If you would rather we ran it for you, including adapting it to your data, see [Getting help](#12-getting-help).

---

## 1. What this migrates

Reads a TestRail instance over its REST API and recreates the structure, test cases, execution history and attachments in Qase.

**Supported:** TestRail Cloud and TestRail Server or Data Center, any version exposing the current REST API.

**Not supported:** direct database access. Earlier versions of this script had a partial MySQL path; it never worked and has been removed. All access is through the API.

## 2. Coverage table

| TestRail | Qase | Status | Notes |
|---|---|---|---|
| Project | Project | Full | Created, or reused when the code already exists |
| Suite and section | Suite | Full | Nested sections become nested suites. `testrail.single_suite` collapses everything into one root suite |
| Test case | Test case | Full | Title, description, preconditions, priority, type, custom fields |
| Case steps | Case steps | Full | Both separated steps and the single text field |
| Shared steps | Shared steps | Full | |
| Custom fields | Custom fields | Full | Created in Qase if missing. System field *values* must exist first, see section 4 |
| Attachments | Attachments | Full | On cases, steps and results, including images inline in text |
| Milestone | Milestone | Partial | Title, description, status and due date. TestRail's original creation date cannot be set through the Qase API |
| Test run | Test run | Full | Filtered by `runs.created_after` |
| Test result | Test result | Full | Status, elapsed time, comment, attachments, author |
| Test plan | Test runs | Partial | Each plan entry becomes a run. Plans have no direct Qase equivalent |
| Configurations | Configurations | Full | |
| Users | Users | Partial | Matched by email. Creating missing users needs a SCIM token, see `users.create` |
| Groups | Groups | Full | Needs a SCIM token |
| References (`refs`) | Custom field | Full | Written as text or markdown links. Turning them into linked Jira issues is a separate step, see section 3 |
| Per-case estimate | | Not supported | The Qase bulk case API has no estimate field |
| TestRail reports and dashboards | | Not supported | No Qase equivalent |

## 3. Known limitations

**Re-running duplicates data.** There is no deduplication below project level. See section 10 before you run this twice.

**System field values must exist in Qase first.** Custom fields are created automatically, but values for *system* fields such as case priority, type and result status cannot be created over the API. Add them in Qase before migrating, or unmapped values fall back to a default and a warning is logged. See section 4.

**Jira issue links are a separate step.** This script extracts TestRail's `refs` into a Qase custom field. Turning those keys into linked issues in Qase is done afterwards by the `link_jira_issues` helper, which has a preview mode and can run across every project.

**Per-case estimates are not migrated.** The Qase bulk case endpoint has no field for them.

**Milestone creation dates are not preserved.** The Qase API does not accept one.

**Attachment size limits apply.** Files above the limit on your Qase plan are skipped, and each one is logged as a warning.

**Users are matched, not merged.** A TestRail user with no matching Qase email becomes `users.default` unless `users.create` is enabled.

## 4. Prerequisites

### TestRail

1. Enable the API at **Administration > Site Settings > API**, ticking **Enable API** and **Enable session authentication for API**.
2. Generate a key at **My Settings > API Keys**, then press **Save Settings**. The key is not stored until you do.
3. The account must have the **Administrator** role. A lesser role cannot read every project, and the migration will silently see fewer projects than you expect.

### Qase

1. Create an API token at **Workspace > API tokens**. This is `qase.api_token`.
2. Only if you want missing users and groups created: create a **SCIM token** at **Workspace > SCIM**. This is `qase.scim_token`. Without it, `users.create` and `groups.create` cannot work.
3. Add your **system field values** before migrating, at **Workspace > Fields**: every case priority, case type and result status you use in TestRail needs to exist in Qase first. They cannot be created over the API.

### Before you run

| What | Why |
|---|---|
| An empty target project, or `migration` settings you have read | Re-running duplicates data, section 10 |
| The Qase user that should own unmatched content | `users.default`, an email address |
| Your issue tracker's browse URL, only if you want clickable reference links | `testrail.refs.url`, optional |

## 5. Install

Requires **Python 3.10 or newer**.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

## 6. Configure

Edit `config.json`. Every key below is read by the code, and every key the code reads is listed here.

### Qase

| Key | Required | Default | Meaning |
|---|---|---|---|
| `qase.api_token` | yes | | API token from **Workspace > API tokens** |
| `qase.host` | yes | `qase.io` | Your Qase host. Leave as `qase.io` on the public cloud. On a dedicated cluster set it to your own host, for example `acme.qase.io`. The API URL, the app URL used for `refs` links, and the slower request pacing a dedicated cluster needs are all derived from this |
| `qase.ssl` | no | `true` | Use HTTPS |
| `qase.scim_token` | no | | Needed only for `users.create` or `groups.create` |
| `qase.scim_host` | no | `app.qase.io` | |

### TestRail

| Key | Required | Default | Meaning |
|---|---|---|---|
| `testrail.api.host` | yes | | Instance URL, for example `https://yourcompany.testrail.io` |
| `testrail.api.user` | yes | | Email of an administrator account |
| `testrail.api.token` | yes | | API key from **My Settings > API Keys** |
| `testrail.api.password` | no | | Account password, used only for attachment downloads that require HTML session auth |
| `testrail.api.requests_per_minute` | no | `0` | Throttle TestRail calls. `0` disables throttling |
| `testrail.project_status` | no | `all` | `all`, `active` or `completed` |
| `testrail.single_suite` | no | `false` | Put every case under one root suite instead of mirroring TestRail's sections |
| `testrail.sync` | no | `false` | Run the synchronous importer. Slower, easier to debug |
| `testrail.refs.enable` | no | `true` | Write TestRail's `refs` into a Qase custom field |
| `testrail.refs.url` | no | empty | Issue tracker browse URL, for example `https://you.atlassian.net/browse`. Empty writes references as plain text instead of links |

### Selection

| Key | Required | Default | Meaning |
|---|---|---|---|
| `projects.import_all` | yes | `false` | Migrate every project. When `false`, `projects.import` must list names |
| `projects.import` | yes unless `import_all` | | Project names exactly as they appear in TestRail |
| `projects.exclude` | no | `[]` | Skipped even when `import_all` is true |
| `runs.created_after` | no | `0` | Unix timestamp. Runs older than this are skipped. `0` means all |

### Cases and users

| Key | Required | Default | Meaning |
|---|---|---|---|
| `cases.preserve_ids` | no | `true` | Keep TestRail case ids as Qase case ids where possible |
| `cases.fields` | no | `[]` | Custom fields to migrate. Empty means all |
| `users.default` | yes | | Email address (or numeric id) of the Qase user who owns anything that cannot be matched |
| `users.map` | no | `{}` | Explicit overrides, `"testrail@email": "qase@email"` or a numeric Qase id |
| `users.migrate` | no | `true` | Build the user map at all. When `false`, everything is authored by `users.default` |
| `users.create` | no | `false` | Create missing users in Qase via SCIM. **Consumes seats** |
| `users.only_active` | no | `true` | Skip users deactivated in TestRail. Setting this to `false` creates deactivated accounts in Qase and consumes seats for them |
| `groups.create` | no | `false` | Create the group named below. Needs a SCIM token |
| `groups.name` | no | `TestRail Migration` | |

### Logging and output

| Key | Required | Default | Meaning |
|---|---|---|---|
| `logging.level` | no | `info` | `error`, `warn`, `info`, `verbose` or `debug`. Each includes the ones before it |
| `logging.write_to_file` | no | `true` | Write a log file under `logging.dir` |
| `logging.dir` | no | `./logs` | |
| `prefix` | no | | Prefix for log and statistics filenames |

Tokens can also be supplied as environment variables, which take precedence over the file: `QASE_API_TOKEN`, `QASE_SCIM_TOKEN`.

## 7. Validate

Always run this before a real migration. It is read-only and writes nothing.

```bash
python preflight.py
```

It checks that the config parses, that no placeholder values are left, that both APIs accept your credentials, that every project you named exists in TestRail, and that `users.default` resolves to a real Qase user.

```
--- Config ---
  PASS  Config file ./config.json: parses
  PASS  qase.api_token (Qase API token)
  ...
--- TestRail ---
  PASS  TestRail authentication: 14 project(s) visible
  PASS  Project 'Mobile App': found in TestRail
--- Qase ---
  PASS  Qase authentication
  PASS  users.default (ops@company.com): resolves to user id 42

All checks passed. Safe to run: python start.py
```

Exit code is `0` when everything passes and `1` otherwise, so it can gate a scripted run.

## 8. Run

```bash
python start.py                      # uses ./config.json
python start.py my-config.json       # or a path you choose
python start.py --dry-run            # read everything, write nothing
```

`--dry-run` runs the entire pipeline against real TestRail and Qase data and reports exactly what it *would* create, without writing anything. Unmapped statuses, missing fields, oversized values and attachment problems all surface. Use it before the real run on any migration you care about. `QASE_DRY_RUN=1` does the same thing.

**Expected duration.** Roughly 10 to 30 minutes for a few thousand cases. Attachments dominate: an instance with tens of thousands of files can run for several hours. Runs and results are the next largest factor.

## 9. What good output looks like

```
	↪ Importing projects [14/14]
	↪ Building users map [212/212]
	↪ Importing custom fields [31/31]
	↪ Importing suites [388/388]
	↪ Importing cases [12480/12480]
	↪ Importing runs [1206/1206]
```

A green `✓` replaces the arrow as each stage completes.

**Warnings and errors are printed in colour on stderr as they happen**, even at the default logging level. A clean run shows none. If you see them scroll past, the migration is still running but something was skipped, and the detail is in the log file.

Afterwards:

- `logs/<prefix>_testrail_<timestamp>.log`, the full log at your configured level
- `stats/<prefix>_stats.json` and `.xlsx`, counts per entity type, source against target

Compare the statistics against TestRail before you tell anyone the migration is done. Counts matching is the quickest signal that nothing was silently dropped.

## 10. Re-run and resume behavior

**Read this before running twice.**

There is no resume. If a run fails halfway, restarting it begins again from the first project.

Deduplication exists **only at project level**: a project whose code already exists in Qase is reused rather than recreated. Everything below that is create-only. **Suites, cases, runs, results and attachments are created again on every run**, so a second run against the same target project produces a duplicate set.

If a run fails partway, either delete the target project in Qase and start over, or migrate the remaining projects individually with `projects.import`.

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Config file not found` | No `config.json` | `cp config.example.json config.json` and fill it in |
| `401` from Qase | Wrong or revoked token | Check `qase.api_token`. Run `python preflight.py` |
| `401` or `403` from TestRail | API disabled, or key not saved | Enable the API and press **Save Settings** after generating the key, section 4 |
| Fewer projects than expected | The TestRail account is not an administrator | Use an administrator account |
| `Project 'X' not found in TestRail` | Name mismatch | Use the exact name, including spacing and case |
| Cases created without priority or type | System field values missing in Qase | Add them at **Workspace > Fields**, then re-run into a clean project |
| Custom fields missing, run looked fine | A `422` was logged but not shown | Check the log file. Custom fields are workspace-global in Qase, so a field scoped to another project fails validation |
| `users.default ... no Qase user has that email` | Typo, or the user is not in this workspace | Use an existing Qase user's email, or a numeric id |
| Attachments missing | Above the size limit on your plan, or needing session auth | Check warnings in the log. Set `testrail.api.password` for HTML-authenticated downloads |
| Duplicate cases after a second run | Expected, see section 10 | Migrate into a clean project |
| Rate limiting from TestRail | Instance limits concurrent API use | Set `testrail.api.requests_per_minute` |

## 12. Getting help

Email **migrations@qase.io**.

GitHub Issues and Discussions are disabled on this repository, so email is the way to reach us.

To get a useful answer on the first reply, include:

- What you ran, and the output of `python preflight.py`
- Your `config.json` **with every token removed**
- The relevant part of the log from `logs/`, again with tokens removed
- Roughly how many projects, cases and attachments are involved

Please do not send API tokens, passwords or customer data. If a log is large, describe the error and we will tell you what to send.

**Want us to run it?** A fully managed migration, including adapting the script to your data structures, is available as a paid service. Email the same address.
