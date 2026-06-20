{#
  Deterministic surrogate key from one or more columns.

  A local reimplementation of dbt_utils.generate_surrogate_key so the project has
  NO package dependencies — it stays installable and `dbt parse`-able fully offline
  (CLAUDE.md golden rule #4). The contract must match on both sides of a join: a dim
  computes its PK with surrogate_key([...]) and the fact computes the FK with the
  SAME column list, so identical natural keys hash to identical surrogates.

  NULLs are coalesced to a sentinel so that (a, NULL) and (a, '') don't collide and
  a null component never nulls the whole key.
#}
{% macro surrogate_key(field_list) %}
    md5(
        {%- for field in field_list %}
        coalesce(cast({{ field }} as varchar), '∅')
        {%- if not loop.last %} || '|' || {% endif %}
        {%- endfor %}
    )
{% endmacro %}
