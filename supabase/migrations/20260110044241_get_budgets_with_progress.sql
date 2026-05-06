-- migrations/get_budgets_with_progress.sql
create or replace function get_budgets_with_progress()
returns table (
  id uuid,
  category_id uuid,
  amount numeric,
  category_name text,
  category_type text,
  category_color text,
  category_icon text,
  progress numeric
)
language sql
stable
as $$
  select 
    b.id,
    b.category_id,
    b.amount,
    c.name,
    c.type,
    c.color,
    c.icon,
    coalesce(sum(t.amount) filter (
      where t.type = 'expense' 
      and t.transaction_date >= date_trunc('month', current_date)
    ), 0) as progress
  from budgets b
  join categories c on c.id = b.category_id
  left join transactions t on t.category_id = b.category_id
  group by b.id, c.id;
$$;