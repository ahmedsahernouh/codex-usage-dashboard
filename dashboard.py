"""Small local Codex quota viewer. Uses only Python's standard library."""
import datetime as dt
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import webbrowser

DATA = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CodexUsageDashboard'
REFRESH_MS = 300_000


class SignInRequired(RuntimeError):
    pass


def account_error(error):
    """Classify errors without exposing raw server text or treating offline as logout."""
    detail = json.dumps(error).lower()
    if any(s in detail for s in ('401', 'unauthorized', 'not authenticated',
                                 'authentication required', 'refresh_token_expired',
                                 'refresh_token_reused', 'please log in', 'please sign in')):
        return SignInRequired('Sign-in required')
    return RuntimeError('Refresh failed; retrying automatically. Reconnect if this persists.')


def codex_command():
    exe = shutil.which('codex.exe')
    if exe:
        return [exe]
    package = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@openai/codex'
    binaries = list(package.glob('node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe'))
    if binaries:
        return [str(binaries[0])]
    script = package / 'bin/codex.js'
    if script.exists() and shutil.which('node'):
        return [shutil.which('node'), str(script)]
    raise RuntimeError('Codex CLI not found. Install Codex CLI, then reopen this window.')


class Client:
    def __init__(self, home=None):
        env = os.environ.copy()
        if home is not None:
            home.mkdir(parents=True, exist_ok=True)
            env['CODEX_HOME'] = str(home)
        self.messages = queue.Queue()
        self.seq = 0
        self.proc = subprocess.Popen(
            codex_command() + ['app-server', '--stdio', '-c', 'cli_auth_credentials_store="file"'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'codex_usage_dashboard', 'version': '1.0.0'}})
            self.send({'method': 'initialized'})
        except Exception:
            self.close()
            raise

    def _reader(self):
        for line in self.proc.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                pass
        self.messages.put({'disconnected': True})

    def send(self, value):
        self.proc.stdin.write(json.dumps(value) + '\n')
        self.proc.stdin.flush()

    def receive(self, deadline):
        try:
            value = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
        except queue.Empty:
            raise RuntimeError('Timed out. Try Refresh again.') from None
        if value.get('disconnected'):
            raise RuntimeError('Codex disconnected. Try Refresh again.')
        return value

    def call(self, method, params=None):
        self.seq += 1
        request_id = self.seq
        self.send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            value = self.receive(deadline)
            if value.get('id') == request_id:
                if 'error' in value:
                    # Do not persist or display raw protocol errors (could contain sensitive data).
                    raise account_error(value['error'])
                return value['result']
        raise RuntimeError('Request timed out.')

    def login(self):
        result = self.call('account/login/start', {'type': 'chatgpt'})
        webbrowser.open(result['authUrl'])
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            value = self.receive(deadline)
            if value.get('method') == 'account/login/completed':
                if not value.get('params', {}).get('success'):
                    raise RuntimeError('Sign-in was not completed. Click Connect to retry.')
                return
        raise RuntimeError('Sign-in timed out. Click Connect to retry.')

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream:
                stream.close()


def quota_rows(response):
    """Keep every reported bucket; never assume primary means five hours."""
    buckets = response.get('rateLimitsByLimitId')
    if not buckets:
        legacy = response.get('rateLimits')
        buckets = {'codex': legacy} if legacy else {}
    rows = []
    for key, bucket in sorted(buckets.items(), key=lambda item: (item[0] != 'codex', item[0])):
        if not bucket:
            continue
        for field in ('primary', 'secondary'):
            window = bucket.get(field)
            if not window:
                continue
            minutes = window.get('windowDurationMins')
            duration = {300: '5 hours', 10080: 'Weekly'}.get(minutes, f'{minutes} min' if minutes else '?')
            used = window.get('usedPercent')
            remaining = f'{max(0, min(100, 100 - used)):g}%' if isinstance(used, (int, float)) else '—'
            stamp = window.get('resetsAt')
            reset = dt.datetime.fromtimestamp(stamp).strftime('%Y-%m-%d %H:%M') if stamp else '—'
            name = 'Reserve' if bucket.get('limitName') == 'gpt-reserve' else bucket.get('limitName') or key
            label = duration if key == 'codex' else f'{name} {duration.lower()}'
            rows.append((label, remaining, reset))
    return rows


def available_resets(response):
    credits = response.get('rateLimitResetCredits') or {}
    count = credits.get('availableCount')
    return str(count) if type(count) is int and count >= 0 else 'Unavailable'


