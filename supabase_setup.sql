-- Receipt Tracker database setup
-- Run this entire file once in Supabase: SQL Editor > New query > Run.

create extension if not exists pgcrypto;

create table if not exists public.allowed_users (
  email text primary key check (email = lower(email)),
  full_name text,
  role text not null default 'member' check (role in ('admin', 'member')),
  active boolean not null default true,
  invited_by text,
  invited_at timestamptz not null default now(),
  last_login_at timestamptz
);

create table if not exists public.businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  active boolean not null default true,
  created_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.categories (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.merchants (
  id uuid primary key default gen_random_uuid(),
  canonical_name text not null,
  normalized_name text not null unique,
  aliases text[] not null default '{}',
  active boolean not null default true,
  created_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.expenses (
  id uuid primary key default gen_random_uuid(),
  vendor text,
  merchant_id uuid references public.merchants(id) on delete set null,
  expense_date date,
  transaction_time text,
  subtotal numeric(14,2),
  tax numeric(14,2),
  tip numeric(14,2),
  fees numeric(14,2),
  discount numeric(14,2),
  total numeric(14,2) not null default 0,
  currency text not null default 'USD',
  usd_total numeric(14,2),
  exchange_rate numeric(18,6),
  category text,
  business_id uuid references public.businesses(id) on delete restrict,
  business_purpose text,
  client_name text,
  project_name text,
  payment_method text,
  card_last4 text,
  receipt_number text,
  location text,
  email_from text,
  email_subject text,
  email_received_at text,
  document_kind text not null default 'receipt',
  tags text[] not null default '{}',
  department text,
  attendees text[] not null default '{}',
  mileage_miles numeric(12,2) not null default 0,
  is_personal boolean not null default false,
  reimbursable boolean not null default true,
  notes text,
  items jsonb not null default '[]'::jsonb,
  confidence jsonb not null default '{}'::jsonb,
  field_sources jsonb not null default '{}'::jsonb,
  raw_text text,
  receipt_path text,
  receipt_name text,
  receipt_mime text,
  receipt_sha256 text,
  text_sha256 text,
  perceptual_hash text,
  duplicate_key text,
  possible_duplicate boolean not null default false,
  duplicate_of uuid references public.expenses(id) on delete set null,
  duplicate_candidate_id uuid references public.expenses(id) on delete set null,
  duplicate_reasons text[] not null default '{}',
  duplicate_score numeric(6,3) not null default 0,
  missing_fields text[] not null default '{}',
  status text not null default 'complete' check (status in ('complete', 'needs_review')),
  review_status text not null default 'needs_review' check (review_status in ('needs_review', 'approved', 'rejected')),
  reimbursement_status text not null default 'not_reimbursed' check (reimbursement_status in ('not_reimbursed', 'in_batch', 'reimbursed')),
  approved_at timestamptz,
  submitted_at timestamptz,
  reimbursed_at timestamptz,
  deleted_at timestamptz,
  created_by uuid,
  created_by_email text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.expenses
  add column if not exists merchant_id uuid references public.merchants(id) on delete set null;

alter table public.expenses add column if not exists transaction_time text;
alter table public.expenses add column if not exists confidence jsonb not null default '{}'::jsonb;
alter table public.expenses add column if not exists field_sources jsonb not null default '{}'::jsonb;
alter table public.expenses add column if not exists fees numeric(14,2);
alter table public.expenses add column if not exists card_last4 text;
alter table public.expenses add column if not exists receipt_number text;
alter table public.expenses add column if not exists location text;
alter table public.expenses add column if not exists email_from text;
alter table public.expenses add column if not exists email_subject text;
alter table public.expenses add column if not exists email_received_at text;
alter table public.expenses add column if not exists document_kind text not null default 'receipt';
alter table public.expenses add column if not exists tags text[] not null default '{}';
alter table public.expenses add column if not exists department text;
alter table public.expenses add column if not exists attendees text[] not null default '{}';
alter table public.expenses add column if not exists mileage_miles numeric(12,2) not null default 0;
alter table public.expenses add column if not exists is_personal boolean not null default false;
alter table public.expenses add column if not exists reimbursable boolean not null default true;
alter table public.expenses add column if not exists text_sha256 text;
alter table public.expenses add column if not exists perceptual_hash text;
alter table public.expenses add column if not exists duplicate_reasons text[] not null default '{}';
alter table public.expenses add column if not exists duplicate_candidate_id uuid references public.expenses(id) on delete set null;
alter table public.expenses add column if not exists duplicate_score numeric(6,3) not null default 0;
alter table public.expenses add column if not exists review_status text not null default 'needs_review';
alter table public.expenses add column if not exists reimbursement_status text not null default 'not_reimbursed';
alter table public.expenses add column if not exists approved_at timestamptz;
alter table public.expenses add column if not exists submitted_at timestamptz;
alter table public.expenses add column if not exists reimbursed_at timestamptz;
alter table public.expenses add column if not exists deleted_at timestamptz;

-- Create merchant records for expenses saved before the merchant upgrade.
insert into public.merchants (canonical_name, normalized_name, aliases, created_by)
select
  min(trim(vendor)),
  regexp_replace(
    regexp_replace(lower(trim(vendor)), '\y(incorporated|corporation|company|limited|inc|corp|co|ltd|llc)\y', '', 'g'),
    '[^a-z0-9]', '', 'g'
  ),
  array_agg(distinct trim(vendor)),
  'migration'
from public.expenses
where vendor is not null and trim(vendor) <> ''
group by regexp_replace(
  regexp_replace(lower(trim(vendor)), '\y(incorporated|corporation|company|limited|inc|corp|co|ltd|llc)\y', '', 'g'),
  '[^a-z0-9]', '', 'g'
)
on conflict (normalized_name) do nothing;

update public.expenses e
set merchant_id = m.id
from public.merchants m
where e.merchant_id is null
  and m.normalized_name = regexp_replace(
    regexp_replace(lower(trim(e.vendor)), '\y(incorporated|corporation|company|limited|inc|corp|co|ltd|llc)\y', '', 'g'),
    '[^a-z0-9]', '', 'g'
  );

create index if not exists expenses_date_idx on public.expenses(expense_date desc);
create index if not exists expenses_business_idx on public.expenses(business_id);
create index if not exists expenses_category_idx on public.expenses(category);
create index if not exists expenses_duplicate_idx on public.expenses(duplicate_key);
create index if not exists expenses_receipt_hash_idx on public.expenses(receipt_sha256);
create index if not exists expenses_merchant_idx on public.expenses(merchant_id);
create index if not exists expenses_review_idx on public.expenses(review_status);
create index if not exists expenses_reimbursement_idx on public.expenses(reimbursement_status);
create index if not exists expenses_receipt_number_idx on public.expenses(receipt_number);
create index if not exists expenses_text_hash_idx on public.expenses(text_sha256);

create table if not exists public.merchant_rules (
  id uuid primary key default gen_random_uuid(),
  match_text text not null unique,
  category text,
  project text,
  tags text[] not null default '{}',
  active boolean not null default true,
  created_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.reimbursement_batches (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  notes text,
  from_date date,
  to_date date,
  status text not null default 'draft' check (status in ('draft', 'submitted', 'paid', 'cancelled')),
  created_by uuid,
  created_by_email text,
  created_at timestamptz not null default now(),
  submitted_at timestamptz,
  paid_at timestamptz,
  cancelled_at timestamptz
);

create table if not exists public.reimbursement_batch_expenses (
  batch_id uuid not null references public.reimbursement_batches(id) on delete cascade,
  expense_id uuid not null references public.expenses(id) on delete restrict,
  added_at timestamptz not null default now(),
  primary key (batch_id, expense_id)
);

create index if not exists reimbursement_batch_expense_idx on public.reimbursement_batch_expenses(expense_id);

create table if not exists public.duplicate_decisions (
  id uuid primary key default gen_random_uuid(),
  expense_id uuid not null references public.expenses(id) on delete cascade,
  matched_expense_id uuid not null references public.expenses(id) on delete cascade,
  decision text not null check (decision in ('keep_both', 'marked_duplicate')),
  reasons text[] not null default '{}',
  actor_email text,
  decided_at timestamptz not null default now(),
  unique(expense_id, matched_expense_id)
);

create table if not exists public.audit_log (
  id bigint generated always as identity primary key,
  expense_id uuid,
  action text not null check (action in ('create', 'update', 'delete', 'restore')),
  actor_id uuid,
  actor_email text,
  before_data jsonb,
  after_data jsonb,
  created_at timestamptz not null default now()
);

insert into public.categories(name) values
  ('Advertising'), ('Bank Fees'), ('Contract Labor'), ('Education'),
  ('Equipment'), ('Fuel'), ('Insurance'), ('Meals'), ('Office Supplies'),
  ('Professional Services'), ('Rent'), ('Repairs & Maintenance'),
  ('Software'), ('Travel'), ('Utilities'), ('Vehicle'), ('Other')
on conflict (name) do nothing;

insert into public.categories(name) values
  ('Advertising & Marketing'), ('Airfare'), ('Education & Training'), ('Entertainment'),
  ('Hotels & Lodging'), ('Medical'), ('Parking & Tolls'), ('Restaurants & Meals'),
  ('Refunds & Credits'), ('Shipping & Postage'), ('Software & Subscriptions'),
  ('Taxes & Government Fees'), ('Transportation')
on conflict (name) do nothing;

-- The app uses the server-side service role after authenticating and allow-listing
-- each user. RLS blocks direct browser access through the public anon key.
alter table public.allowed_users enable row level security;
alter table public.businesses enable row level security;
alter table public.categories enable row level security;
alter table public.merchants enable row level security;
alter table public.expenses enable row level security;
alter table public.audit_log enable row level security;
alter table public.merchant_rules enable row level security;
alter table public.reimbursement_batches enable row level security;
alter table public.reimbursement_batch_expenses enable row level security;
alter table public.duplicate_decisions enable row level security;

-- Create the private receipt bucket if it does not exist.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'receipts', 'receipts', false, 26214400,
  array['image/png','image/jpeg','image/tiff','image/bmp','image/webp','application/pdf',
        'message/rfc822','text/plain','text/csv','text/html','text/markdown']
)
on conflict (id) do update set
  public = false,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

-- No public storage policies are intentionally created. The Streamlit server
-- accesses this private bucket using its protected service-role secret.
