"""Constrain the audit-chain helper to the caller's own clinic (ADR-013, F-05).

``enamel_audit_prev_hash`` is a ``SECURITY DEFINER`` helper owned by the
migration runner (the owner): it reads the head of an audit chain so that a
writer that may **not** read the audit table (the patient portal) can still
append to the chain. It accepted an arbitrary clinic id, so any role that
could execute it could ask for the chain head of another clinic.

The helper now refuses a chain that is neither

* the clinic of the caller's own context (``app.current_clinic_id``), nor
* the platform chain (``NULL``), which only a staff principal may read, nor
* a chain of a caller that holds the maintenance bypass (the owner and
  ``enamel_maint``), which is the platform path by design.

The patient roles (``enamel_portal`` and ``enamel_portal_auth``) are held to
the stricter rule: they may read the chain of the clinic they are bound to
and nothing else - not the platform chain, not another clinic. The
``role`` GUC is read inside the function, so the rule follows the role the
request actually runs under (``SET LOCAL ROLE``), not the login role.

The execution privilege stays with the roles that legitimately append to a
chain (the staff runtime, the worker, the two patient roles and the
maintenance role); it is revoked from ``PUBLIC``. It cannot be revoked from
the patient roles: the portal writes audit events
(``apps/portal/views.py``) through ``apps.core.audit.record``, which reads the
chain head through this helper, and the portal may not read the audit table
itself. What is closed is the **cross-clinic read**, not the privilege.

The reverse restores the previous (unconstrained) definition.
"""

from django.db import migrations

PREVIOUS_DEFINITION = """
CREATE OR REPLACE FUNCTION enamel_audit_prev_hash(p_clinic_id uuid)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_previous text;
    v_bypass text;
BEGIN
    v_bypass := current_setting('app.bypass_rls', true);
    PERFORM set_config('app.bypass_rls', 'on', true);
    SELECT ae.hash INTO v_previous
      FROM public.core_auditevent ae
     WHERE ae.clinic_id IS NOT DISTINCT FROM p_clinic_id
     ORDER BY ae.id DESC
     LIMIT 1;
    PERFORM set_config('app.bypass_rls', COALESCE(v_bypass, ''), true);
    RETURN COALESCE(v_previous, '');
END;
$$;
"""

CONSTRAINED_DEFINITION = """
CREATE OR REPLACE FUNCTION enamel_audit_prev_hash(p_clinic_id uuid)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_previous text;
    v_bypass text;
    v_caller uuid;
    v_role text;
BEGIN
    v_bypass := current_setting('app.bypass_rls', true);
    v_caller := nullif(current_setting('app.current_clinic_id', true), '')::uuid;
    v_role := coalesce(current_setting('role', true), '');

    -- A patient principal may read the chain of the clinic it is bound to and
    -- nothing else - neither another clinic nor the platform chain.
    IF v_role IN ('enamel_portal', 'enamel_portal_auth') THEN
        IF p_clinic_id IS NULL OR p_clinic_id IS DISTINCT FROM v_caller THEN
            RAISE EXCEPTION
                'enamel_audit_prev_hash: a patient principal may only read the audit chain of its own clinic'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSIF v_bypass IS DISTINCT FROM 'on'
       AND p_clinic_id IS NOT NULL
       AND p_clinic_id IS DISTINCT FROM v_caller THEN
        RAISE EXCEPTION
            'enamel_audit_prev_hash: the caller may only read the audit chain of its own clinic'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    PERFORM set_config('app.bypass_rls', 'on', true);
    SELECT ae.hash INTO v_previous
      FROM public.core_auditevent ae
     WHERE ae.clinic_id IS NOT DISTINCT FROM p_clinic_id
     ORDER BY ae.id DESC
     LIMIT 1;
    PERFORM set_config('app.bypass_rls', COALESCE(v_bypass, ''), true);
    RETURN COALESCE(v_previous, '');
END;
$$;

REVOKE ALL ON FUNCTION enamel_audit_prev_hash(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION enamel_audit_prev_hash(uuid)
    TO enamel_app, enamel_worker, enamel_portal, enamel_portal_auth, enamel_maint;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_public_booking_catalogue"),
    ]

    operations = [
        migrations.RunSQL(
            CONSTRAINED_DEFINITION,
            reverse_sql=PREVIOUS_DEFINITION,
            state_operations=[],
        ),
    ]
