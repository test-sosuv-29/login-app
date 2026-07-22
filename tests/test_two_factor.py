import os
import sqlite3
import tempfile
import unittest

import app as auth_module


class TwoFactorAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        auth_module.configure_database(f"sqlite:///{self.temp_db.name}")
        auth_module.init_db()
        auth_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = auth_module.app.test_client()

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_new_user_is_redirected_to_two_factor_setup(self):
        response = self.client.post(
            "/register",
            data={"email": "new@example.com", "password": "secret123"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/two-factor/setup")

        conn = sqlite3.connect(self.temp_db.name)
        row = conn.execute(
            "SELECT two_factor_enabled, two_factor_secret FROM users WHERE email=?",
            ("new@example.com",),
        ).fetchone()
        conn.close()

        self.assertEqual(row[0], 0)
        self.assertTrue(row[1])

    def test_existing_user_is_prompted_to_setup_two_factor_on_first_login(self):
        auth_module.create_user("existing@example.com", "password123")

        # Step 1: email goes to the separate password page.
        email_response = self.client.post(
            "/login",
            data={"email": "existing@example.com"},
            follow_redirects=False,
        )

        self.assertEqual(email_response.status_code, 302)
        self.assertEqual(email_response.headers["Location"], "/login/password")

        # Step 2: password on its own page continues to two-factor setup.
        password_response = self.client.post(
            "/login/password",
            data={"password": "password123"},
            follow_redirects=False,
        )

        self.assertEqual(password_response.status_code, 302)
        self.assertEqual(password_response.headers["Location"], "/two-factor/setup")

    def test_password_page_requires_email_step_first(self):
        response = self.client.get("/login/password", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")

    def test_login_with_unknown_email_shows_error(self):
        response = self.client.post(
            "/login",
            data={"email": "nobody@example.com"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No account found with that email", response.data)


if __name__ == "__main__":
    unittest.main()
