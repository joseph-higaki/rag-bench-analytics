{#
  Provision the dashboard's least-privilege read-only role on the marts schema.

  Golden rule #2 / ADR-001 #5: Streamlit reads marts via a read-only role, never the
  analytics owner. Run as an on-run-end hook so it is idempotent and RE-APPLIED every
  build — marts are `table`-materialized (dropped + recreated), so `grant select on all
  tables` must re-run each time or the new tables aren't readable. Schema-guarded so a
  partial run (e.g. seed-only, marts absent) is a no-op instead of an error. Password
  comes from env (golden rule #5); the committed default is local-only, mirroring the
  analytics/analytics posture in docker-compose.
#}
{% macro grant_marts_reader() %}
  {%- if target.type != 'postgres' or not execute -%}
    {%- do return(none) -%}
  {%- endif -%}

  {%- set reader = env_var('MARTS_READER_USER', 'marts_reader') -%}
  {%- set password = env_var('MARTS_READER_PASSWORD', 'marts_reader') -%}
  {%- set marts_schema = target.schema ~ '_marts' -%}

  {%- set found = run_query(
        "select 1 from information_schema.schemata where schema_name = '" ~ marts_schema ~ "'") -%}
  {%- if found | length == 0 -%}
    {%- do log("grant_marts_reader: schema " ~ marts_schema ~ " absent — skipping", info=true) -%}
    {%- do return(none) -%}
  {%- endif -%}

  {%- set create_role -%}
    do $$ begin
      if not exists (select 1 from pg_roles where rolname = '{{ reader }}') then
        create role {{ reader }} login password '{{ password }}';
      end if;
    end $$
  {%- endset -%}
  {%- do run_query(create_role) -%}
  {%- do run_query('grant connect on database "' ~ target.dbname ~ '" to ' ~ reader) -%}
  {%- do run_query('grant usage on schema "' ~ marts_schema ~ '" to ' ~ reader) -%}
  {%- do run_query('grant select on all tables in schema "' ~ marts_schema ~ '" to ' ~ reader) -%}
  {%- do run_query('alter default privileges in schema "' ~ marts_schema ~ '" grant select on tables to ' ~ reader) -%}
  {%- do log("granted read-only marts access to " ~ reader ~ " on " ~ marts_schema, info=true) -%}
{% endmacro %}
