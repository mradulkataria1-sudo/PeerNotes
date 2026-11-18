# PeerNotes: Collaborative Note-Sharing & Peer Review Portal

Django project for the Full Stack Development Lab (24B15CS314).

## Run it

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo          # optional demo data (safe to re-run)
python manage.py createsuperuser    # for the admin panel
python manage.py runserver
```

Open http://127.0.0.1:8000/. Demo logins (after `seed_demo`): `asha`, `ben`, `chitra`, `dev` (students), `moderator` (Moderators group) and `reviewer` (Reviewers group); password `peernotes123`.
Run tests with `python manage.py test` (115 tests, temporary `MEDIA_ROOT`, no network needed).

**Upgrading an existing database.** `python manage.py migrate` applies `0002_duplicates_reports_thumbnails` on top of existing data (no existing migration is edited) and refreshes the Moderators/Reviewers permissions. Notes uploaded before the upgrade have no extracted text or thumbnail yet; fill them in with

```bash
python manage.py backfill_notes          # only notes that are missing something; safe to re-run
python manage.py backfill_notes --all    # reprocess everything
```

Only pip packages are needed: PDF thumbnails use PyMuPDF (a self-contained wheel), so poppler is **not** required. Nothing else needs installing on a fresh machine with SQLite.

## What's new

| # | Feature | How it works |
|---|---|---|
| 1 | **Duplicate detection** | On upload, text is extracted (PDF via `pypdf`, DOCX via `python-docx`, TXT/MD directly; images and other types are skipped gracefully) and stored on `Note.extracted_text`. It is compared with the other notes **of the same subject** using TF-IDF cosine similarity (scikit-learn). At or above the threshold (default 0.85) the uploader sees a warning listing the similar notes with links and similarity percentages and must press **Upload anyway** (or **Discard upload**). The file is parked in `MEDIA_ROOT/pending/` plus the session while they decide, so they don't have to re-pick it. The closest match is stored as `Note.similarity_score` and, when above the threshold, `Note.duplicate_of`. Moderators get a **duplicate check** filter and a similarity column in the admin note list. Replacing a note's file on edit refreshes the text and score but never blocks the edit. |
| 2 | **Better ranking** | `trending_score = recency × (0.7 × weighted rating + 0.3 × normalised downloads)`. Weighted rating is the Bayesian average `(v/(v+m))·R + (m/(v+m))·C` (R = note mean, v = its rating count, C = global mean, m = `BAYESIAN_MIN_VOTES`, default 5). Recency is `0.5 ** (age_days / TRENDING_HALF_LIFE_DAYS)` (default 90 days). All of it is one `annotate()` chain in `NoteQuerySet.with_stats()`; C, m and the max download count are single scalars fetched once per query. The dashboard text explains the formula and shows the live C and m. |
| 3 | **Report and review queue** | `Report` model (reason: duplicate / spam / wrong subject / inappropriate / other; free text; status open / resolved / dismissed; `resolved_by`; timestamps). Students click **Report** on a note page (AJAX modal). `/moderation/` lists open reports with **Remove note**, **Mark resolved** and **Dismiss**, plus a "recently handled" table and a CSV export. Access is by Django permission (`view_report`, `change_report`, `delete_note`), never by username. A new **Reviewers** group can view/resolve/dismiss reports but has no `delete_note`, so it cannot remove notes. |
| 4 | **PDF preview** | The note page embeds the PDF in an `<iframe>` served inline by `/notes/<id>/preview/` (login required, same-origin framing only, not counted as a download). Page 1 is rendered to a PNG thumbnail with PyMuPDF at upload time and shown on note cards; other file types get a placeholder icon. Thumbnail or text-extraction failures are logged and never block an upload. |
| 5 | **REST API** | Django REST Framework, token auth, pagination and throttling. See below. |
| + | **CSV export** (optional extra) | `/moderation/export.csv` for moderators/reviewers. Cells that start with `= + - @` are prefixed with `'` so spreadsheets can't execute them. |
| + | **CI** (optional extra) | `.github/workflows/tests.yml` runs `check`, `makemigrations --check` and the test suite. |

## URLs added

| URL | Name | Who |
|---|---|---|
| `POST /notes/<id>/report/` | `report_note` | logged-in students (not the uploader); AJAX/JSON |
| `GET /notes/<id>/preview/` | `note_preview` | logged-in users; PDFs only |
| `GET /moderation/` | `moderation_queue` | `notes.view_report` |
| `POST /moderation/<report_id>/<remove\|resolve\|dismiss>/` | `report_action` | `change_report` (remove also needs `delete_note`) |
| `GET /moderation/export.csv` | `moderation_export` | `notes.view_report` |
| `/api/...` | see below | |

Existing URL names, model field names and templates' contracts are unchanged.

## REST API

Base path `/api/`. Authenticate with `Authorization: Token <key>`. Reads are public; writes need a token. Errors are JSON. Pages hold 9 items (`?page=`, `?page_size=` up to 50).

