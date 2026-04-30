-- Fix: digest() lives in the `extensions` schema (Supabase default), but
-- delete_user_data sets `search_path = public, pg_temp` for SECURITY DEFINER
-- hardening, so the call resolves nowhere and fails with 42883.
-- Qualify the call explicitly rather than widening the search path.

CREATE OR REPLACE FUNCTION public.delete_user_data(p_clerk_user_id text)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    -- FK-respecting deletion order. DELETE on a non-existent user is a no-op.
    delete from transactions where user_id = p_clerk_user_id;
    delete from debt_payments where user_id = p_clerk_user_id;
    -- allocations has no user_id; scope via budget_id.
    delete from allocations
        where budget_id in (
            select id from budgets where user_id = p_clerk_user_id
        );
    delete from budget_archive_reports where user_id = p_clerk_user_id;
    delete from recurring_transactions where user_id = p_clerk_user_id;
    delete from debts where user_id = p_clerk_user_id;
    delete from goals where user_id = p_clerk_user_id;
    delete from budgets where user_id = p_clerk_user_id;
    delete from accounts where user_id = p_clerk_user_id;
    delete from user_settings where user_id = p_clerk_user_id;
    delete from profiles where clerk_user_id = p_clerk_user_id;

    update public.account_deletion_requests
        set status = 'completed',
            completed_at = now()
        where user_id = p_clerk_user_id
        and status in ('clerk_called', 'scheduled', 'processing');

    insert into public.account_deletion_audit (user_id_hash, event, metadata)
        values (
            extensions.digest(p_clerk_user_id, 'sha256'),
            'user_data_deleted',
            jsonb_build_object('called_at', now())
        );
end;
$$;

revoke all on function public.delete_user_data(text) from public, anon, authenticated;
grant execute on function public.delete_user_data(text) to service_role;
