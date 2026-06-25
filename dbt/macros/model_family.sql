{#
  Roll a model identity up to its family by stripping a dated snapshot suffix
  (`-YYYYMMDD`): e.g. `claude-haiku-4-5-20251001` -> `claude-haiku-4-5`. Date-less
  names (Ollama tags like `qwen2.5:3b-instruct`) pass through unchanged.

  The single home for model-name normalization (ADR-003): call it wherever a model
  string lands (`generator`, `writer`, ...) so the same processing is the same code.
  Takes a SQL expression so callers can normalize a coalesced identity in one pass,
  e.g. {{ model_family("coalesce(generator_model_resolved, generator_model)") }}.
#}
{% macro model_family(expr) %}
    regexp_replace({{ expr }}, '-\d{8}$', '')
{% endmacro %}