| Endpoint | Method | Notes |
|---|---|---|
| `/api/auth/token/` | POST `username`, `password` | returns `{"token": ...}`; throttled at 10/min |
| `/api/notes/` | GET | same filters as the web feed: `q`, `subject`, `semester`, `branch`, `tag`, `sort` (`trending`, `newest`, `rated`, `downloads`) |
| `/api/notes/<id>/` | GET | adds `my_rating` and `bookmarked` for the token user |
| `/api/notes/<id>/rate/` | POST `{"stars": 1-5}` | 201 first time, 200 on change; 403 on your own note; 400 outside 1-5; one rating per user |
| `/api/notes/<id>/comments/` | GET, POST `{"text": ...}` | text 1-1000 chars, author is always the token user |
| `/api/notes/<id>/bookmark/` | POST | toggles; returns `{"bookmarked": bool}` |
| `/api/trending/` | GET | top notes by trending score, `?limit=` (default 10, max 50) |
| `/api/me/` | GET | profile, roles and ORM-aggregated stats |

Note objects include `avg_rating`, `rating_count`, `comment_count` and `trending_score`. Example:

```bash
TOKEN=$(curl -s -d "username=ben&password=peernotes123" http://127.0.0.1:8000/api/auth/token/ | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" -d '{"stars":4}' http://127.0.0.1:8000/api/notes/3/rate/
```

## Settings

All in `peernotes/settings.py`; the ones marked *env* can also be set as environment variables.

| Setting | Default | Meaning |
|---|---|---|
| `DUPLICATE_SIMILARITY_THRESHOLD` (*env*) | `0.85` | cosine similarity at or above which an upload is flagged |
| `DUPLICATE_MIN_CHARS` | `50` | texts shorter than this are never compared |
| `DUPLICATE_MAX_RESULTS` | `5` | similar notes listed in the warning |
| `EXTRACT_MAX_CHARS` / `EXTRACT_MAX_PDF_PAGES` | `200000` / `60` | extraction limits |
| `BAYESIAN_MIN_VOTES` (*env*) | `5` | `m` in the weighted average |
| `TRENDING_HALF_LIFE_DAYS` (*env*) | `90` | recency half-life; `0` switches decay off |
| `THUMBNAIL_WIDTH_PX` | `480` | thumbnail width |
| `REPORT_DETAILS_MAX_CHARS` | `500` | free-text limit on reports |
| `REST_FRAMEWORK` | token auth, 9/page | throttles `anon` 60/min, `user` 240/min, `token_auth` 10/min (*env*: `API_THROTTLE_ANON`, `API_THROTTLE_USER`, `API_THROTTLE_TOKEN`) |

## Synopsis to code map

| Synopsis item | Where |
|---|---|
| Models: Subject, Note, Rating, Comment, Bookmark | `notes/models.py` |
| One rating per (note, user), DB-enforced | `Rating.Meta.unique_together` |
| Trending score: weighted (Bayesian) rating, 0.7/0.3 weighting, recency decay | `NoteQuerySet.with_stats()`, `ranking_params()`, `AgeInDays` in `notes/models.py`; explained on `/dashboard/` |
| CRUD for notes, forms, validation | `notes/views.py`, `notes/forms.py` |
| Roles: students, moderators, reviewers | `Moderators` and `Reviewers` groups auto-created in `notes/signals.py`; checks use Django permissions |
| Moderation | Report queue `/moderation/` (`moderation_queue`, `report_action`), `Report` model, admin (`NoteAdmin`, `ReportAdmin`) |
| Duplicate / plagiarism detection | `notes/textutils.py` (extraction), `notes/similarity.py` (TF-IDF), `notes/services.py`, upload flow in `note_create`; admin `LikelyDuplicateFilter` |
| PDF preview and thumbnails | `notes/previews.py`, `note_preview` view, `templates/notes/note_detail.html`, `_note_grid.html` |
| REST API | `notes/api/` (`views.py`, `serializers.py`, `pagination.py`, `urls.py`), `REST_FRAMEWORK` in settings |
| Reports: trending, top contributors, subject rating trends | `dashboard` view, `/dashboard/` |
| AJAX rating, comments, bookmarks, reporting, live search/filter | `static/js/app.js`, JSON/partial endpoints in `notes/views.py` |
| Shared feed filters (web and API) | `notes/filters.py` |
| SQLite dev, optional PostgreSQL | `peernotes/settings.py` (set `POSTGRES_DB`, `POSTGRES_USER`, ...) |
| Tests | `notes/tests.py` (original), `test_duplicates.py`, `test_ranking.py`, `test_reports.py`, `test_preview.py`, `test_api.py`, `test_commands.py` (seed, backfill, migration on existing data); helpers in `factories.py` |

## Making a moderator or reviewer
In `/admin/`: create or open a user and add them to the **Moderators** group (can also remove notes) or the **Reviewers** group (resolve/dismiss reports only). Staff status is only needed for the admin panel; `/moderation/` works from the group's permissions alone.

## Notes
- Downloads are counted by the `/notes/<id>/download/` view and require login. In development (`DEBUG=1`) Django also serves `media/` directly, so uploaded files are reachable by URL; in production serve `media/` through your web server with access rules of your choice.
- Abandoned duplicate warnings leave one file in `media/pending/` per session at most (it is replaced on the next upload, deleted on confirm or discard). It is safe to clear that folder.
- The recency and download terms are computed with database-specific SQL for the note age (`julianday` on SQLite, `EXTRACT(EPOCH ...)` on PostgreSQL). The SQLite path is covered by tests; the PostgreSQL path has not been run here.
- Production: set `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and serve `media/` via your web server.
