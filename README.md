# Gantt Diagram Editor

This is a simple Gantt Diagram Editor that can be edited by multiple users simultaneously. It was mostly vibe coded.

## Features

![img.png](img.png)

- Add project task phases and deadlines
- Grouping tasks
- Show project timeline by days, weeks and months
- Customize colors and fonts
- Import as CSV
- Export as SVG and PNG
- User management with login and three roles

## Users and roles

Every page requires a login. There are three roles:

| Role | German label | Permissions |
| --- | --- | --- |
| `viewer` | Betrachter | View and export the chart |
| `editor` | Bearbeiter | Additionally edit projects, tasks and settings |
| `admin` | Admin | Additionally manage users at `/admin` |

Roles are enforced on the server, not only in the UI — a viewer receives `403` on
`POST /api/data` even when bypassing the interface.

### Initial admin account

The first admin is created from environment variables in `docker-compose.yml`,
but **only while no user exists yet**. Afterwards, further accounts are created
by an admin under `/admin`.

```yaml
environment:
  GANTT_ADMIN_USER: admin
  GANTT_ADMIN_PASSWORD: "at-least-10-characters"
```

The initial password must be changed on first login. Every account an admin
creates gets an initial password with the same forced change, and resetting a
password immediately invalidates that user's open sessions.

Because a password in a compose file is readable via `docker inspect`, all three
of `GANTT_ADMIN_USER`, `GANTT_ADMIN_PASSWORD` and `GANTT_SECRET_KEY` can also be
read from a file via the `_FILE` suffix, so Docker secrets can be used instead.
See `docker-compose.example.yml` for the details.

### Further settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `GANTT_SECRET_KEY` | generated once, stored in the DB | Signs the session cookies; set explicitly when running several instances |
| `GANTT_COOKIE_SECURE` | `0` | Set to `1` when running behind HTTPS |
| `GANTT_DB` | `gantt.db` | Path to the SQLite database |
| `GANTT_PORT` | `8000` | Port the server listens on |

## Running locally

```bash
pip install -r requirements.txt
GANTT_ADMIN_PASSWORD="at-least-10-characters" python server.py
```

## To Do

- Rework of adding and editing tasks as it takes up too much space