def read_account_and_limits(client):
    """Ask Codex to renew its managed session before requesting browser sign-in."""
    account = client.call('account/read', {'refreshToken': False}).get('account')
    if not account:
        account = client.call('account/read', {'refreshToken': True}).get('account')
    if not account:
        raise SignInRequired('Sign-in required')
    try:
        limits = client.call('account/rateLimits/read')
    except SignInRequired:
        # A stale access token can fail the quota call even when account/read
        # still recognizes the stored account. Retry after managed refresh.
        account = client.call('account/read', {'refreshToken': True}).get('account')
        if not account:
            raise SignInRequired('Sign-in required') from None
        limits = client.call('account/rateLimits/read')
    return account, limits


def save_settings(path, settings):
    """Replace the settings file only after the complete JSON has been written."""
    temporary = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    temporary.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    temporary.replace(path)


class Dashboard:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.clients = set()
        self.lock = threading.Lock()
        self.busy = False
        self.prompted = set()
        self.needs_login = set()
        self.last_success = {}
        DATA.mkdir(parents=True, exist_ok=True)
        self.settings_file = DATA / 'settings.json'
        self.settings = {}
        self.reload_settings()
        root.title('Codex usage')
        root.geometry('1240x360')
        root.minsize(1200, 360)
        ttk.Style().configure('Treeview', rowheight=48, font=('Segoe UI', 10))
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        columns = ('account', 'plan', 'remaining', 'reset', 'renewal', 'checked', 'full_resets')
        self.table = ttk.Treeview(frame, columns=columns, show='headings', height=3, selectmode='browse')
        for col, heading, width in zip(columns,
                ('Account', 'Plan', 'Remaining', 'Reset date & time', 'Renewal (manual)', 'Last checked', 'Full resets'),
                (230, 70, 190, 185, 150, 120, 115)):
            self.table.heading(col, text=heading)
            self.table.column(col, width=width, minwidth=70)
        for i in range(3):
            self.table.insert('', 'end', iid=str(i), values=(f'Account {i+1}', '—', '—', '—', self.renewal(i), 'Never', '—'))
        self.table.pack(fill='both', expand=True)
        ttk.Label(frame, text='Weekly = 7-day allowance. Reserve = separate allowance. Each reset date matches the allowance on the same line. Local timezone.').pack(anchor='w', pady=(8, 0))
        bar = ttk.Frame(frame)
        bar.pack(fill='x', pady=(12, 0))
        self.buttons = []
        for title, action in [('Refresh', self.refresh), ('Connect selected account', self.connect),
                              ('Set renewal date', self.set_renewal)]:
            button = ttk.Button(bar, text=title, command=action)
            button.pack(side='left', padx=(0, 8))
            self.buttons.append(button)
        self.status = tk.StringVar(value='')
        ttk.Label(frame, textvariable=self.status).pack(anchor='w', pady=(10, 0))
        self.table.selection_set('0')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)
        root.after(200, self.refresh)
        root.after(REFRESH_MS, self.auto_refresh)
        root.after(5000, self.refresh_saved_dates)

    def reload_settings(self):
        try:
            saved = json.loads(self.settings_file.read_text(encoding='utf-8'))
            if isinstance(saved, dict):
                self.settings = saved
        except (OSError, ValueError):
            # Keep the current copy if another process has a temporary I/O issue.
            pass

    def refresh_saved_dates(self):
        self.reload_settings()
        for i in range(3):
            self.table.set(str(i), 'renewal', self.renewal(i))
        self.root.after(5000, self.refresh_saved_dates)

    def auto_refresh(self):
        if not self.busy:
            self.refresh()
        self.root.after(REFRESH_MS, self.auto_refresh)

    def renewal(self, i):
        slot = self.settings.get(str(i), {})
        value = slot.get('renewal', '')
        if slot.get('renewal_needs_review') and value:
            return value + ' (verify)'
        if value and value < dt.date.today().isoformat():
            return value + ' (past)'
        return value or '—'

    def set_renewal(self):
        self.reload_settings()
        selected = self.table.selection()
        if not selected:
            return
        i = selected[0]
        value = simpledialog.askstring('Renewal date', 'Next renewal date (YYYY-MM-DD). Blank clears it.\nThis date is entered manually, not read from billing.', parent=self.root,
                                       initialvalue=self.settings.get(i, {}).get('renewal', ''))
        if value is None:
            return
        value = value.strip()
        try:
            if value:
                value = dt.date.fromisoformat(value).isoformat()
        except ValueError:
            messagebox.showerror('Invalid date', 'Use YYYY-MM-DD.', parent=self.root)
            return
        slot = self.settings.setdefault(i, {})
        slot['renewal'] = value
        slot.pop('renewal_needs_review', None)
        save_settings(self.settings_file, self.settings)
        self.table.set(i, 'renewal', self.renewal(i))

    def start(self, connect=None):
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.state(['disabled'])
        self.status.set('Sign in to the selected account in your browser…' if connect is not None else 'Refreshing…')
        threading.Thread(target=self.worker, args=(connect,), daemon=True).start()

    def refresh(self):
        self.start()

    def connect(self):
        selected = self.table.selection()
        if selected:
            self.start(int(selected[0]))

    def worker(self, connect):
        failures = 0
        for i in ([connect] if connect is not None else range(3)):
            client = None
            try:
                client = Client(DATA / f'account-{i+1}')
                with self.lock:
                    self.clients.add(client)
                if connect is not None:
                    client.login()
                account, response = read_account_and_limits(client)
                quotas = quota_rows(response)
                remaining = '\n'.join(f'{r[0]}: {r[1]}' for r in quotas) or 'Unavailable'
                resets = '\n'.join(r[2] for r in quotas) or 'Unavailable'
                self.events.put(('row', i, (account.get('email', f'Account {i+1}'), account.get('planType', '—'), remaining, resets, available_resets(response))))
                self.events.put(('height', max(48, len(quotas)*20 + 10)))
            except Exception as exc:
                failures += 1
                self.events.put(('failed', i, isinstance(exc, SignInRequired)))
            finally:
                if client:
                    with self.lock:
                        self.clients.discard(client)
                    client.close()
        self.events.put(('done', failures))

    def poll(self):
        while not self.events.empty():
            event = self.events.get()
            if event[0] == 'row':
                email = event[2][0]
                if '@' in email:
                    self.reload_settings()
                    slot = self.settings.setdefault(str(event[1]), {})
                    if slot.get('email') and slot['email'] != email:
                        # Keep user-entered information even if account identity
                        # changes; mark it for review instead of silently erasing it.
                        slot['renewal_needs_review'] = True
                    if slot.get('email') != email:
                        slot['email'] = email
                        save_settings(self.settings_file, self.settings)
                self.needs_login.discard(event[1])
                self.prompted.discard(event[1])
                self.last_success[event[1]] = dt.datetime.now().strftime('%H:%M:%S')
                self.table.item(str(event[1]), values=(*event[2][:4], self.renewal(event[1]), self.last_success[event[1]], event[2][4]))
            elif event[0] == 'failed':
                i, auth = event[1:]
                old = self.table.item(str(i), 'values')
                self.table.item(str(i), values=(old[0], old[1], 'Sign-in required' if auth else 'Refresh failed',
                                               'Unavailable', self.renewal(i),
                                               ('Stale ' + self.last_success[i]) if i in self.last_success else 'Never', 'Unavailable'))
                if auth:
                    self.needs_login.add(i)
            elif event[0] == 'height':
                style = ttk.Style()
                current = int(style.lookup('Treeview', 'rowheight') or 48)
                style.configure('Treeview', rowheight=max(current, event[1]))
            elif event[0] == 'error':
                self.status.set(event[1])
            elif event[0] == 'done':
                self.busy = False
                for button in self.buttons:
                    button.state(['!disabled'])
                self.status.set('Auto-refresh: every 5 minutes • ' +
                                ('Some accounts need attention; unavailable values are not current.' if event[1] else
                                 'Updated ' + dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                pending = sorted(self.needs_login - self.prompted)
                if pending:
                    self.prompted.update(pending)
                    self.root.after(50, lambda items=pending: self.prompt_login(items))
        self.root.after(100, self.poll)

    def prompt_login(self, items):
        names = ', '.join(f'Account {i+1}' for i in items)
        if messagebox.askyesno('Sign-in required', names + ' need sign-in.\nConnect the first one now?\nYou can also select any row and click Connect.', parent=self.root):
            self.table.selection_set(str(items[0]))
            self.start(items[0])

    def close(self):
        with self.lock:
            for client in self.clients:
                if client.proc.poll() is None:
                    client.proc.terminate()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()
