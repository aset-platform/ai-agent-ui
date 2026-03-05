"""Unit tests for auth.password — hash, verify, and strength validation."""

import pytest


class TestHashAndVerifyPassword:
    """Tests for :func:`auth.password.hash_password` and
    :func:`auth.password.verify_password`."""

    def test_hash_returns_string(self):
        from auth.password import hash_password

        result = hash_password("Secret1!")
        assert isinstance(result, str)
        assert len(result) > 20

    def test_hash_is_not_plaintext(self):
        from auth.password import hash_password

        hashed = hash_password("Secret1!")
        assert "Secret1!" not in hashed

    def test_verify_correct_password(self):
        from auth.password import hash_password, verify_password

        hashed = hash_password("GoodPass1!")
        assert verify_password("GoodPass1!", hashed) is True

    def test_verify_wrong_password(self):
        from auth.password import hash_password, verify_password

        hashed = hash_password("GoodPass1!")
        assert verify_password("WrongPass1!", hashed) is False

    def test_two_hashes_of_same_password_differ(self):
        """bcrypt uses a random salt — identical passwords yield different hashes."""
        from auth.password import hash_password

        h1 = hash_password("Same1Pass!")
        h2 = hash_password("Same1Pass!")
        assert h1 != h2

    def test_verify_both_hashes_accept_correct_password(self):
        from auth.password import hash_password, verify_password

        password = "Same1Pass!"
        h1 = hash_password(password)
        h2 = hash_password(password)
        assert verify_password(password, h1) is True
        assert verify_password(password, h2) is True


class TestValidatePasswordStrength:
    """Tests for :func:`auth.password.validate_password_strength`."""

    def test_strong_password_passes(self):
        from auth.password import validate_password_strength

        # Should not raise
        validate_password_strength("Secure123!")

    def test_too_short_raises(self):
        from fastapi import HTTPException

        from auth.password import validate_password_strength

        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("Sh0rt")
        assert exc_info.value.status_code == 400

    def test_no_digit_raises(self):
        from fastapi import HTTPException

        from auth.password import validate_password_strength

        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("NoDigitHere!")
        assert exc_info.value.status_code == 400

    def test_minimum_length_with_digit_passes(self):
        from auth.password import validate_password_strength

        # Meets all requirements: 8 chars, digit, upper, special
        validate_password_strength("Pass123!")

    def test_no_uppercase_raises(self):
        """Passwords without an uppercase letter must be rejected."""
        from fastapi import HTTPException

        from auth.password import validate_password_strength

        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("nouppr1!")
        assert exc_info.value.status_code == 400

    def test_no_special_char_raises(self):
        """Passwords without a special character must be rejected."""
        from fastapi import HTTPException

        from auth.password import validate_password_strength

        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("NoSpecial1")
        assert exc_info.value.status_code == 400

    def test_empty_string_raises(self):
        from fastapi import HTTPException

        from auth.password import validate_password_strength

        with pytest.raises(HTTPException):
            validate_password_strength("")
