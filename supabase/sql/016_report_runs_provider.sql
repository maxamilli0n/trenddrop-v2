alter table public.report_runs
add column if not exists provider text not null default 'ebay';

alter table public.products
add column if not exists signals integer;

alter table public.products
add column if not exists top_rated boolean default false;

create or replace view public.v_products_clean as
select
    p.id,
    p.provider,
    p.source,
    p.title,
    p.price,
    p.currency,
    p.seller_feedback,
    p.signals,
    p.top_rated,
    p.image_url,
    p.url,
    p.inserted_at
from public.products p
where p.title is not null
  and p.price is not null;

create or replace view public.v_report_status as
select
  r.run_started_at,
  r.provider,
  r.data_window_label,
  r.products_total,
  r.curated_count,
  r.success,
  r.error_message,
  r.pdf_url,
  r.csv_url
from public.report_runs r
order by r.run_started_at desc
limit 20;

notify pgrst, 'reload schema';

