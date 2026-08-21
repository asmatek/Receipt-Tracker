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

create table if not exists public.expenses (
  id uuid primary key default gen_random_uuid(),
  vendor text,
  expense_date date,
  subtotal numeric(14,2),
  tax numeric(14,2),
  tip numeric(14,2),
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
  notes text,
  items jsonb not null default '[]'::jsonb,
  raw_text text,
  receipt_path text,
  receipt_name text,
  receipt_mime text,
  receipt_sha256 text,
  duplicate_key text,
  possible_duplicate boolean not null default false,
  duplicate_of uuid references public.expenses(id) on delete set null,
  missing_fields text[] not null default '{}',
  status text not null default 'complete' check (status in ('complete', 'needs_review')),
  created_by uuid,
  created_by_email text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists expenses_date_idx on public.expenses(expense_date desc);
create index if not exists expenses_business_idx on public.expenses(business_id);
create index if not exists expenses_category_idx on public.expenses(category);
create index if not exists expenses_duplicate_idx on public.expenses(duplicate_key);
create index if not exists expenses_receipt_hash_idx on public.expenses(receipt_sha256);

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

-- The app uses the server-side service role after authenticating and allow-listing
-- each user. RLS blocks direct browser access through the public anon key.
alter table public.allowed_users enable row level security;
alter table public.businesses enable row level security;
alter table public.categories enable row level security;
alter table public.expenses enable row level security;
alter table public.audit_log enable row level security;

-- Create the private receipt bucket if it does not exist.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'receipts', 'receipts', false, 26214400,
  array['image/png','image/jpeg','image/tiff','image/bmp','image/webp','application/pdf']
)
on conflict (id) do update set
  public = false,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

-- No public storage policies are intentionally created. The Streamlit server
-- accesses this private bucket using its protected service-role secret.
