import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import tkinter as tk
import dashboard
from dashboard import quota_rows, account_error, SignInRequired, available_resets, read_account_and_limits


class QuotaTests(unittest.TestCase):
    def test_missing_account_refreshes_before_sign_in_prompt(self):
        class Client:
            calls = []
            def call(self, method, params=None):
                self.calls.append((method, params))
                if method == 'account/read':
                    return {'account': {'type': 'chatgpt'}} if params['refreshToken'] else {'account': None}
                return {'rateLimits': {}}
        client = Client()
        account, _ = read_account_and_limits(client)
        self.assertEqual(account['type'], 'chatgpt')
        self.assertEqual(client.calls[1], ('account/read', {'refreshToken': True}))

    def test_expired_quota_request_refreshes_and_retries(self):
        class Client:
            calls = []
            def call(self, method, params=None):
                self.calls.append((method, params))
                if method == 'account/read':
                    return {'account': {'type': 'chatgpt'}}
                if len([c for c in self.calls if c[0] == 'account/rateLimits/read']) == 1:
                    raise SignInRequired('expired')
                return {'rateLimits': {}}
        client = Client()
        read_account_and_limits(client)
        self.assertEqual(client.calls, [
            ('account/read', {'refreshToken': False}),
            ('account/rateLimits/read', None),
            ('account/read', {'refreshToken': True}),
            ('account/rateLimits/read', None)])

    def test_network_failure_does_not_prompt_for_sign_in(self):
        class Client:
            def call(self, method, params=None):
                if method == 'account/read':
                    return {'account': {'type': 'chatgpt'}}
                raise RuntimeError('offline')
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            read_account_and_limits(Client())

    def test_reset_count_missing_is_not_zero(self):
        for response in ({}, {'rateLimitResetCredits': None}, {'rateLimitResetCredits': {}}):
            self.assertEqual(available_resets(response), 'Unavailable')
        self.assertEqual(available_resets({'rateLimitResetCredits': {'availableCount': 0}}), '0')

    def test_reset_count_is_authoritative_even_with_truncated_details(self):
        self.assertEqual(available_resets({'rateLimitResetCredits': {
            'availableCount': 3, 'credits': []}}), '3')

    def test_sign_in_error_is_distinct_from_network_failure(self):
        self.assertIsInstance(account_error({'message': '401 Unauthorized'}), SignInRequired)
        self.assertNotIsInstance(account_error({'message': 'Connection timeout'}), SignInRequired)

    def test_reserve_label_and_reset_date(self):
        row = quota_rows({'rateLimitsByLimitId': {'base_model_inference': {
            'limitName': 'gpt-reserve', 'primary': {
                'usedPercent': 0, 'windowDurationMins': 10080, 'resetsAt': 1791034246}}}})[0]
        self.assertEqual(row[:2], ('Reserve weekly', '100%'))
        self.assertRegex(row[2], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')

    def test_missing_is_not_zero(self):
        self.assertEqual(quota_rows({}), [])
        row = quota_rows({'rateLimits': {'primary': {'windowDurationMins': 300}}})[0]
        self.assertEqual(row, ('5 hours', '—', '—'))

    def test_weekly_can_be_primary(self):
        row = quota_rows({'rateLimits': {'primary': {
            'usedPercent': 27, 'windowDurationMins': 10080}}})[0]
        self.assertEqual(row[:2], ('Weekly', '73%'))

    def test_multibucket_preferred_over_legacy(self):
        result = {'rateLimits': {'primary': {'usedPercent': 99}},
                  'rateLimitsByLimitId': {
                      'codex': {'primary': {'usedPercent': 0, 'windowDurationMins': 300}},
                      'special': {'secondary': {'usedPercent': 100, 'windowDurationMins': 60}}}}
        self.assertEqual([r[:2] for r in quota_rows(result)], [('5 hours', '100%'), ('special 60 min', '0%')])

    def test_null_buckets(self):
        self.assertEqual(quota_rows({'rateLimitsByLimitId': {'codex': None}}), [])


class RenewalTests(unittest.TestCase):
    def test_date_survives_close_reopen_and_email_change(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(dashboard, 'DATA', Path(folder)):
            root = tk.Tk()
            root.withdraw()
            app = dashboard.Dashboard(root)
            with patch.object(dashboard.simpledialog, 'askstring', return_value='2099-10-25'):
                app.set_renewal()
            self.assertEqual(app.table.set('0', 'renewal'), '2099-10-25')
            app.close()

            root = tk.Tk()
            root.withdraw()
            app = dashboard.Dashboard(root)
            self.assertEqual(app.table.set('0', 'renewal'), '2099-10-25')
            app.settings['0']['email'] = 'old@example.com'
            dashboard.save_settings(app.settings_file, app.settings)
            app.events.put(('row', 0, ('new@example.com', 'pro', 'Weekly: 50%', '2026-10-02 12:00', '1')))
            app.poll()
            self.assertEqual(app.table.set('0', 'renewal'), '2099-10-25 (verify)')
            self.assertEqual(json.loads((Path(folder) / 'settings.json').read_text())['0']['renewal'], '2099-10-25')
            app.close()

    def test_open_window_picks_up_dates_saved_by_another_window(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(dashboard, 'DATA', Path(folder)):
            root = tk.Tk()
            root.withdraw()
            app = dashboard.Dashboard(root)
            self.assertEqual(app.table.set('0', 'renewal'), '—')
            dashboard.save_settings(app.settings_file, {'0': {'renewal': '2099-10-28'}})
            app.refresh_saved_dates()
            self.assertEqual(app.table.set('0', 'renewal'), '2099-10-28')
            app.close()


if __name__ == '__main__':
    unittest.main()
