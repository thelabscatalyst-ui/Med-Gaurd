"""TenantContext: the only object that can describe "who is asking". No database needed."""

import pytest

from app.db.tenant import TenantContext


def test_settings_for_a_clinic_owner():
    ctx = TenantContext(doctor_id=10, clinic_id=1, is_clinic_admin=True, can_read_finance=True)
    assert ctx.as_settings() == {
        "app.doctor_id": "10",
        "app.clinic_id": "1",
        "app.is_clinic_admin": "true",
        "app.can_read_finance": "true",
    }


def test_solo_doctor_is_explicit_never_empty():
    # An empty string would look the same as "forgot to set it"; 'solo' can't be confused.
    assert TenantContext(doctor_id=30, clinic_id=None).as_settings()["app.clinic_id"] == "solo"


def test_defaults_grant_nothing_extra():
    s = TenantContext(doctor_id=11, clinic_id=1).as_settings()
    assert s["app.is_clinic_admin"] == "false"
    assert s["app.can_read_finance"] == "false"


def test_solo_doctor_cannot_be_clinic_admin():
    with pytest.raises(ValueError):
        TenantContext(doctor_id=30, clinic_id=None, is_clinic_admin=True)


@pytest.mark.parametrize("bad", [0, -5])
def test_ids_must_be_positive(bad):
    with pytest.raises(ValueError):
        TenantContext(doctor_id=bad, clinic_id=1)
    with pytest.raises(ValueError):
        TenantContext(doctor_id=1, clinic_id=bad)


@pytest.mark.parametrize("bad", [True, "10", 10.0])
def test_ids_must_be_real_ints(bad):
    # bool is a subclass of int in Python: doctor_id=True must not become doctor 1.
    with pytest.raises(TypeError):
        TenantContext(doctor_id=bad, clinic_id=1)


def test_context_is_immutable():
    ctx = TenantContext(doctor_id=11, clinic_id=1)
    with pytest.raises(AttributeError):
        ctx.is_clinic_admin = True  # type: ignore[misc]
