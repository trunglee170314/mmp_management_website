"""Relax strength rules without weakening authentication or reset authorization."""
import json

from django.conf import settings
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.test import Client, TestCase
from django.urls import reverse

from .forms import AdminPasswordResetForm, RegistrationForm, UserCreateForm, UserEditForm
from .models import AuditLog, PasswordResetRequest, User


class InternalPasswordPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="password-admin", display_name="Password Admin", role="admin", account_status="active", password="old-admin-password")
        cls.member = User.objects.create_user(username="employee", display_name="Employee", email="employee@example.test", account_status="active", password="old-member-password")

    def creation_data(self, password="1", username="simple-new-user"):
        return {"username": username, "display_name": "New Employee", "email": "", "role": "member", "password1": password, "password2": password}

    def test_all_strength_validators_are_disabled(self):
        self.assertEqual(settings.AUTH_PASSWORD_VALIDATORS, [])

    def test_registration_and_admin_creation_accept_short_numeric_common_and_similar_passwords(self):
        for form_type in [RegistrationForm, UserCreateForm]:
            for password in ["1", "a", "12345678", "password", "simple-new-user"]:
                with self.subTest(form=form_type.__name__, password_kind=password):
                    form = form_type(self.creation_data(password))
                    self.assertTrue(form.is_valid(), form.errors)
                    user = form.save(commit=False)
                    self.assertTrue(user.check_password(password))
                    self.assertNotEqual(user.password, password, "Password must still be hashed")

    def test_edit_reset_and_django_password_forms_share_the_relaxed_policy(self):
        for password in ["1", "a", "12345678", "password", "employee"]:
            with self.subTest(password_kind=password):
                edit = UserEditForm({"username": self.member.username, "display_name": self.member.display_name,
                                     "email": self.member.email, "role": "member", "new_password": password}, instance=self.member)
                self.assertTrue(edit.is_valid(), edit.errors)
                reset = AdminPasswordResetForm({"new_password1": password, "new_password2": password}, user=self.member)
                self.assertTrue(reset.is_valid(), reset.errors)
                standard = SetPasswordForm(self.member, {"new_password1": password, "new_password2": password})
                self.assertTrue(standard.is_valid(), standard.errors)
                change = PasswordChangeForm(self.member, {"old_password": "old-member-password", "new_password1": password, "new_password2": password})
                self.assertTrue(change.is_valid(), change.errors)

    def test_required_password_and_confirmation_are_still_enforced(self):
        for form_type in [RegistrationForm, UserCreateForm]:
            for first, second in [("", ""), ("1", "2"), ("1", "")]:
                with self.subTest(form=form_type.__name__, first=first, second=second):
                    self.assertFalse(form_type({**self.creation_data(), "password1": first, "password2": second}).is_valid())
        for first, second in [("", ""), ("1", "2"), ("1", "")]:
            self.assertFalse(AdminPasswordResetForm({"new_password1": first, "new_password2": second}, user=self.member).is_valid())
            self.assertFalse(SetPasswordForm(self.member, {"new_password1": first, "new_password2": second}).is_valid())

    def test_blank_optional_admin_edit_password_preserves_current_hash(self):
        old_hash = self.member.password
        form = UserEditForm({"username": self.member.username, "display_name": self.member.display_name, "role": "member", "new_password": ""}, instance=self.member)
        self.assertTrue(form.is_valid(), form.errors)
        form.save(); self.member.refresh_from_db()
        self.assertEqual(self.member.password, old_hash)

    def test_registration_accepts_simple_password_but_still_requires_account_approval(self):
        response = self.client.post(reverse("register"), self.creation_data())
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="simple-new-user")
        self.assertTrue(user.check_password("1")); self.assertFalse(user.is_active)
        self.assertEqual(user.account_status, User.AccountStatus.PENDING)
        self.assertFalse(self.client.login(username=user.username, password="1"))

    def test_registration_page_has_no_strength_checklist_or_live_password_requests(self):
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 200)
        for text in ["password-rules", "data-password-check-url", "at least 8 characters", "commonly used password", "entirely numeric"]:
            self.assertNotContains(response, text)
        form = response.context["form"]
        self.assertTrue(form.fields["password1"].required)
        self.assertTrue(form.fields["password2"].required)
        self.assertNotIn("minlength", form.fields["password1"].widget.attrs)

    def test_admin_can_create_and_edit_a_user_with_a_simple_password(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("user_add"), self.creation_data())
        self.assertRedirects(response, reverse("users"))
        user = User.objects.get(username="simple-new-user")
        self.assertTrue(user.check_password("1")); self.assertTrue(user.is_active)
        response = self.client.post(reverse("user_edit", args=[user.pk]), {
            "username": user.username, "display_name": user.display_name, "role": "member", "new_password": "a",
        })
        self.assertRedirects(response, reverse("users")); user.refresh_from_db()
        self.assertTrue(user.check_password("a")); self.assertNotEqual(user.password, "a")

    def test_self_change_still_requires_correct_current_password_and_keeps_current_session(self):
        self.client.force_login(self.member)
        other_session = Client(); other_session.force_login(self.member)
        payload = {"old_password": "incorrect", "new_password1": "1", "new_password2": "1"}
        response = self.client.post(reverse("password_change"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("old_password", response.context["form"].errors)
        self.member.refresh_from_db(); self.assertTrue(self.member.check_password("old-member-password"))
        payload["old_password"] = "old-member-password"
        response = self.client.post(reverse("password_change"), payload)
        self.assertRedirects(response, reverse("profile"))
        self.member.refresh_from_db(); self.assertTrue(self.member.check_password("1"))
        self.assertNotEqual(self.member.password, "1")
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)
        self.assertEqual(other_session.get(reverse("profile")).status_code, 302, "Old sessions must still be invalidated")

    def test_password_change_blank_or_mismatched_new_password_does_not_modify_password(self):
        self.client.force_login(self.member)
        for first, second in [("", ""), ("1", "2")]:
            response = self.client.post(reverse("password_change"), {"old_password": "old-member-password", "new_password1": first, "new_password2": second})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.member.refresh_from_db(); self.assertTrue(self.member.check_password("old-member-password"))

    def test_admin_reset_accepts_simple_password_preserves_audit_and_never_stores_plaintext(self):
        self.client.force_login(self.admin)
        reset = PasswordResetRequest.objects.create(user=self.member)
        response = self.client.post(reverse("password_reset_review", args=[reset.pk]), {"new_password1": "1", "new_password2": "1"})
        self.assertRedirects(response, reverse("users"))
        self.member.refresh_from_db(); reset.refresh_from_db()
        self.assertTrue(self.member.check_password("1")); self.assertNotEqual(self.member.password, "1")
        self.assertEqual(reset.status, PasswordResetRequest.Status.COMPLETED)
        self.assertEqual(reset.reviewed_by, self.admin)
        self.assertTrue(AuditLog.objects.filter(entity_id=self.member.pk, action="Password reset completed", actor=self.admin).exists())

    def test_admin_reset_mismatch_or_blank_is_rejected_without_completing_request(self):
        self.client.force_login(self.admin)
        reset = PasswordResetRequest.objects.create(user=self.member)
        for first, second in [("", ""), ("1", "2")]:
            response = self.client.post(reverse("password_reset_review", args=[reset.pk]), {"new_password1": first, "new_password2": second})
            self.assertEqual(response.status_code, 200); self.assertTrue(response.context["form"].errors)
        reset.refresh_from_db(); self.member.refresh_from_db()
        self.assertEqual(reset.status, PasswordResetRequest.Status.PENDING)
        self.assertTrue(self.member.check_password("old-member-password"))

    def test_reset_and_user_creation_permissions_are_unchanged(self):
        reset = PasswordResetRequest.objects.create(user=self.member)
        endpoint = reverse("password_reset_review", args=[reset.pk])
        self.assertEqual(self.client.post(endpoint, {"new_password1": "1", "new_password2": "1"}).status_code, 302)
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(endpoint, {"new_password1": "1", "new_password2": "1"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("user_add"), self.creation_data()).status_code, 403)
        self.assertEqual(self.client.post(reverse("user_edit", args=[self.admin.pk]), {"new_password": "1"}).status_code, 403)
        self.member.refresh_from_db(); self.assertTrue(self.member.check_password("old-member-password"))

    def test_forgot_password_still_creates_admin_request_not_an_automatic_reset(self):
        response = self.client.post(reverse("forgot_password"), {"username": self.member.username})
        self.assertRedirects(response, reverse("forgot_password_done"))
        self.assertTrue(PasswordResetRequest.objects.filter(user=self.member, status=PasswordResetRequest.Status.PENDING).exists())
        self.member.refresh_from_db(); self.assertTrue(self.member.check_password("old-member-password"))

    def test_legacy_registration_check_does_not_reintroduce_strength_rules(self):
        for password in ["1", "password", "employee"]:
            response = self.client.post(reverse("registration_password_check"), json.dumps({"password": password, "username": "employee"}), content_type="application/json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), dict.fromkeys(["similarity", "length", "common", "numeric"], True))
