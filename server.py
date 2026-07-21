import hashlib
import json
import os
import secrets
import sqlite3
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.security import check_password_hash, generate_password_hash

DB_FILE = os.environ.get('GANTT_DB', 'gantt.db')
PORT = int(os.environ.get('GANTT_PORT', '8000'))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Rollen. Reihenfolge = aufsteigende Rechte.
ROLE_VIEWER = 'viewer'
ROLE_EDITOR = 'editor'
ROLE_ADMIN = 'admin'
ROLES = (ROLE_VIEWER, ROLE_EDITOR, ROLE_ADMIN)
ROLE_LABELS = {
    ROLE_VIEWER: 'Betrachter',
    ROLE_EDITOR: 'Bearbeiter',
    ROLE_ADMIN: 'Admin',
}
EDITING_ROLES = (ROLE_EDITOR, ROLE_ADMIN)

MIN_PASSWORD_LENGTH = 10

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))


# ─── Datenbank ────────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS app_state (id INTEGER PRIMARY KEY, json_data TEXT)')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                     password_hash TEXT NOT NULL,
                     role TEXT NOT NULL,
                     must_change_password INTEGER NOT NULL DEFAULT 0
                 )''')
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')

    c.execute('SELECT COUNT(*) FROM app_state')
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO app_state (id, json_data) VALUES (1, '{}')")

    conn.commit()
    conn.close()


def get_setting(key):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row[0] if row else None


def set_setting(key, value):
    conn = sqlite3.connect(DB_FILE)
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()


# ─── Secret Key ───────────────────────────────────────────────────────────────

def resolve_secret_key():
    """Bevorzugt GANTT_SECRET_KEY. Ohne Konfiguration wird einmalig ein
    zufälliger Schlüssel erzeugt und in der DB abgelegt, damit Sessions
    Neustarts überleben."""
    env_key = read_env_or_file('GANTT_SECRET_KEY')
    if env_key:
        return env_key
    stored = get_setting('secret_key')
    if stored:
        return stored
    generated = secrets.token_hex(32)
    set_setting('secret_key', generated)
    return generated


def read_env_or_file(name):
    """Liest NAME oder – für Docker Secrets – NAME_FILE."""
    path = os.environ.get(name + '_FILE')
    if path:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except OSError as err:
            raise RuntimeError(f'{name}_FILE nicht lesbar: {err}') from err
    value = os.environ.get(name)
    return value.strip() if value else None


# ─── Benutzer ─────────────────────────────────────────────────────────────────

def session_token(password_hash):
    """Kurzer Fingerabdruck des Passwort-Hashes. Wandert in die Session, damit
    ein Passwortwechsel alle bestehenden Sessions ungültig macht – sonst bliebe
    ein zurückgesetzter Zugang weiter offen."""
    return hashlib.sha256(password_hash.encode('utf-8')).hexdigest()[:16]


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row['id'])
        self.username = row['username']
        self.role = row['role']
        self.must_change_password = bool(row['must_change_password'])
        self.session_token = session_token(row['password_hash'])

    def get_id(self):
        return f'{self.id}:{self.session_token}'

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def can_edit(self):
        return self.role in EDITING_ROLES

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN


login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Bitte melden Sie sich an.'


@login_manager.user_loader
def load_user(session_id):
    raw_id, _, token = session_id.partition(':')
    row = get_db().execute('SELECT * FROM users WHERE id = ?', (raw_id,)).fetchone()
    if not row:
        return None
    # Passwort geändert oder zurückgesetzt -> alte Session verfällt.
    if not secrets.compare_digest(token, session_token(row['password_hash'])):
        return None
    return User(row)


def create_user(username, password, role, must_change_password=True):
    get_db().execute(
        'INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?, ?, ?, ?)',
        (username, generate_password_hash(password), role, int(must_change_password)))
    get_db().commit()


def bootstrap_admin():
    """Legt den initialen Admin an – nur solange noch kein Benutzer existiert."""
    conn = sqlite3.connect(DB_FILE)
    count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    if count > 0:
        conn.close()
        return

    username = read_env_or_file('GANTT_ADMIN_USER') or 'admin'
    password = read_env_or_file('GANTT_ADMIN_PASSWORD')
    if not password:
        conn.close()
        raise RuntimeError(
            'Es existiert noch kein Benutzer und GANTT_ADMIN_PASSWORD ist nicht gesetzt. '
            'Bitte in der docker-compose.yml GANTT_ADMIN_PASSWORD (oder GANTT_ADMIN_PASSWORD_FILE) '
            'hinterlegen, um den initialen Admin-Zugang anzulegen.')
    if len(password) < MIN_PASSWORD_LENGTH:
        conn.close()
        raise RuntimeError(f'GANTT_ADMIN_PASSWORD muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.')

    conn.execute(
        'INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?, ?, ?, 1)',
        (username, generate_password_hash(password), ROLE_ADMIN))
    conn.commit()
    conn.close()
    print(f'Initialer Admin "{username}" angelegt. Das Passwort muss beim ersten Login geändert werden.')


# ─── Zugriffsschutz ───────────────────────────────────────────────────────────

def editor_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.can_edit:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def force_password_change():
    """Wer ein Initialpasswort hat, kommt nur an die Passwortseite."""
    if not current_user.is_authenticated or not current_user.must_change_password:
        return None
    if request.endpoint in ('change_password', 'logout', 'static'):
        return None
    if request.path.startswith('/api/'):
        return jsonify({'error': 'password_change_required'}), 403
    return redirect(url_for('change_password'))


# ─── Authentifizierung ────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        row = get_db().execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            login_user(User(row))
            return redirect(url_for('index'))
        flash('Benutzername oder Passwort ist falsch.', 'error')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/passwort', methods=['GET', 'POST'], endpoint='change_password')
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        repeat = request.form.get('repeat_password', '')

        row = get_db().execute('SELECT password_hash FROM users WHERE id = ?', (current_user.id,)).fetchone()
        if not check_password_hash(row['password_hash'], current):
            flash('Das aktuelle Passwort ist falsch.', 'error')
        elif len(new) < MIN_PASSWORD_LENGTH:
            flash(f'Das neue Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.', 'error')
        elif new != repeat:
            flash('Die beiden neuen Passwörter stimmen nicht überein.', 'error')
        elif new == current:
            flash('Das neue Passwort muss sich vom bisherigen unterscheiden.', 'error')
        else:
            user_id = current_user.id
            get_db().execute(
                'UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?',
                (generate_password_hash(new), user_id))
            get_db().commit()
            # Der Passwortwechsel entwertet die eigene Session ebenfalls –
            # deshalb hier direkt neu anmelden.
            row = get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            login_user(User(row))
            flash('Passwort geändert.', 'success')
            return redirect(url_for('index'))
    return render_template('change_password.html', forced=current_user.must_change_password,
                           min_length=MIN_PASSWORD_LENGTH)


# ─── Benutzerverwaltung (nur Admin) ───────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin():
    rows = get_db().execute('SELECT * FROM users ORDER BY username COLLATE NOCASE').fetchall()
    return render_template('admin.html', users=rows, roles=ROLES, role_labels=ROLE_LABELS,
                           min_length=MIN_PASSWORD_LENGTH)


@app.route('/admin/users', methods=['POST'])
@admin_required
def admin_create_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', '')

    if not username:
        flash('Bitte einen Benutzernamen angeben.', 'error')
    elif role not in ROLES:
        flash('Unbekannte Rolle.', 'error')
    elif len(password) < MIN_PASSWORD_LENGTH:
        flash(f'Das Initialpasswort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.', 'error')
    else:
        try:
            create_user(username, password, role, must_change_password=True)
            flash(f'Benutzer "{username}" angelegt. Das Passwort muss beim ersten Login geändert werden.', 'success')
        except sqlite3.IntegrityError:
            flash(f'Der Benutzername "{username}" ist bereits vergeben.', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/users/<int:user_id>/rolle', methods=['POST'])
@admin_required
def admin_set_role(user_id):
    role = request.form.get('role', '')
    if role not in ROLES:
        flash('Unbekannte Rolle.', 'error')
        return redirect(url_for('admin'))
    if str(user_id) == current_user.id and role != ROLE_ADMIN:
        flash('Sie können sich nicht selbst die Admin-Rolle entziehen.', 'error')
        return redirect(url_for('admin'))
    if not role == ROLE_ADMIN and is_last_admin(user_id):
        flash('Der letzte verbliebene Admin kann nicht herabgestuft werden.', 'error')
        return redirect(url_for('admin'))

    get_db().execute('UPDATE users SET role = ? WHERE id = ?', (role, user_id))
    get_db().commit()
    flash('Rolle geändert.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/users/<int:user_id>/passwort', methods=['POST'])
@admin_required
def admin_reset_password(user_id):
    password = request.form.get('password', '')
    if len(password) < MIN_PASSWORD_LENGTH:
        flash(f'Das neue Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.', 'error')
    else:
        get_db().execute(
            'UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?',
            (generate_password_hash(password), user_id))
        get_db().commit()
        flash('Passwort zurückgesetzt. Es muss beim nächsten Login geändert werden.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/users/<int:user_id>/loeschen', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    if str(user_id) == current_user.id:
        flash('Sie können Ihren eigenen Zugang nicht löschen.', 'error')
    elif is_last_admin(user_id):
        flash('Der letzte verbliebene Admin kann nicht gelöscht werden.', 'error')
    else:
        get_db().execute('DELETE FROM users WHERE id = ?', (user_id,))
        get_db().commit()
        flash('Benutzer gelöscht.', 'success')
    return redirect(url_for('admin'))


def is_last_admin(user_id):
    row = get_db().execute('SELECT role FROM users WHERE id = ?', (user_id,)).fetchone()
    if not row or row['role'] != ROLE_ADMIN:
        return False
    count = get_db().execute('SELECT COUNT(*) FROM users WHERE role = ?', (ROLE_ADMIN,)).fetchone()[0]
    return count <= 1


# ─── Anwendung & API ──────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return send_from_directory(BASE_DIR, 'gantt-editor.html')


@app.route('/api/me')
@login_required
def api_me():
    return jsonify({
        'username': current_user.username,
        'role': current_user.role,
        'role_label': current_user.role_label,
        'can_edit': current_user.can_edit,
        'is_admin': current_user.is_admin,
        'csrf_token': generate_csrf(),
    })


@app.route('/api/data', methods=['GET'])
@login_required
def api_get_data():
    row = get_db().execute('SELECT json_data FROM app_state WHERE id = 1').fetchone()
    return app.response_class(
        response=row['json_data'] if row else '{}',
        mimetype='application/json',
        headers={'Cache-Control': 'no-cache, no-store, must-revalidate'})


@app.route('/api/data', methods=['POST'])
@editor_required
def api_save_data():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({'error': 'Invalid JSON'}), 400
    get_db().execute('UPDATE app_state SET json_data = ? WHERE id = 1', (json.dumps(payload),))
    get_db().commit()
    return jsonify({'status': 'success'})


@app.errorhandler(403)
def forbidden(err):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'forbidden'}), 403
    return render_template('403.html'), 403


# ─── Start ────────────────────────────────────────────────────────────────────

def configure():
    init_db()
    app.secret_key = resolve_secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        # Hinter HTTPS-Reverse-Proxy auf 1 setzen, damit das Cookie nur
        # verschlüsselt übertragen wird.
        SESSION_COOKIE_SECURE=os.environ.get('GANTT_COOKIE_SECURE', '0') == '1',
    )
    CSRFProtect(app)
    bootstrap_admin()


configure()


if __name__ == '__main__':
    from waitress import serve
    print(f'Starte Server auf http://localhost:{PORT}')
    serve(app, host='0.0.0.0', port=PORT)
