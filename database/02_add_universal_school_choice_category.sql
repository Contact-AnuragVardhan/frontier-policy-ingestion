-- AI Choice Map category migration: add "Universal School Choice".
-- REVIEW BEFORE APPLYING. This only updates CHECK constraints; it does not insert policy data.

alter table public.policies
  drop constraint if exists policies_category_check;

alter table public.policies
  add constraint policies_category_check
  check (category in (
    'AI Use',
    'Student Privacy',
    'Parental Consent',
    'AI Literacy',
    'School Procurement',
    'Universal School Choice'
  ));

alter table public.policies
  drop constraint if exists policies_categories_values_check;

alter table public.policies
  add constraint policies_categories_values_check
  check (
    categories <@ array[
      'AI Use',
      'Student Privacy',
      'Parental Consent',
      'AI Literacy',
      'School Procurement',
      'Universal School Choice'
    ]::text[]
  );
